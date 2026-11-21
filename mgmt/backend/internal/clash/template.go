// Package clash 黄金模板渲染（任务书 §3.2）。
// 只允许动态替换：节点列表、节点组成员、节点直连地址、管理面直连地址。
// Session 1 节点 host 仅支持 IPv4 字面量；域名/IPv6 返回明确校验错误。
package clash

import (
	"embed"
	"errors"
	"fmt"
	"net"
	"regexp"
	"strings"

	"routermgmt/internal/crypto"
)

//go:embed golden.yaml
var goldenFS embed.FS

const markerNodeProxies = "#{{NODE_PROXIES}}"
const markerGroupProxy = "#{{GROUP_PROXY_MEMBERS}}"
const markerGroupAuto = "#{{GROUP_AUTO_MEMBERS}}"
const markerNodeDirect = "#{{NODE_DIRECT_RULES}}"
const markerMediaMembers = "#{{MEDIA_MEMBERS}}"
const markerProxyProviders = "#{{PROXY_PROVIDERS}}"
const markerAirportGroup = "#{{AIRPORT_GROUP}}"
const markerAutoMembers = "#{{AUTO_MEMBERS}}"

var reNodeName = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$`)

// hysteria2 密码：限定字母数字与 -_（16~64 位），保证可安全嵌入 YAML 裸标量
var reHy2Password = regexp.MustCompile(`^[A-Za-z0-9_-]{16,64}$`)

// 支持的节点协议（nodes.Proto 存量数据为空串，按 vless-reality 处理）
const (
	ProtoVLESS   = "vless-reality"
	ProtoVLESSWS = "vless-ws" // 2026-10-30：Cloudflare CDN 中转（WS+TLS，host=CF 边缘 IPv4，SNI/Host=域名）
	ProtoHy2     = "hysteria2"
)

// ValidHy2Password hysteria2 密码校验（节点 API 与渲染共用同一规则）。
func ValidHy2Password(pw string) bool { return reHy2Password.MatchString(pw) }

// NodeRender 参与渲染的节点（已从 AES-GCM 信封解出参数）。
type NodeRender struct {
	ID       int64
	Name     string
	Purpose  string // ordinary|video：video 只入 US-MEDIA，不进 PROXY/AUTO/NODE-PICK
	Proto    string // vless-reality | vless-ws | hysteria2（空串= vless-reality）
	Host     string
	Port     int
	UUID     string
	PBK      string
	SNI      string
	SID      string
	Flow     string
	FP       string
	Password string // hysteria2
	WSPath   string // vless-ws：WebSocket 路径
	WSHost   string // vless-ws：Host 头（空=SNI）
}

// RenderInput 渲染输入。
type RenderInput struct {
	DeviceID      string
	Nodes         []NodeRender // 按优先级排序，第一个为 primary
	MgmtDirectIPs []string     // 管理面 WG endpoint 等需直连的 IPv4
	// 机场订阅兜底（2026-10-31 第4层）：AirportSubURL 非空时渲染 proxy-providers
	// 与 AIRPORT(url-test) 组；AirportGeneration 为缓存代次（换机场时递增，g01→g02）
	AirportSubURL     string
	AirportGeneration string
}

// ValidateNodeName 校验节点名可安全嵌入 YAML/任务参数。
func ValidateNodeName(name string) bool { return reNodeName.MatchString(name) }

// validateHost Session 1 仅支持 IPv4 字面量（域名/IPv6 在实现解析与直连规则前拒绝）。
func validateHost(host string) error {
	ip := net.ParseIP(host)
	if ip == nil || ip.To4() == nil || strings.Contains(host, ":") {
		return fmt.Errorf("节点 host %q 不是 IPv4 字面量：Session 1 仅支持 IPv4，域名/IPv6 需先实现解析与对应直连规则", host)
	}
	return nil
}

// Render 生成设备订阅 YAML（原始字节，含凭据；随后必须签名，禁止改动）。
func Render(in RenderInput) ([]byte, error) {
	if len(in.Nodes) == 0 {
		return nil, errors.New("没有可用的绑定节点，拒绝生成配置")
	}
	var proxies, groupProxy, groupAuto, directRules, mediaMembers []string
	for _, n := range in.Nodes {
		if !ValidateNodeName(n.Name) {
			return nil, fmt.Errorf("节点名 %q 不合法（仅允许字母数字与 - _，长度 2~32）", n.Name)
		}
		purpose := n.Purpose
		if purpose == "" {
			purpose = "ordinary" // 存量行兼容：空=ordinary；未知值必须显式拒绝（B15）
		}
		if purpose != "ordinary" && purpose != "video" {
			return nil, fmt.Errorf("节点 %s purpose %q 非法（仅 ordinary/video），拒绝渲染", n.Name, purpose)
		}
		if err := validateHost(n.Host); err != nil {
			return nil, err
		}
		if n.Port < 1 || n.Port > 65535 {
			return nil, fmt.Errorf("节点 %s 端口不合法", n.Name)
		}
		switch n.Proto {
		case "", ProtoVLESS:
			if err := renderVLESS(&n, &proxies); err != nil {
				return nil, err
			}
		case ProtoVLESSWS:
			if err := renderVLESSWS(&n, &proxies); err != nil {
				return nil, err
			}
		case ProtoHy2:
			if !ValidHy2Password(n.Password) {
				return nil, fmt.Errorf("节点 %s hysteria2 密码不合法（需 16~64 位字母数字/-_）", n.Name)
			}
			// 服务端自签证书（内部管理面使用），客户端跳过证书校验，接入靠密码鉴权
			proxies = append(proxies, fmt.Sprintf(`  - name: "%s"
    type: hysteria2
    server: %s
    port: %d
    password: %s
    udp: true
    skip-cert-verify: true`, n.Name, n.Host, n.Port, n.Password))
		default:
			return nil, fmt.Errorf("节点 %s 协议 %q 不支持（当前仅 vless-reality / vless-ws / hysteria2）", n.Name, n.Proto)
		}
		// 用途隔离（2026-11-17 v37 修订 B13/B14）：video/bulk 类节点只进 US-MEDIA
		// （受控媒体路径），不进普通 PROXY/AUTO/NODE-PICK（含 select 手选——普通组
		// 不得残留受配额治理通道的隐藏入口；诊断切换走平台 purpose 调整）。
		if purpose == "video" {
			mediaMembers = append(mediaMembers, fmt.Sprintf("      - %s", n.Name))
			// B14：节点自身公网 IP /32 直连保护对全部用途生效（video 分支此前被
			// continue 跳过，导致 BULK 节点 IP 直连保护缺失）。
			directRules = append(directRules, fmt.Sprintf("  - IP-CIDR,%s/32,DIRECT,no-resolve", n.Host))
			continue // 普通组不收 video 类
		}
		groupProxy = append(groupProxy, fmt.Sprintf("      - %s", n.Name))
		groupAuto = append(groupAuto, fmt.Sprintf("      - %s", n.Name))
		// 节点自身公网 IP 直连：不做这条，TUN 会把 SSH 绕回节点（§3.1）
		directRules = append(directRules, fmt.Sprintf("  - IP-CIDR,%s/32,DIRECT,no-resolve", n.Host))
	}
	// 管理面 WG endpoint 直连，避免 WG 套入代理（§3.2 末段）
	for _, ip := range in.MgmtDirectIPs {
		if err := validateHost(ip); err != nil {
			return nil, fmt.Errorf("管理面直连地址 %q 无效: %w", ip, err)
		}
		rule := fmt.Sprintf("  - IP-CIDR,%s/32,DIRECT,no-resolve", ip)
		if !containsStr(directRules, rule) {
			directRules = append(directRules, rule)
		}
	}

	// include-all does not include other groups. Always append an explicit fallback.
	mediaMembers = append(mediaMembers, "      - NODE-PICK")

	// 机场订阅兜底（第4层）：URL 非空时注入 provider/组/链位
	airport := strings.TrimSpace(in.AirportSubURL) != ""
	providersBlock := []string{""}
	airportGroup := []string{}
	autoMembers := []string{"      - NODE-PICK"}
	if airport {
		gen := in.AirportGeneration
		if gen == "" {
			gen = "g01"
		}
		subURL := strings.NewReplacer(`\`, `\\`, `"`, `\"`).Replace(in.AirportSubURL)
		providersBlock = strings.Split(fmt.Sprintf(`proxy-providers:
  airport-sub:
    type: http
    url: "%s"
    path: ./proxy_providers/airport-%s.yaml
    interval: 21600
    proxy: NODE-PICK
    health-check:
      enable: true
      url: https://www.gstatic.com/generate_204
      interval: 300
      timeout: 5000
      lazy: false
      expected-status: 204
    exclude-filter: '(剩余流量|套餐到期|到期时间|官网|公告|重置时间|节点超时.*更新订阅)'
    override:
      additional-prefix: "AP | "
`, subURL, gen), "\n")
		airportGroup = strings.Split(`  - name: "AIRPORT"
    type: url-test
    use:
      - airport-sub
    url: https://www.gstatic.com/generate_204
    interval: 300
    timeout: 5000
    expected-status: 204
    tolerance: 100
    lazy: false
    empty-fallback: REJECT
`, "\n")
		autoMembers = append(autoMembers, "      - AIRPORT")
		mediaMembers = append(mediaMembers, "      - AIRPORT")
	}
	autoMembers = append(autoMembers, "      - DIRECT")

	raw, err := goldenFS.ReadFile("golden.yaml")
	if err != nil {
		return nil, err
	}
	var out []string
	for _, line := range strings.Split(string(raw), "\n") {
		trimmed := strings.TrimSpace(line)
		switch trimmed {
		case markerNodeProxies:
			out = append(out, proxies...)
		case markerGroupProxy:
			out = append(out, groupProxy...)
		case markerGroupAuto:
			out = append(out, groupAuto...)
		case markerNodeDirect:
			out = append(out, directRules...)
		case markerMediaMembers:
			out = append(out, mediaMembers...)
		case markerProxyProviders:
			out = append(out, providersBlock...)
		case markerAirportGroup:
			out = append(out, airportGroup...)
		case markerAutoMembers:
			out = append(out, autoMembers...)
		default:
			out = append(out, line)
		}
	}
	// 标记全部被替换，残留说明模板被破坏
	joined := strings.Join(out, "\n")
	for _, m := range []string{markerNodeProxies, markerGroupProxy, markerGroupAuto, markerNodeDirect, markerMediaMembers, markerProxyProviders, markerAirportGroup, markerAutoMembers} {
		if strings.Contains(joined, m) {
			return nil, fmt.Errorf("黄金模板标记 %s 未被替换", m)
		}
	}
	return []byte(joined), nil
}

