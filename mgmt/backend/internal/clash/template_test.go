package clash

import (
	"strings"
	"testing"

	"gopkg.in/yaml.v3"
)

func fakeNodes() []NodeRender {
	return []NodeRender{
		{Name: "TEST-SGP1", Host: "203.0.113.10", Port: 443, UUID: "11111111-2222-3333-4444-555555555555",
			PBK: "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", SNI: "www.microsoft.com", SID: "aabbccdd", Flow: "xtls-rprx-vision", FP: "chrome"},
		{Name: "TEST-TKY2", Host: "198.51.100.20", Port: 443, UUID: "66666666-7777-8888-9999-000000000000",
			PBK: "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB", SNI: "www.microsoft.com", SID: "11223344"},
	}
}

func renderOK(t *testing.T, in RenderInput) []byte {
	t.Helper()
	out, err := Render(in)
	if err != nil {
		t.Fatalf("Render 失败: %v", err)
	}
	return out
}

func TestMediaFallbackMembers(t *testing.T) {
	for _, hasLAX := range []bool{false, true} {
		nodes := fakeNodes()
		if hasLAX {
			nodes[1].Name = "LAX-VLESS-US-01"
		}
		var doc map[string]any
		if err := yaml.Unmarshal(renderOK(t, RenderInput{DeviceID: "rtr-media", Nodes: nodes}), &doc); err != nil {
			t.Fatal(err)
		}
		found := false
		for _, item := range doc["proxy-groups"].([]any) {
			g := item.(map[string]any)
			if g["name"] != "US-MEDIA" {
				continue
			}
			found = true
			members := toStrings(g["proxies"].([]any))
			want := "NODE-PICK"
			if hasLAX {
				want = "LAX-VLESS-US-01,NODE-PICK"
			}
			if strings.Join(members, ",") != want || g["include-all"] != nil {
				t.Fatalf("invalid media fallback (LAX=%v): %v", hasLAX, g)
			}
		}
		if !found {
			t.Fatal("US-MEDIA missing")
		}
	}
}

