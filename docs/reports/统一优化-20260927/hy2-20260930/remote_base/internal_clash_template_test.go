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
	// AUTO 必须 fallback 且成员恰为 [NODE-PICK, DIRECT]：节点全挂时自动回落直连
	if autoGroup["type"] != "fallback" {
		t.Errorf("AUTO 组类型必须是 fallback，实际 %v", autoGroup["type"])
	}
	aProxies := toStrings(autoGroup["proxies"].([]any))
	if len(aProxies) != 2 || aProxies[0] != "NODE-PICK" || aProxies[1] != "DIRECT" {
		t.Errorf("AUTO 组必须且只能是 [NODE-PICK, DIRECT]，实际 %v", aProxies)
	}
	if nodePick["type"] != "url-test" {
		t.Errorf("NODE-PICK 组类型必须是 url-test，实际 %v", nodePick["type"])
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
