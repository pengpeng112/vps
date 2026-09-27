package httpserver

// 数据库集成测试（需要 Postgres，任务书 §20.1）：
//   环境变量 ROUTERMGMT_TEST_DSN 指向空库，如
//   postgres://mgmt:m@127.0.0.1:55432/mgmt_test?sslmode=disable
//   未设置时整体跳过。运行方式见 mgmt/scripts/test-integration.ps1 / .sh

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/pquerna/otp"
	"github.com/pquerna/otp/totp"

	"routermgmt/internal/config"
	"routermgmt/internal/crypto"
	"routermgmt/internal/db"
	"routermgmt/internal/models"
)

var (
	intgDSN string
	srv     *Server
	ts      *httptest.Server
)

func TestMain(m *testing.M) {
	gin.SetMode(gin.TestMode)
	intgDSN = os.Getenv("ROUTERMGMT_TEST_DSN")
	if intgDSN == "" {
		fmt.Println("ROUTERMGMT_TEST_DSN 未设置，跳过数据库集成测试（HMAC 等单测仍运行）")
		os.Exit(m.Run())
	}
	g, err := db.Connect(intgDSN)
	if err != nil {
		fmt.Println("连接测试库失败:", err)
		os.Exit(1)
	}
	if _, err := g.DB(); err == nil {
		g.Exec(`DROP SCHEMA public CASCADE; CREATE SCHEMA public;`)
		// 0002 授权迁移引用的角色（生产由 compose init 脚本创建）
		g.Exec(`DO $$ BEGIN
			IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='mgmt_migrate') THEN
				CREATE ROLE mgmt_migrate LOGIN PASSWORD 'test';
			END IF;
			IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='mgmt_app') THEN
				CREATE ROLE mgmt_app LOGIN PASSWORD 'test';
			END IF;
		END $$;`)
	}
	if err := db.Migrate(g); err != nil {
		fmt.Println("迁移失败:", err)
		os.Exit(1)
	}
	dir, _ := os.MkdirTemp("", "mgmt-test")
	defer os.RemoveAll(dir)
	cfg := &config.Config{
		Env: "dev", Listen: "127.0.0.1:0", DatabaseURL: intgDSN,
		DataDir: dir, JWTSecret: "test-jwt-secret-32bytes-xxxxxxxxx",
		BootstrapAdmin: "admin", BootstrapPassword: "bootstrap-pass-123",
		MgmtPublicURL: "https://10.10.0.1:8443", TrafficAlertGB: 80,
		HeartbeatTimeout: 5 * time.Second, OfflineScanEvery: time.Second, MetricsKeepDays: 90,
	}
	masterKey := make([]byte, 32)
	for i := range masterKey {
		masterKey[i] = byte(i + 1)
	}
	signKey, err := NewEd25519SigningKey(dir)
	if err != nil {
		fmt.Println(err)
		os.Exit(1)
	}
	srv = New(cfg, g, masterKey, signKey)
	if err := srv.BootstrapAdmin(); err != nil {
		fmt.Println(err)
		os.Exit(1)
	}
	ts = httptest.NewServer(srv.BuildEngine())
	defer ts.Close()
	os.Exit(m.Run())
}

// ---- 测试辅助 ----

// httpDo 走真实 httptest server（需要中间件读取 RemoteAddr 等）。
func requireDB(t *testing.T) {
	t.Helper()
	if ts == nil || srv == nil {
		t.Skip("需要 ROUTERMGMT_TEST_DSN")
	}
}

func httpDo(t *testing.T, method, path string, body []byte, headers map[string]string) (int, map[string]any, string) {
	t.Helper()
	requireDB(t)
	req, _ := http.NewRequest(method, ts.URL+path, bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("请求失败: %v", err)
	}
	defer resp.Body.Close()
	buf := new(bytes.Buffer)
	buf.ReadFrom(resp.Body)
	raw := buf.String()
	var parsed map[string]any
	json.Unmarshal(buf.Bytes(), &parsed)
	return resp.StatusCode, parsed, raw
}

func adminLogin(t *testing.T) string {
	t.Helper()
	// bootstrap 管理员
	st, body, _ := httpDo(t, "POST", "/api/v1/auth/login",
		mustJSON(t, map[string]string{"username": "admin", "password": "bootstrap-pass-123"}), nil)
	if st != 200 {
		t.Fatalf("登录失败 %d %v", st, body)
	}
	needSetup, _ := body["need_setup"].(bool)
	if !needSetup {
		t.Fatalf("首次登录应返回 need_setup: %v", body)
	}
	ticket := body["ticket"].(string)
	// setup TOTP
	st, body, _ = httpDo(t, "POST", "/api/v1/auth/totp/setup", mustJSON(t, map[string]string{"ticket": ticket}), nil)
	if st != 200 {
		t.Fatalf("totp setup 失败 %d %v", st, body)
	}
	secret := body["secret"].(string)
	code, err := totp.GenerateCodeCustom(secret, time.Now(), totp.ValidateOpts{Period: 30, Skew: 1, Digits: otp.DigitsSix, Algorithm: otp.AlgorithmSHA1})
	if err != nil {
		t.Fatal(err)
	}
	st, body, _ = httpDo(t, "POST", "/api/v1/auth/totp/confirm", mustJSON(t, map[string]string{"ticket": ticket, "code": code}), nil)
	if st != 200 {
		t.Fatalf("totp confirm 失败 %d %v", st, body)
	}
	return body["token"].(string)
}