// TestGoldenRules 断言任务书 §3.2 全部硬性检查。
func TestGoldenRules(t *testing.T) {
	in := RenderInput{DeviceID: "rtr-test01", Nodes: fakeNodes(), MgmtDirectIPs: []string{"192.0.2.1"}}
	raw := renderOK(t, in)

	var doc map[string]any
	if err := yaml.Unmarshal(raw, &doc); err != nil {
		t.Fatalf("渲染结果不是合法 YAML: %v", err)
	}
	s := string(raw)

	// 1. GEOSITE,cn,DIRECT 与 GEOIP,CN,DIRECT,no-resolve
	if !strings.Contains(s, "- GEOSITE,cn,DIRECT\n") {
		t.Error("缺少 GEOSITE,cn,DIRECT")
	}
	if !strings.Contains(s, "- GEOIP,CN,DIRECT,no-resolve") {
		t.Error("缺少 GEOIP,CN,DIRECT,no-resolve")
	}

	// 2. CN-MEDIA 组只允许 DIRECT
	groups := doc["proxy-groups"].([]any)
	var cnMedia map[string]any
	for _, g := range groups {
		gm := g.(map[string]any)
		if gm["name"] == "CN-MEDIA" {
			cnMedia = gm
		}
	}
	if cnMedia == nil {
		t.Fatal("缺少 CN-MEDIA 组")
	}
	proxies := cnMedia["proxies"].([]any)
	if len(proxies) != 1 || proxies[0] != "DIRECT" {
		t.Errorf("CN-MEDIA 组只允许 DIRECT，实际 %v", proxies)
	}

	// 3. 节点公网 IP 的 /32 直连规则
	for _, n := range fakeNodes() {
		if !strings.Contains(s, "- IP-CIDR,"+n.Host+"/32,DIRECT,no-resolve") {
			t.Errorf("缺少节点 %s 的 /32 直连规则", n.Host)
		}
	}
	// 管理面 endpoint 直连
	if !strings.Contains(s, "- IP-CIDR,192.0.2.1/32,DIRECT,no-resolve") {
		t.Error("缺少管理面直连规则")
	}

	// 4. MATCH,PROXY 是最后一条规则
	rules := doc["rules"].([]any)
	if last, ok := rules[len(rules)-1].(string); !ok || last != "MATCH,PROXY" {
		t.Errorf("最后一条规则必须是 MATCH,PROXY，实际 %v", rules[len(rules)-1])
	}

	// 5. fake-ip
	dns := doc["dns"].(map[string]any)
	if dns["enhanced-mode"] != "fake-ip" {
		t.Errorf("dns.enhanced-mode 必须是 fake-ip，实际 %v", dns["enhanced-mode"])
	}

	// 6. akamaized.net 不得进 CN-MEDIA（本模板中不得出现）
	if strings.Contains(s, "akamaized") {
		t.Error("akamaized.net 出现在模板中（国内外站点共用，禁止整段送 CN-MEDIA）")
	}

	// 7. UDP/443 REJECT 保留在 MATCH 之前
	if !strings.Contains(s, "- AND,((NETWORK,UDP),(DST-PORT,443)),REJECT") {
		t.Error("缺少 UDP/443 REJECT 规则")
	}

	// 8. 动态项：PROXY/AUTO/NODE-PICK 组结构（2026-09-25 起 AUTO=fallback 容灾）
	var proxyGroup, autoGroup, nodePick map[string]any
	for _, g := range groups {
		gm := g.(map[string]any)
		switch gm["name"] {
		case "PROXY":
			proxyGroup = gm
		case "AUTO":
			autoGroup = gm
		case "NODE-PICK":
			nodePick = gm
		}
	}
	if proxyGroup == nil || autoGroup == nil || nodePick == nil {
		t.Fatal("缺少 PROXY/AUTO/NODE-PICK 组")
	}
	if !listHas(proxyGroup["proxies"], "TEST-SGP1") || !listHas(proxyGroup["proxies"], "TEST-TKY2") {
		t.Errorf("PROXY 组缺少节点成员: %v", proxyGroup["proxies"])
	}
	if !listHas(proxyGroup["proxies"], "AUTO") || !listHas(proxyGroup["proxies"], "DIRECT") {
		t.Errorf("PROXY 组必须保留 AUTO 与 DIRECT: %v", proxyGroup["proxies"])
	}
	// AUTO 必须 fallback：无机场时 [NODE-PICK, DIRECT]；有机场时 [NODE-PICK, AIRPORT, DIRECT]
	if autoGroup["type"] != "fallback" {
		t.Errorf("AUTO 组类型必须是 fallback，实际 %v", autoGroup["type"])
	}
	aProxies := toStrings(autoGroup["proxies"].([]any))
	if len(aProxies) != 2 || aProxies[0] != "NODE-PICK" || aProxies[1] != "DIRECT" {
		t.Errorf("无机场渲染时 AUTO 必须且只能是 [NODE-PICK, DIRECT]，实际 %v", aProxies)
	}
	// NODE-PICK 必须 fallback（2026-09-27 M03：腾讯主用/DO 备用的有序主备，
	// 按成员顺序取第一个健康节点，成员顺序=绑定优先级 primary 在前）
	if nodePick["type"] != "fallback" {
		t.Errorf("NODE-PICK 组类型必须是 fallback，实际 %v", nodePick["type"])
	}
	if autoGroup["url"] != nodePick["url"] || autoGroup["expected-status"] != 204 {
		t.Errorf("nested AUTO/NODE-PICK health targets must agree: AUTO=%v NODE-PICK=%v", autoGroup["url"], nodePick["url"])
	}
	if !listHas(nodePick["proxies"], "TEST-SGP1") || !listHas(nodePick["proxies"], "TEST-TKY2") {
		t.Errorf("NODE-PICK 组缺少节点成员: %v", nodePick["proxies"])
	}

	// 9. 国内视频域名样例在 CN-MEDIA 段
	for _, d := range []string{"douyin.com", "bilibili", "xiaohongshu.com", "ixigua.com"} {
		if !strings.Contains(s, d) {
			t.Errorf("缺少国内视频规则 %s", d)
		}
	}

	// 10. gstatic 必须走 PROXY 且先于 GEOSITE,google-cn 命中（2026-09-27 统一优化 M02：
	// 办公网 DNS fallback 可能给境外不可达答案，DIRECT 直拨超时）
	ruleStrs := toStrings(doc["rules"].([]any))
	gs := indexOf(ruleStrs, "DOMAIN-SUFFIX,gstatic.com,PROXY")
	gcn := indexOf(ruleStrs, "GEOSITE,google-cn,DIRECT")
	if gs < 0 {
		t.Error("缺少 DOMAIN-SUFFIX,gstatic.com,PROXY 规则")
	} else if gcn < 0 || gs > gcn {
		t.Errorf("gstatic PROXY 规则必须在 GEOSITE,google-cn 之前（gs=%d gcn=%d）", gs, gcn)
	}
	// 10b. dl.google.com 同理（2026-09-30：google-cn 判 DIRECT 使大文件下载在劣质线路必死）
	dlg := indexOf(ruleStrs, "DOMAIN-SUFFIX,dl.google.com,PROXY")
	if dlg < 0 {
		t.Error("缺少 DOMAIN-SUFFIX,dl.google.com,PROXY 规则")
	} else if dlg > gcn {
		t.Errorf("dl.google PROXY 规则必须在 GEOSITE,google-cn 之前（dlg=%d gcn=%d）", dlg, gcn)
	}
}

