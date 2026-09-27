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

var reNodeName = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$`)

// NodeRender 参与渲染的节点（已从 AES-GCM 信封解出参数）。
type NodeRender struct {
	ID    int64
	Name  string
	Host  string
	Port  int
	UUID  string
	PBK   string
	SNI   string
	SID   string
	Flow  string
	FP    string
}

// RenderInput 渲染输入。
type RenderInput struct {
	DeviceID       string
	Nodes          []NodeRender // 按优先级排序，第一个为 primary
	MgmtDirectIPs  []string     // 管理面 WG endpoint 等需直连的 IPv4
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
	var proxies, groupProxy, groupAuto, directRules []string
	for _, n := range in.Nodes {
		if !ValidateNodeName(n.Name) {
			return nil, fmt.Errorf("节点名 %q 不合法（仅允许字母数字与 - _，长度 2~32）", n.Name)
		}
		if err := validateHost(n.Host); err != nil {
			return nil, err
		}
		if n.Port < 1 || n.Port > 65535 {
			return nil, fmt.Errorf("节点 %s 端口不合法", n.Name)
		}
		if !crypto.ValidUUID(n.UUID) {
			return nil, fmt.Errorf("节点 %s uuid 参数不合法", n.Name)
		}
		if !crypto.ValidPBK(n.PBK) {
			return nil, fmt.Errorf("节点 %s pbk 参数不合法", n.Name)
		}
		if !crypto.ValidSID(n.SID) {
			return nil, fmt.Errorf("节点 %s sid 参数不合法", n.Name)
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
		proxies = append(proxies, fmt.Sprintf(`  - name: "%s"
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
		default:
			out = append(out, line)
		}
	}
	// 标记全部被替换，残留说明模板被破坏
	joined := strings.Join(out, "\n")
	for _, m := range []string{markerNodeProxies, markerGroupProxy, markerGroupAuto, markerNodeDirect} {
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

var reSecretLine = regexp.MustCompile(`^(\s+(?:uuid|public-key|short-id): )(\S+)$`)

// MaskYAML 将渲染结果中的凭据行掩码（uuid/pbk 前后 4 位，sid 前后 2 位）。
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