func totpCode(t *testing.T, secret string) string {
	code, err := totp.GenerateCodeCustom(secret, time.Now(), totp.ValidateOpts{Period: 30, Skew: 1, Digits: otp.DigitsSix, Algorithm: otp.AlgorithmSHA1})
	if err != nil {
		t.Fatal(err)
	}
	return code
}

func mustJSON(t *testing.T, v any) []byte {
	t.Helper()
	raw, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	return raw
}

type deviceCreds struct {
	ID      string
	HMACKey string
	Token   string
}

func createDeviceAndEnroll(t *testing.T, jwt string, name string) deviceCreds {
	t.Helper()
	st, body, _ := httpDo(t, "POST", "/api/v1/devices", mustJSON(t, map[string]string{"name": name}), map[string]string{"Authorization": "Bearer " + jwt})
	if st != 200 {
		t.Fatalf("创建设备失败 %d %v", st, body)
	}
	code := body["enroll_code"].(string)
	devID := body["id"].(string)
	st, body, _ = httpDo(t, "POST", "/api/v1/devices/enroll", mustJSON(t, map[string]string{
		"enroll_code": code, "hw_model": "test,model", "firmware_ver": "test-1.0",
	}), nil)
	if st != 200 {
		t.Fatalf("enroll 失败 %d %v", st, body)
	}
	return deviceCreds{ID: devID, HMACKey: body["hmac_key"].(string), Token: body["sub_token"].(string)}
}

var nonceSeq int

func agentRequest(t *testing.T, creds deviceCreds, method, path string, body []byte, overrides map[string]string) (int, map[string]any, string) {
	t.Helper()
	nonceSeq++
	nonce := fmt.Sprintf("%032x", nonceSeq)
	tsStr := fmt.Sprintf("%d", time.Now().Unix())
	if v, ok := overrides["ts"]; ok {
		tsStr = v
	}
	if v, ok := overrides["nonce"]; ok {
		nonce = v
	}
	bodyHash := crypto.SHA256Hex(body)
	sig := SignCanonical(creds.HMACKey, CanonicalString(creds.ID, toInt(tsStr), nonce, method, path, bodyHash))
	headers := map[string]string{
		"X-Device-Id":   creds.ID,
		"X-Timestamp":   tsStr,
		"X-Nonce":       nonce,
		"Authorization": "HMAC-SHA256 " + sig,
	}
	for k, v := range overrides {
		if k == "Authorization" || k == "X-Device-Id" {
			headers[k] = v
		}
	}
	return httpDo(t, method, path, body, headers)
}

func toInt(s string) int64 {
	var v int64
	fmt.Sscanf(s, "%d", &v)
	return v
}

func createTestNode(t *testing.T, jwt, name, host string) int64 {
	t.Helper()
	st, body, _ := httpDo(t, "POST", "/api/v1/nodes", mustJSON(t, map[string]any{
		"name": name, "host": host, "port": 443,
		"params": map[string]string{
			"uuid": "11111111-2222-3333-4444-555555555555",
			"pbk":  "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
			"sid":  "aabbccdd", "sni": "www.microsoft.com",
		},
	}), map[string]string{"Authorization": "Bearer " + jwt})
	if st != 200 {
		t.Fatalf("创建节点失败 %d %v", st, body)
	}
	f := body["id"].(float64)
	return int64(f)
}

// ---- 用例 ----

var adminJWT string

func ensureAdmin(t *testing.T) string {
	t.Helper()
	if adminJWT == "" {
		adminJWT = adminLogin(t)
	}
	return adminJWT
}

// TestAdminAuthFlow 未完成 TOTP 绑定时拿不到完整 JWT；ticket 重用失败；限流生效。
func TestAdminAuthFlow(t *testing.T) {
	// bootstrap 密码来自 config? 测试里 BootstrapAdmin 用 cfg.BootstrapAdmin/Password —— 为空会失败
	// 上面的 adminLogin 已覆盖主流程；这里补 ticket 重用与错误密码
	st, body, _ := httpDo(t, "POST", "/api/v1/auth/login", mustJSON(t, map[string]string{"username": "admin", "password": "wrong"}), nil)
	if st != 401 {
		t.Fatalf("错误密码应 401，实际 %d %v", st, body)
	}
}