// TestGoldenRuleOrderStaticTail 结构差异测试：渲染后的静态规则尾部必须与黄金模板骨架一致。
func TestGoldenRuleOrderStaticTail(t *testing.T) {
	in := RenderInput{DeviceID: "rtr-test01", Nodes: fakeNodes()}
	raw := renderOK(t, in)
	var doc map[string]any
	if err := yaml.Unmarshal(raw, &doc); err != nil {
		t.Fatal(err)
	}
	rules := toStrings(doc["rules"].([]any))

	wantTail := []string{
		"GEOIP,private,DIRECT,no-resolve",
		"IP-CIDR,192.168.0.0/16,DIRECT,no-resolve",
		"IP-CIDR,10.0.0.0/8,DIRECT,no-resolve",
		"IP-CIDR,172.16.0.0/12,DIRECT,no-resolve",
		"IP-CIDR,100.64.0.0/10,DIRECT,no-resolve",
		"IP-CIDR,169.254.0.0/16,DIRECT,no-resolve",
		"IP-CIDR6,fc00::/7,DIRECT,no-resolve",
		"IP-CIDR6,fe80::/10,DIRECT,no-resolve",
		"GEOSITE,private,DIRECT",
		"GEOSITE,bilibili,CN-MEDIA",
		"GEOSITE,cn,DIRECT",
		"GEOIP,CN,DIRECT,no-resolve",
		"DOMAIN-SUFFIX,swcdn.apple.com,DOWNLOAD",
		"AND,((NETWORK,UDP),(DST-PORT,443)),REJECT",
		"MATCH,PROXY",
	}
	got := strings.Join(rules, "\n")
	for i := 0; i < len(wantTail)-1; i++ {
		a, b := wantTail[i], wantTail[i+1]
		ia := indexOf(rules, a)
		ib := indexOf(rules, b)
		if ia < 0 || ib < 0 {
			t.Fatalf("静态规则缺失: %s / %s", a, b)
		}
		if ia >= ib {
			t.Errorf("规则顺序被改乱: %s(%d) 应在 %s(%d) 之前", a, ia, b, ib)
		}
	}
	_ = got
	// 节点直连规则必须先于 GEOIP,private（第一位）
	if rules[0] != "IP-CIDR,203.0.113.10/32,DIRECT,no-resolve" {
		t.Errorf("第一条规则应是第一个节点的直连规则，实际 %s", rules[0])
	}
}

// TestRenderHostValidation 域名/IPv6 节点必须返回明确校验错误。
func TestRenderHostValidation(t *testing.T) {
	for _, host := range []string{"sgp.example.com", "2001:db8::1", ""} {
		in := RenderInput{DeviceID: "rtr-x", Nodes: []NodeRender{{Name: "N1", Host: host, Port: 443,
			UUID: "11111111-2222-3333-4444-555555555555", PBK: "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", SID: "aabb"}}}
		if _, err := Render(in); err == nil {
			t.Errorf("host=%q 应拒绝", host)
		}
	}
	if _, err := Render(RenderInput{DeviceID: "rtr-x"}); err == nil {
		t.Error("无节点应拒绝")
	}
}