func containsStr(list []string, s string) bool {
	for _, v := range list {
		if v == s {
			return true
		}
	}
	return false
}

// renderVLESS vless-reality 节点参数校验与出站段渲染。
func renderVLESS(n *NodeRender, proxies *[]string) error {
	if !crypto.ValidUUID(n.UUID) {
		return fmt.Errorf("节点 %s uuid 参数不合法", n.Name)
	}
	if !crypto.ValidPBK(n.PBK) {
		return fmt.Errorf("节点 %s pbk 参数不合法", n.Name)
	}
	if !crypto.ValidSID(n.SID) {
		return fmt.Errorf("节点 %s sid 参数不合法", n.Name)
	}
	sni := n.SNI
	if sni == "" {
		sni = "www.microsoft.com"
	}
	flow := n.Flow
	if flow == "" {
		flow = "xtls-rprx-vision"
	}
	fp := n.FP
	if fp == "" {
		fp = "chrome"
	}
	*proxies = append(*proxies, fmt.Sprintf(`  - name: "%s"
    type: vless
    server: %s
    port: %d
    uuid: %s
    network: tcp
    udp: true
    tls: true
    flow: %s
    servername: %s
    client-fingerprint: %s
    reality-opts:
      public-key: %s
      short-id: %s`, n.Name, n.Host, n.Port, n.UUID, flow, sni, fp, n.PBK, n.SID))
	return nil
}