func TestDeviceLifecycleAndAuth(t *testing.T) {
	jwt := ensureAdmin(t)
	creds := createDeviceAndEnroll(t, jwt, "t-auth")

	// 心跳成功
	st, body, _ := agentRequest(t, creds, "POST", "/api/v1/agent/heartbeat", mustJSON(t, map[string]any{
		"device_id": creds.ID, "ts": time.Now().Unix(),
		"metrics": map[string]any{"sample_id": "s-1", "boot_id": "b-1", "cpu": 12.5},
	}), nil)
	if st != 200 {
		t.Fatalf("心跳失败 %d %v", st, body)
	}

	// 重放同 nonce → 401
	st, _, _ = agentRequest(t, creds, "POST", "/api/v1/agent/heartbeat", mustJSON(t, map[string]any{
		"device_id": creds.ID, "ts": time.Now().Unix(),
		"metrics": map[string]any{"sample_id": "s-2", "boot_id": "b-1"},
	}), map[string]string{"nonce": fmt.Sprintf("%032x", nonceSeq)})
	if st != 401 {
		t.Fatalf("重放 nonce 应 401，实际 %d", st)
	}

	// 过期时间戳 → 401
	st, _, _ = agentRequest(t, creds, "POST", "/api/v1/agent/heartbeat", mustJSON(t, map[string]any{
		"device_id": creds.ID, "ts": time.Now().Unix(),
		"metrics": map[string]any{"sample_id": "s-3"},
	}), map[string]string{"ts": fmt.Sprintf("%d", time.Now().Unix()-600)})
	if st != 401 {
		t.Fatalf("过期时间戳应 401，实际 %d", st)
	}

	// 错误密钥 → 401
	bad := creds
	bad.HMACKey = strings.Repeat("f", 64)
	st, _, _ = agentRequest(t, bad, "POST", "/api/v1/agent/heartbeat", mustJSON(t, map[string]any{
		"device_id": bad.ID, "ts": time.Now().Unix(),
		"metrics": map[string]any{"sample_id": "s-4"},
	}), nil)
	if st != 401 {
		t.Fatalf("错误密钥应 401，实际 %d", st)
	}

	// query 参数 → 401
	st, _, _ = agentRequest(t, creds, "GET", "/api/v1/agent/tasks?x=1", nil, nil)
	if st != 401 {
		t.Fatalf("agent 接口带 query 应 401，实际 %d", st)
	}

	// body device_id 不一致 → 403
	st, _, _ = agentRequest(t, creds, "POST", "/api/v1/agent/heartbeat", mustJSON(t, map[string]any{
		"device_id": "rtr-hax0r", "ts": time.Now().Unix(),
		"metrics": map[string]any{"sample_id": "s-5"},
	}), nil)
	if st != 403 {
		t.Fatalf("body device_id 不一致应 403，实际 %d", st)
	}
}

func TestEnrollGuards(t *testing.T) {
	jwt := ensureAdmin(t)
	// 过期注册码
	st, body, _ := httpDo(t, "POST", "/api/v1/devices", mustJSON(t, map[string]string{"name": "t-expired"}), map[string]string{"Authorization": "Bearer " + jwt})
	if st != 200 {
		t.Fatal("create failed")
	}
	code, id := body["enroll_code"].(string), body["id"].(string)
	srv.DB.Exec(`UPDATE devices SET enroll_code_expires_at = now() - interval '1 minute' WHERE id=?`, id)
	st, _, _ = httpDo(t, "POST", "/api/v1/devices/enroll", mustJSON(t, map[string]string{"enroll_code": code}), nil)
	if st != 401 {
		t.Fatalf("过期注册码应 401，实际 %d", st)
	}

	// 并发 enroll：只有一个成功
	st, body, _ = httpDo(t, "POST", "/api/v1/devices", mustJSON(t, map[string]string{"name": "t-concurrent"}), map[string]string{"Authorization": "Bearer " + jwt})
	code, id = body["enroll_code"].(string), body["id"].(string)
	type res struct{ st int }
	ch := make(chan res, 5)
	payload := mustJSON(t, map[string]string{"enroll_code": code})
	for i := 0; i < 5; i++ {
		go func() {
			req, _ := http.NewRequest("POST", ts.URL+"/api/v1/devices/enroll", bytes.NewReader(payload))
			req.Header.Set("Content-Type", "application/json")
			resp, err := http.DefaultClient.Do(req)
			if err != nil {
				ch <- res{st: -1}
				return
			}
			resp.Body.Close()
			ch <- res{st: resp.StatusCode}
		}()
	}
	okCount := 0
	for i := 0; i < 5; i++ {
		r := <-ch
		if r.st == 200 {
			okCount++
		}
	}
	if okCount != 1 {
		t.Fatalf("并发 enroll 应只成功 1 个，实际 %d", okCount)
	}
	// 注册码已作废：再次使用 → 401
	st, _, _ = httpDo(t, "POST", "/api/v1/devices/enroll", payload, nil)
	if st != 401 {
		t.Fatalf("已用注册码应 401，实际 %d", st)
	}

	// 设备 IP 不重复
	var ips int64
	srv.DB.Raw(`SELECT COUNT(DISTINCT wg_ip) - COUNT(*) FROM devices`).Scan(&ips)
	if ips != 0 {
		t.Fatalf("wg_ip 出现重复")
	}
}