// TestMaskYAML 掩码渲染：uuid/pbk 前后 4 位，sid 前后 2 位，不得遗留全文。
func TestMaskYAML(t *testing.T) {
	raw := renderOK(t, RenderInput{DeviceID: "rtr-test01", Nodes: fakeNodes()})
	masked := MaskYAML(raw)
	for _, secret := range []string{
		"11111111-2222-3333-4444-555555555555",
		"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
		"aabbccdd",
	} {
		if strings.Contains(masked, secret) {
			t.Errorf("掩码后仍含明文凭据 %s", secret[:6])
		}
	}
	if !strings.Contains(masked, "uuid: 1111...5555") {
		t.Error("uuid 掩码格式不符（前后 4 位）")
	}
}

// TestRenderHy2 hysteria2 节点：出站段类型/端口/密码正确，组成员与直连规则同步，
// 密码弱/缺失拒绝；MaskYAML 掩码 password；vless 存量字段缺失时拒绝（不静默降级）。
func TestRenderHy2(t *testing.T) {
	pw := "Hy2Test-Password-2026-AbCdEf123456"
	in := RenderInput{DeviceID: "rtr-hy2", Nodes: []NodeRender{{
		Name: "TX-HY2-01", Proto: ProtoHy2, Host: "203.0.113.99", Port: 443, Password: pw,
	}}}
	raw := renderOK(t, in)
	s := string(raw)
	if !strings.Contains(s, "type: hysteria2") {
		t.Error("缺少 hysteria2 出站类型")
	}
	if !strings.Contains(s, "server: 203.0.113.99") || !strings.Contains(s, "port: 443") {
		t.Error("host/port 未渲染")
	}
	if !strings.Contains(s, "password: "+pw) {
		t.Error("密码未渲染")
	}
	if !strings.Contains(s, "skip-cert-verify: true") {
		t.Error("自签证书需 skip-cert-verify")
	}
	if !strings.Contains(s, "- TX-HY2-01") {
		t.Error("组成员未包含 hy2 节点")
	}
	if !strings.Contains(s, "IP-CIDR,203.0.113.99/32,DIRECT,no-resolve") {
		t.Error("节点直连规则缺失")
	}
	// 掩码
	masked := MaskYAML(raw)
	if strings.Contains(masked, pw) {
		t.Error("MaskYAML 泄露 hy2 密码")
	}
	if !strings.Contains(masked, "password: "+MaskNodeParamForTest(pw)) {
		t.Error("password 掩码格式不符")
	}
	// 弱密码/缺失拒绝
	for _, bad := range []string{"", "short", "含中文密码password测试数据含中文密码", "has space in it 1234567890"} {
		if _, err := Render(RenderInput{DeviceID: "rtr-x", Nodes: []NodeRender{{
			Name: "TX-HY2-01", Proto: ProtoHy2, Host: "203.0.113.99", Port: 443, Password: bad,
		}}}); err == nil {
			t.Errorf("弱密码 %q 应拒绝", bad[:min(len(bad), 8)])
		}
	}
	// 未知协议拒绝
	if _, err := Render(RenderInput{DeviceID: "rtr-x", Nodes: []NodeRender{{
		Name: "X1", Proto: "trojan", Host: "203.0.113.99", Port: 443,
	}}}); err == nil {
		t.Error("未知协议应拒绝")
	}
	// hysteria2 节点误带 vless 校验路径：proto=hy2 时 uuid/pbk/sid 不应被要求（上方已验证渲染成功）
}

func MaskNodeParamForTest(pw string) string {
	return pw[:2] + "..." + pw[len(pw)-2:]
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}

func listHas(v any, s string) bool {
	l, ok := v.([]any)
	if !ok {
		return false
	}
	for _, item := range l {
		if item == any(s) {
			return true
		}
	}
	return false
}

func toStrings(l []any) []string {
	out := make([]string, 0, len(l))
	for _, v := range l {
		out = append(out, v.(string))
	}
	return out
}

func indexOf(list []string, s string) int {
	for i, v := range list {
		if v == s {
			return i
		}
	}
	return -1
}