// renderVLESSWS vless-ws 节点（Cloudflare CDN 中转）：host=CF 边缘 IPv4（优选），
// SNI/Host 头=中转域名，TLS 在 CF 边缘终止。禁止 flow（vision 与 ws 不兼容）。
var reWSPath = regexp.MustCompile(`^/[A-Za-z0-9._-]{0,32}$`)
var reWSHost = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9.-]{0,253}$`)

func ValidWSPath(p string) bool { return reWSPath.MatchString(p) }
func ValidWSHost(h string) bool { return reWSHost.MatchString(h) }

func renderVLESSWS(n *NodeRender, proxies *[]string) error {
	if !crypto.ValidUUID(n.UUID) {
		return fmt.Errorf("节点 %s uuid 参数不合法", n.Name)
	}
	if !ValidWSHost(n.SNI) {
		return fmt.Errorf("节点 %s sni 参数不合法（vless-ws 需要中转域名，如 cf.example.top）", n.Name)
	}
	if !ValidWSPath(n.WSPath) {
		return fmt.Errorf("节点 %s ws_path 参数不合法（形如 /k3x9，1~32 字符）", n.Name)
	}
	wsHost := n.WSHost
	if wsHost == "" {
		wsHost = n.SNI
	}
	if !ValidWSHost(wsHost) {
		return fmt.Errorf("节点 %s ws_host 参数不合法", n.Name)
	}
	fp := n.FP
	if fp == "" {
		fp = "chrome"
	}
	if n.Flow != "" {
		return fmt.Errorf("节点 %s：vless-ws 不支持 flow（vision 与 ws 不兼容），请留空", n.Name)
	}
	*proxies = append(*proxies, fmt.Sprintf(`  - name: "%s"
    type: vless
    server: %s
    port: %d
    uuid: %s
    network: ws
    udp: false
    tls: true
    servername: %s
    client-fingerprint: %s
    ws-opts:
      path: %s
      headers:
        Host: %s`, n.Name, n.Host, n.Port, n.UUID, n.SNI, fp, n.WSPath, wsHost))
	return nil
}

var reSecretLine = regexp.MustCompile(`^(\s+(?:uuid|password|public-key|short-id): )(\S+)$`)

// MaskYAML 将渲染结果中的凭据行掩码（uuid/pbk 前后 4 位，sid/password 前后 2 位）。
// 仅用于管理端预览/详情展示；原始 YAML 只允许设备经 HMAC/token 下载。
func MaskYAML(yamlBytes []byte) string {
	lines := strings.Split(string(yamlBytes), "\n")
	for i, line := range lines {
		m := reSecretLine.FindStringSubmatch(line)
		if m == nil {
			continue
		}
		key := strings.TrimSpace(strings.SplitN(m[1], ":", 2)[0])
		lines[i] = m[1] + crypto.MaskNodeParam(key, m[2])
	}
	return strings.Join(lines, "\n")
}