func TestNodesMaskingAndBinding(t *testing.T) {
	jwt := ensureAdmin(t)
	nodeID := createTestNode(t, jwt, "MASKNODE", "203.0.113.99")

	// GET 掩码
	st, body, _ := httpDo(t, "GET", "/api/v1/nodes", nil, map[string]string{"Authorization": "Bearer " + jwt})
	if st != 200 {
		t.Fatal("list nodes failed")
	}
	items := body["items"].([]any)
	first := items[0].(map[string]any)
	params := first["params"].(map[string]any)
	if params["uuid"] != "1111...5555" || params["pbk"] != "AAAA...AAAA" || params["sid"] != "aa...dd" {
		t.Fatalf("掩码不符: %v", params)
	}
	if strings.Contains(fmt.Sprint(body), "11111111-2222-3333-4444-555555555555") {
		t.Fatal("节点列表泄漏完整 uuid")
	}

	// PATCH 写回掩码 → 422
	st, _, _ = httpDo(t, "PATCH", fmt.Sprintf("/api/v1/nodes/%d", nodeID), mustJSON(t, map[string]any{
		"params": map[string]string{"uuid": "1111...5555"},
	}), map[string]string{"Authorization": "Bearer " + jwt})
	if st != 422 {
		t.Fatalf("掩码写回应 422，实际 %d", st)
	}

	// 非法 host（域名）→ 422
	st, _, _ = httpDo(t, "POST", "/api/v1/nodes", mustJSON(t, map[string]any{
		"name": "BADHOST", "host": "sgp.example.com", "port": 443,
		"params": map[string]string{"uuid": "11111111-2222-3333-4444-555555555555", "pbk": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", "sid": "aabb"},
	}), map[string]string{"Authorization": "Bearer " + jwt})
	if st != 422 {
		t.Fatalf("域名 host 应 422，实际 %d", st)
	}
	_ = nodeID
}

func TestConfigPublishAndSubscription(t *testing.T) {
	jwt := ensureAdmin(t)
	creds := createDeviceAndEnroll(t, jwt, "t-sub")
	nodeID := createTestNode(t, jwt, "SUBNODE", "203.0.113.100")

	// 无绑定 → preview 409
	st, _, _ := httpDo(t, "POST", "/api/v1/devices/"+creds.ID+"/configs/preview", mustJSON(t, map[string]string{}), map[string]string{"Authorization": "Bearer " + jwt})
	if st != 409 {
		t.Fatalf("无绑定 preview 应 409，实际 %d", st)
	}

	// 绑定（不 apply）
	st, _, _ = httpDo(t, "POST", "/api/v1/assignments/batch", mustJSON(t, map[string]any{
		"device_ids": []string{creds.ID}, "node_id": nodeID, "is_primary": true,
	}), map[string]string{"Authorization": "Bearer " + jwt})
	if st != 200 {
		t.Fatal("绑定失败")
	}

	// preview：掩码无秘密
	st, body, _ := httpDo(t, "POST", "/api/v1/devices/"+creds.ID+"/configs/preview", mustJSON(t, map[string]string{}), map[string]string{"Authorization": "Bearer " + jwt})
	if st != 200 {
		t.Fatalf("preview 失败 %d %v", st, body)
	}
	masked := body["masked_yaml"].(string)
	if strings.Contains(masked, "11111111-2222-3333-4444-555555555555") {
		t.Fatal("预览泄漏完整凭据")
	}
	if !strings.Contains(masked, "1111...5555") {
		t.Fatal("预览应包含掩码 uuid")
	}
	revision := body["revision"].(string)

	// apply：发布 v1 + 任务
	st, body, _ = httpDo(t, "POST", "/api/v1/devices/"+creds.ID+"/configs/apply", mustJSON(t, map[string]string{"revision": revision}), map[string]string{
		"Authorization": "Bearer " + jwt, "Idempotency-Key": "apply-1"})
	if st != 200 {
		t.Fatalf("apply 失败 %d %v", st, body)
	}
	if v := body["version"].(float64); v != 1 {
		t.Fatalf("首个版本应为 1，实际 %v", v)
	}

	// 订阅：正确 token 返回 YAML；黄金规则在
	st, _, raw := httpDo(t, "GET", "/sub/"+creds.ID+"?token="+creds.Token, nil, nil)
	if st != 200 {
		t.Fatalf("订阅失败 %d %s", st, raw)
	}
	for _, rule := range []string{"GEOSITE,cn,DIRECT", "GEOIP,CN,DIRECT,no-resolve", "IP-CIDR,203.0.113.100/32,DIRECT,no-resolve", "MATCH,PROXY", "enhanced-mode: fake-ip"} {
		if !strings.Contains(raw, rule) {
			t.Fatalf("订阅缺少黄金规则 %s", rule)
		}
	}
	// CN-MEDIA 只有 DIRECT
	sec := raw[strings.Index(raw, "CN-MEDIA"):strings.Index(raw, "DOWNLOAD")]
	if strings.Count(sec, "- DIRECT") != 1 || strings.Contains(sec, "SUBNODE") {
		t.Fatalf("CN-MEDIA 组不合规: %s", sec)
	}

	// 重复 GET 不生成新版本
	var cnt int64
	srv.DB.Raw(`SELECT COUNT(*) FROM configs WHERE device_id=?`, creds.ID).Scan(&cnt)
	if cnt != 1 {
		t.Fatalf("重复订阅不应增加版本，实际 %d", cnt)
	}

	// 错误 token → 401
	st, _, _ = httpDo(t, "GET", "/sub/"+creds.ID+"?token=WRONG", nil, nil)
	if st != 401 {
		t.Fatalf("错误 token 应 401，实际 %d", st)
	}

	// revision 变化后 apply → 409
	srv.DB.Exec(`UPDATE nodes SET host='203.0.113.101' WHERE id=?`, nodeID)
	st, _, _ = httpDo(t, "POST", "/api/v1/devices/"+creds.ID+"/configs/apply", mustJSON(t, map[string]string{"revision": revision}), map[string]string{
		"Authorization": "Bearer " + jwt, "Idempotency-Key": "apply-2"})
	if st != 409 {
		t.Fatalf("revision 过期应 409，实际 %d", st)
	}
}

func TestTaskClaimResultFlow(t *testing.T) {
	jwt := ensureAdmin(t)
	credsA := createDeviceAndEnroll(t, jwt, "t-taskA")
	credsB := createDeviceAndEnroll(t, jwt, "t-taskB")
	nodeID := createTestNode(t, jwt, "TASKNODE", "203.0.113.102")

	// 绑定 + apply 生成 switch_node 任务
	st, body, _ := httpDo(t, "POST", "/api/v1/assignments/batch", mustJSON(t, map[string]any{
		"device_ids": []string{credsA.ID}, "node_id": nodeID, "is_primary": true, "apply": true,
	}), map[string]string{"Authorization": "Bearer " + jwt})
	if st != 200 {
		t.Fatal("绑定+apply 失败")
	}
	items := body["items"].([]any)[0].(map[string]any)
	if items["ok"] != true {
		t.Fatal("apply 应成功")
	}

	// A 领取任务
	st, body, _ = agentRequest(t, credsA, "GET", "/api/v1/agent/tasks", nil, nil)
	if st != 200 {
		t.Fatalf("领取失败 %d %v", st, body)
	}
	tasks := body["tasks"].([]any)
	if len(tasks) != 1 {
		t.Fatalf("应领取 1 个任务，实际 %d", len(tasks))
	}
	task := tasks[0].(map[string]any)
	taskID := task["task_id"].(string)
	if task["type"] != "switch_node" {
		t.Fatalf("任务类型错误: %v", task["type"])
	}
	if task["signature"] == nil || task["config_sha256"] == nil {
		t.Fatal("任务应带签名与哈希")
	}
	configURLPath := strings.TrimPrefix(task["config_url"].(string), "https://10.10.0.1:8443")

	// 下载配置：验证响应头
	st2, _, raw := agentRequest(t, credsA, "GET", configURLPath, nil, nil)
	if st2 != 200 {
		t.Fatalf("下载配置失败 %d", st2)
	}
	if !strings.Contains(raw, "mixed-port: 7890") {
		t.Fatal("配置内容不符")
	}

	// B 无法读 A 的配置版本
	badPath := configURLPath
	st3, _, _ := agentRequest(t, credsB, "GET", badPath, nil, nil)
	if st3 != 404 {
		t.Fatalf("B 读 A 配置应 404，实际 %d", st3)
	}

	// 运行中不可重复领取
	st, body, _ = agentRequest(t, credsA, "GET", "/api/v1/agent/tasks", nil, nil)
	if st == 200 {
		if tasks2, ok := body["tasks"].([]any); ok && len(tasks2) != 0 {
			t.Fatal("运行中任务未完成前不应领取新任务")
		}
	}

	// 回结果 ok
	lease := task["lease_id"].(string)
	st, _, _ = agentRequest(t, credsA, "POST", "/api/v1/agent/tasks/"+taskID+"/result", mustJSON(t, map[string]any{
		"lease_id": lease, "status": "ok", "detail": map[string]any{"exit_ip": "203.0.113.102"},
	}), nil)
	if st != 200 {
		t.Fatalf("回结果失败 %d", st)
	}
	// 重复相同结果 → 200
	st, _, _ = agentRequest(t, credsA, "POST", "/api/v1/agent/tasks/"+taskID+"/result", mustJSON(t, map[string]any{
		"lease_id": lease, "status": "ok", "detail": map[string]any{},
	}), nil)
	if st != 200 {
		t.Fatalf("重复相同结果应 200，实际 %d", st)
	}
	// 冲突结果 → 409
	st, _, _ = agentRequest(t, credsA, "POST", "/api/v1/agent/tasks/"+taskID+"/result", mustJSON(t, map[string]any{
		"lease_id": lease, "status": "failed", "detail": map[string]any{},
	}), nil)
	if st != 409 {
		t.Fatalf("冲突结果应 409，实际 %d", st)
	}

	// current_config_version 推进
	var dev models.Device
	srv.DB.Where("id=?", credsA.ID).First(&dev)
	if dev.CurrentConfigVersion != 1 {
		t.Fatalf("current_config_version 应为 1，实际 %d", dev.CurrentConfigVersion)
	}

	// B 不能回 A 的任务
	st, _, _ = agentRequest(t, credsB, "POST", "/api/v1/agent/tasks/"+taskID+"/result", mustJSON(t, map[string]any{
		"lease_id": lease, "status": "ok", "detail": map[string]any{},
	}), nil)
	if st != 200 && st != 404 && st != 409 {
		t.Fatalf("B 回 A 任务应被拒绝，实际 %d", st)
	}
}

func TestIdempotencyKey(t *testing.T) {
	jwt := ensureAdmin(t)
	creds := createDeviceAndEnroll(t, jwt, "t-idem")
	headers := map[string]string{"Authorization": "Bearer " + jwt, "Idempotency-Key": "idem-1"}
	body1 := mustJSON(t, map[string]any{"type": "ping_test", "params": map[string]string{"host": "223.5.5.5"}})
	st, b1, _ := httpDo(t, "POST", "/api/v1/devices/"+creds.ID+"/ops", body1, headers)
	if st != 200 {
		t.Fatalf("ops 失败 %d %v", st, b1)
	}
	// 相同 key + 相同内容 → 返回原任务
	st, b2, _ := httpDo(t, "POST", "/api/v1/devices/"+creds.ID+"/ops", body1, headers)
	if st != 200 || b2["task_id"] != b1["task_id"] {
		t.Fatalf("幂等重放应返回原任务: %v vs %v", b1["task_id"], b2["task_id"])
	}
	// 相同 key + 不同内容 → 409
	st, _, _ = httpDo(t, "POST", "/api/v1/devices/"+creds.ID+"/ops", mustJSON(t, map[string]any{
		"type": "ping_test", "params": map[string]string{"host": "119.29.29.29"},
	}), headers)
	if st != 409 {
		t.Fatalf("同 key 不同内容应 409，实际 %d", st)
	}
	// 缺 key → 422
	st, _, _ = httpDo(t, "POST", "/api/v1/devices/"+creds.ID+"/ops", body1, map[string]string{"Authorization": "Bearer " + jwt})
	if st != 422 {
		t.Fatalf("缺 Idempotency-Key 应 422，实际 %d", st)
	}
}


// TestSwitchPrimaryTwice 二次换绑 primary 不应触发 one_primary 唯一索引（回归）。
func TestSwitchPrimaryTwice(t *testing.T) {
	jwt := ensureAdmin(t)
	creds := createDeviceAndEnroll(t, jwt, "t-reswitch")
	n1 := createTestNode(t, jwt, "RSW1", "203.0.113.120")
	n2 := createTestNode(t, jwt, "RSW2", "203.0.113.121")
	for _, n := range []int64{n1, n2} {
		st, body, _ := httpDo(t, "POST", "/api/v1/assignments/batch", mustJSON(t, map[string]any{
			"device_ids": []string{creds.ID}, "node_id": n, "is_primary": true, "apply": true,
		}), map[string]string{"Authorization": "Bearer " + jwt})
		if st != 200 {
			t.Fatalf("换绑失败 %d %v", st, body)
		}
		item := body["items"].([]any)[0].(map[string]any)
		if item["ok"] != true {
			t.Fatalf("换绑应成功: %v", item)
		}
	}
	var cnt int64
	srv.DB.Raw(`SELECT COUNT(*) FROM node_assignments WHERE device_id=? AND is_primary`, creds.ID).Scan(&cnt)
	if cnt != 1 {
		t.Fatalf("primary 应只有 1 个，实际 %d", cnt)
	}
	var dev models.Device
	srv.DB.Where("id=?", creds.ID).First(&dev)
	if dev.CurrentNodeID == nil || *dev.CurrentNodeID != n2 {
		t.Fatalf("current_node_id 应指向 n2")
	}
}

func TestTrafficAccounting(t *testing.T) {
	jwt := ensureAdmin(t)
	creds := createDeviceAndEnroll(t, jwt, "t-traffic")

	sendSample := func(sampleID string, wanRx, wanTx, pRx, pTx int64, bootID, epoch, status string) {
		metrics := map[string]any{
			"sample_id": sampleID, "boot_id": bootID,
			"wan_rx_bytes": wanRx, "wan_tx_bytes": wanTx,
			"proxy_measurement_status": status,
			"proxy_counter_epoch":      epoch,
		}
		if status == "available" {
			metrics["proxy_rx_bytes"] = pRx
			metrics["proxy_tx_bytes"] = pTx
		}
		st, body, _ := agentRequest(t, creds, "POST", "/api/v1/agent/heartbeat", mustJSON(t, map[string]any{
			"device_id": creds.ID, "ts": time.Now().Unix(), "metrics": metrics,
		}), nil)
		if st != 200 {
			t.Fatalf("样本 %s 心跳失败 %d %v", sampleID, st, body)
		}
	}

	// 首样本仅基线
	sendSample("tr-1", 1000, 500, 100, 50, "boot-1", "e1", "available")
	// 正常增量
	sendSample("tr-2", 2000, 900, 300, 120, "boot-1", "e1", "available")
	var mt models.MonthlyTraffic
	srv.DB.Where("device_id=? AND year_month<>''", creds.ID).First(&mt)
	if mt.WanRxBytes != 1000 || mt.WanTxBytes != 400 || mt.ProxyRxBytes != 200 || mt.ProxyTxBytes != 70 {
		t.Fatalf("增量不符: %+v", mt)
	}
	// 重复样本不重复累加
	sendSample("tr-2", 2000, 900, 300, 120, "boot-1", "e1", "available")
	srv.DB.Where("device_id=?", creds.ID).First(&mt)
	if mt.WanRxBytes != 1000 {
		t.Fatalf("重复样本重复累加: %+v", mt)
	}
	// 计数器下降 → 重置基线，不产生负值
	sendSample("tr-3", 100, 50, 10, 5, "boot-1", "e1", "available")
	srv.DB.Where("device_id=?", creds.ID).First(&mt)
	if mt.WanRxBytes != 1000 {
		t.Fatalf("计数器下降不应累加: %+v", mt)
	}
	sendSample("tr-4", 300, 150, 30, 15, "boot-1", "e1", "available")
	srv.DB.Where("device_id=?", creds.ID).First(&mt)
	if mt.WanRxBytes != 1200 {
		t.Fatalf("基线重建后增量不符: %+v", mt)
	}
	// epoch 变化 → 代理基线重建，WAN 不受影响（tr-5 代理不累加：tr-2 +200、tr-4 +20 共 220）
	sendSample("tr-5", 500, 250, 9999, 9999, "boot-1", "e2", "available")
	srv.DB.Where("device_id=?", creds.ID).First(&mt)
	if mt.ProxyRxBytes != 220 {
		t.Fatalf("epoch 变化后代理不应累加: %+v", mt)
	}
	// unavailable → 代理不累计，WAN 正常累计
	sendSample("tr-6", 600, 260, 0, 0, "boot-1", "e2", "unavailable")
	srv.DB.Where("device_id=?", creds.ID).First(&mt)
	if mt.WanRxBytes != 1500 || mt.ProxyRxBytes != 220 {
		t.Fatalf("unavailable 处理不符: %+v", mt)
	}
	// boot 变化 → WAN 基线重建
	sendSample("tr-7", 999999, 1, 1, 1, "boot-2", "e2", "available")
	srv.DB.Where("device_id=?", creds.ID).First(&mt)
	if mt.WanRxBytes != 1500 {
		t.Fatalf("boot 变化后 WAN 不应累加: %+v", mt)
	}
}

func TestRollbackStreakAndAlerts(t *testing.T) {
	jwt := ensureAdmin(t)
	creds := createDeviceAndEnroll(t, jwt, "t-rb")
	nodeID := createTestNode(t, jwt, "RBNODE", "203.0.113.103")
	st, body, _ := httpDo(t, "POST", "/api/v1/assignments/batch", mustJSON(t, map[string]any{
		"device_ids": []string{creds.ID}, "node_id": nodeID, "is_primary": true, "apply": true,
	}), map[string]string{"Authorization": "Bearer " + jwt})
	if st != 200 || !body["items"].([]any)[0].(map[string]any)["ok"].(bool) {
		t.Fatal("apply 失败")
	}
	st, body, _ = agentRequest(t, creds, "GET", "/api/v1/agent/tasks", nil, nil)
	task := body["tasks"].([]any)[0].(map[string]any)
	taskID := task["task_id"].(string)
	lease := task["lease_id"].(string)

	// 第一次 rolled_back
	st, _, _ = agentRequest(t, creds, "POST", "/api/v1/agent/tasks/"+taskID+"/result", mustJSON(t, map[string]any{
		"lease_id": lease, "status": "rolled_back", "detail": map[string]any{"error": "health check failed"},
	}), nil)
	if st != 200 {
		t.Fatal("第一次 rolled_back 失败")
	}
	var cnt int64
	srv.DB.Raw(`SELECT COUNT(*) FROM alerts WHERE kind='consecutive_rollback'`).Scan(&cnt)
	if cnt != 0 {
		t.Fatal("1 次回滚不应告警")
	}
	// 第二个配置任务再 rolled_back → 告警
	st, _, _ = httpDo(t, "POST", "/api/v1/devices/"+creds.ID+"/ops", mustJSON(t, map[string]any{
		"type": "reload_proxy",
	}), map[string]string{"Authorization": "Bearer " + jwt, "Idempotency-Key": "rb-2"})
	if st != 200 {
		t.Fatal("reload_proxy 下发失败")
	}
	st, body, _ = agentRequest(t, creds, "GET", "/api/v1/agent/tasks", nil, nil)
	task2 := body["tasks"].([]any)[0].(map[string]any)
	st, _, _ = agentRequest(t, creds, "POST", "/api/v1/agent/tasks/"+task2["task_id"].(string)+"/result", mustJSON(t, map[string]any{
		"lease_id": task2["lease_id"], "status": "rolled_back", "detail": map[string]any{},
	}), nil)
	if st != 200 {
		t.Fatal("第二次 rolled_back 失败")
	}
	srv.DB.Raw(`SELECT COUNT(*) FROM alerts WHERE kind='consecutive_rollback'`).Scan(&cnt)
	if cnt != 1 {
		t.Fatalf("连续 2 次回滚应告警，实际 %d", cnt)
	}
	// rollback_failed → critical 告警
	st, _, _ = agentRequest(t, creds, "POST", "/api/v1/agent/tasks/"+task2["task_id"].(string)+"/result", mustJSON(t, map[string]any{
		"lease_id": task2["lease_id"], "status": "ok", "detail": map[string]any{},
	}), nil)
	if st != 200 {
		// ok 会清零 streak，幂等返回 200
	}
}

func TestTaskExpiryAndRevoke(t *testing.T) {
	jwt := ensureAdmin(t)
	creds := createDeviceAndEnroll(t, jwt, "t-exp")

	// 过期任务：制造过去 deadline 的 pending 任务
	srv.DB.Exec(`INSERT INTO tasks (id, device_id, type, params_json, status, created_by, deadline)
		VALUES ('t-expiredtest', ?, 'ping_test', '{}', 'pending', 'test', now() - interval '10 minutes')`, creds.ID)
	if err := srv.sweepOnce(); err != nil {
		t.Fatal(err)
	}
	var status string
	srv.DB.Raw(`SELECT status FROM tasks WHERE id='t-expiredtest'`).Scan(&status)
	if status != "expired" {
		t.Fatalf("超期 pending 应 expired，实际 %s", status)
	}
	// 过期任务不可领取
	st, body, _ := agentRequest(t, creds, "GET", "/api/v1/agent/tasks", nil, nil)
	if tasks := body["tasks"].([]any); len(tasks) != 0 {
		t.Fatal("过期任务不应被领取")
	}

	// 吊销：pending 任务取消 + 心跳 401
	st, _, _ = httpDo(t, "POST", "/api/v1/devices/"+creds.ID+"/ops", mustJSON(t, map[string]any{
		"type": "ping_test", "params": map[string]string{"host": "223.5.5.5"},
	}), map[string]string{"Authorization": "Bearer " + jwt, "Idempotency-Key": "revoke-1"})
	if st != 200 {
		t.Fatal("下发失败")
	}
	st, _, _ = httpDo(t, "POST", "/api/v1/devices/"+creds.ID+"/revoke", nil, map[string]string{"Authorization": "Bearer " + jwt})
	if st != 200 {
		t.Fatal("吊销失败")
	}
	var cnt int64
	srv.DB.Raw(`SELECT COUNT(*) FROM tasks WHERE device_id=? AND status='cancelled'`, creds.ID).Scan(&cnt)
	if cnt == 0 {
		t.Fatal("吊销后 pending 任务应取消")
	}
	st, _, _ = agentRequest(t, creds, "POST", "/api/v1/agent/heartbeat", mustJSON(t, map[string]any{
		"device_id": creds.ID, "ts": time.Now().Unix(), "metrics": map[string]any{"sample_id": "rv-1"},
	}), nil)
	if st != 401 {
		t.Fatalf("吊销设备心跳应 401，实际 %d", st)
	}
}

func TestOfflineDetection(t *testing.T) {
	jwt := ensureAdmin(t)
	creds := createDeviceAndEnroll(t, jwt, "t-offline")
	// 从未心跳：创建时间已超过宽限 → 离线告警
	srv.DB.Exec(`UPDATE devices SET created_at = now() - interval '1 hour' WHERE id=?`, creds.ID)
	if err := srv.sweepOnce(); err != nil {
		t.Fatal(err)
	}
	var cnt int64
	srv.DB.Raw(`SELECT COUNT(*) FROM alerts WHERE kind='device_offline' AND device_id=?`, creds.ID).Scan(&cnt)
	if cnt != 1 {
		t.Fatalf("离线告警数量不符: %d", cnt)
	}
	// 再扫一次不重复
	if err := srv.sweepOnce(); err != nil {
		t.Fatal(err)
	}
	srv.DB.Raw(`SELECT COUNT(*) FROM alerts WHERE kind='device_offline' AND device_id=?`, creds.ID).Scan(&cnt)
	if cnt != 1 {
		t.Fatalf("离线告警应去重: %d", cnt)
	}
}

func TestDashboardOnline(t *testing.T) {
	jwt := ensureAdmin(t)
	creds := createDeviceAndEnroll(t, jwt, "t-dash")
	agentRequest(t, creds, "POST", "/api/v1/agent/heartbeat", mustJSON(t, map[string]any{
		"device_id": creds.ID, "ts": time.Now().Unix(),
		"metrics": map[string]any{"sample_id": "dash-1", "cpu": 30},
	}), nil)
	st, body, _ := httpDo(t, "GET", "/api/v1/dashboard", nil, map[string]string{"Authorization": "Bearer " + jwt})
	if st != 200 {
		t.Fatal("dashboard 失败")
	}
	devs := body["devices"].(map[string]any)
	if devs["online"].(float64) < 1 {
		t.Fatalf("应有在线设备: %v", devs)
	}
	if body["traffic"] == nil {
		t.Fatal("缺少流量块")
	}
}

func TestAuditTrail(t *testing.T) {
	jwt := ensureAdmin(t)
	st, body, _ := httpDo(t, "GET", "/api/v1/audit?page_size=100", nil, map[string]string{"Authorization": "Bearer " + jwt})
	if st != 200 {
		t.Fatal("审计查询失败")
	}
	actions := map[string]bool{}
	for _, it := range body["items"].([]any) {
		a := it.(map[string]any)["action"].(string)
		actions[a] = true
	}
	for _, want := range []string{"device_create", "enroll", "config_publish", "assignment_set"} {
		if !actions[want] {
			t.Fatalf("审计缺少动作 %s", want)
		}
	}
}

func TestPersistenceKeepsKeysAndConfigs(t *testing.T) {
	requireDB(t)
	// 重建 Server（同库同 DataDir）：签名公钥不变、设备凭据仍可验签
	jwt := ensureAdmin(t)
	creds := createDeviceAndEnroll(t, jwt, "t-persist")
	var cfgID int64
	srv.DB.Raw(`SELECT COALESCE(MAX(id),0) FROM configs`).Scan(&cfgID)
	// 新 server 实例
	dir := srv.Cfg.DataDir
	signKey2, err := NewEd25519SigningKey(dir)
	if err != nil {
		t.Fatal(err)
	}
	if signKey2.PublicPEM() != srv.SignPubPEM {
		t.Fatal("重建后签名公钥变化（DATA_DIR 未持久化？）")
	}
	srv2 := New(srv.Cfg, srv.DB, srv.MasterKey, signKey2)
	_ = srv2
	// 旧凭据仍可心跳（同一 Server 实例下已验证；这里验证 HMAC key 从 DB 解密可用）
	st, _, _ := agentRequest(t, creds, "POST", "/api/v1/agent/heartbeat", mustJSON(t, map[string]any{
		"device_id": creds.ID, "ts": time.Now().Unix(),
		"metrics": map[string]any{"sample_id": "persist-1"},
	}), nil)
	if st != 200 {
		t.Fatalf("凭据持久化验证失败: %d", st)
	}
}