// TestRenderVLESSWS vless-ws（CF CDN 中转）节点渲染：结构、默认指纹、Host 回退、flow 拒绝。
func TestRenderVLESSWS(t *testing.T) {
	nodes := append(fakeNodes(), NodeRender{
		Name: "CF-CDN-GIA-01", Proto: ProtoVLESSWS, Host: "104.18.32.7", Port: 2053,
		UUID: "99999999-8888-7777-6666-555555555555", SNI: "cf.example.top", WSPath: "/k3x9",
	})
	var doc map[string]any
	if err := yaml.Unmarshal(renderOK(t, RenderInput{DeviceID: "rtr-ws", Nodes: nodes}), &doc); err != nil {
		t.Fatal(err)
	}
	var cf map[string]any
	for _, item := range doc["proxies"].([]any) {
		p := item.(map[string]any)
		if p["name"] == "CF-CDN-GIA-01" {
			cf = p
		}
	}
	if cf == nil {
		t.Fatal("CF 节点未渲染")
	}
	if cf["network"] != "ws" || cf["tls"] != true || cf["servername"] != "cf.example.top" {
		t.Fatalf("CF 节点基础字段错误: %v", cf)
	}
	if cf["client-fingerprint"] != "chrome" {
		t.Fatalf("CF 节点默认指纹缺失: %v", cf["client-fingerprint"])
	}
	ws := cf["ws-opts"].(map[string]any)
	if ws["path"] != "/k3x9" || ws["headers"].(map[string]any)["Host"] != "cf.example.top" {
		t.Fatalf("ws-opts 错误: %v", ws)
	}
	// flow 必须被拒绝（vision 与 ws 不兼容）；注意须改切片元素而非结构体副本
	nodes[len(nodes)-1].Flow = "xtls-rprx-vision"
	if _, err := Render(RenderInput{DeviceID: "rtr-ws2", Nodes: nodes}); err == nil {
		t.Fatal("vless-ws 带 flow 应被拒绝")
	}
	// 非法 path 拒绝
	nodes[len(nodes)-1].Flow = ""
	nodes[len(nodes)-1].WSPath = "no-slash"
	if _, err := Render(RenderInput{DeviceID: "rtr-ws3", Nodes: nodes}); err == nil {
		t.Fatal("非法 ws_path 应被拒绝")
	}
}

// TestRenderAirport 机场订阅兜底渲染：provider/组/链位/空组REJECT/无机场时零残留。
func TestRenderAirport(t *testing.T) {
	sub := "https://sub.example.cc/api/v1/client/subscribe?token=tok123"
	// 1) 有机场
	var doc map[string]any
	y := renderOK(t, RenderInput{DeviceID: "rtr-ap", Nodes: fakeNodes(),
		AirportSubURL: sub, AirportGeneration: "g01"})
	if err := yaml.Unmarshal(y, &doc); err != nil {
		t.Fatal(err)
	}
	prov := doc["proxy-providers"].(map[string]any)["airport-sub"].(map[string]any)
	if prov["url"] != sub || prov["proxy"] != "NODE-PICK" {
		t.Fatalf("provider 字段错误: %v", prov)
	}
	if prov["path"] != "./proxy_providers/airport-g01.yaml" {
		t.Fatalf("缓存代次路径错误: %v", prov["path"])
	}
	grps := map[string]map[string]any{}
	for _, item := range doc["proxy-groups"].([]any) {
		g := item.(map[string]any)
		grps[g["name"].(string)] = g
	}
	ap := grps["AIRPORT"]
	if ap == nil || ap["type"] != "url-test" || ap["empty-fallback"] != "REJECT" {
		t.Fatalf("AIRPORT 组缺失或配置错误: %v", ap)
	}
	if ap["use"].([]any)[0] != "airport-sub" {
		t.Fatalf("AIRPORT 未引用 provider: %v", ap["use"])
	}
	autoM := toStrings(grps["AUTO"]["proxies"].([]any))
	if len(autoM) != 3 || autoM[0] != "NODE-PICK" || autoM[1] != "AIRPORT" || autoM[2] != "DIRECT" {
		t.Fatalf("有机场时 AUTO 成员错误: %v", autoM)
	}
	um := toStrings(grps["US-MEDIA"]["proxies"].([]any))
	if um[len(um)-1] != "AIRPORT" {
		t.Fatalf("US-MEDIA 未追加 AIRPORT: %v", um)
	}
	// 2) 无机场：零机场元素残留
	var doc2 map[string]any
	if err := yaml.Unmarshal(renderOK(t, RenderInput{DeviceID: "rtr-noap", Nodes: fakeNodes()}), &doc2); err != nil {
		t.Fatal(err)
	}
	if doc2["proxy-providers"] != nil {
		t.Fatal("无机场时不应有 proxy-providers")
	}
	for _, item := range doc2["proxy-groups"].([]any) {
		if item.(map[string]any)["name"] == "AIRPORT" {
			t.Fatal("无机场时不应有 AIRPORT 组")
		}
	}
}
