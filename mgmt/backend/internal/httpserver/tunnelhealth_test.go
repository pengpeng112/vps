package httpserver

// tunnelhealth_test.go — /api/v1/internal/tunnel-health 集成测试（2026-09-24 阶段1C）。
// 覆盖：内部令牌鉴权（含未配置时 404）、字段落库、3 次阈值告警、去重、
// 恢复自动 ack、修复任务下发与幂等。
// 不经管理员 API：设备行直接经 GORM 造/读，避免与套件内其它测试的登录态耦合。

import (
	"testing"
	"time"

	"routermgmt/internal/models"
)

func TestTunnelHealthReportFlow(t *testing.T) {
	if intgDSN == "" {
		t.Skip("需要 ROUTERMGMT_TEST_DSN")
	}
	creds := models.Device{
		ID: "rtr-tunneltest1", Name: "tunnel-test-rtr",
		LifecycleStatus: models.LifecycleApproved, WGIp: "10.10.0.99",
	}
	if err := srv.DB.Create(&creds).Error; err != nil {
		t.Fatalf("造设备失败: %v", err)
	}
	defer srv.DB.Delete(&models.Device{}, "id=?", creds.ID)

	orig := srv.Cfg.InternalToken
	defer func() { srv.Cfg.InternalToken = orig }()
	srv.Cfg.InternalToken = "test-internal-token"

	hdr := map[string]string{"X-Internal-Token": "test-internal-token"}

	// 0) 鉴权负向：错 token / 无 token / 未配置（404）
	st, body, _ := httpDo(t, "POST", "/api/v1/internal/tunnel-health",
		mustJSON(t, map[string]any{"device_id": creds.ID, "port": 18222}),
		map[string]string{"X-Internal-Token": "wrong"})
	if st != 401 {
		t.Fatalf("错误 token 应 401，got %d %v", st, body)
	}
	st, body, _ = httpDo(t, "POST", "/api/v1/internal/tunnel-health",
		mustJSON(t, map[string]any{"device_id": creds.ID, "port": 18222}), nil)
	if st != 401 {
		t.Fatalf("缺 token 应 401，got %d %v", st, body)
	}
	srv.Cfg.InternalToken = ""
	st, body, _ = httpDo(t, "POST", "/api/v1/internal/tunnel-health",
		mustJSON(t, map[string]any{"device_id": creds.ID, "port": 18222}), hdr)
	if st != 404 {
		t.Fatalf("未配置 INTERNAL_TOKEN 应 404（fail-closed），got %d %v", st, body)
	}
	srv.Cfg.InternalToken = "test-internal-token"

	// 1) 失败 1~2 次：只更新字段，不产生告警、不下发修复
	for i := 1; i <= 2; i++ {
		st, body, _ = httpDo(t, "POST", "/api/v1/internal/tunnel-health", mustJSON(t, map[string]any{
			"device_id": creds.ID, "port": 18222, "alive": false,
			"consecutive_failures": i, "request_repair": true,
		}), hdr)
		if st != 200 || body["ok"] != true {
			t.Fatalf("上报失败 %d %v", st, body)
		}
		if _, has := body["task_id"]; has {
			t.Fatalf("未达阈值不应下发修复任务: %v", body)
		}
	}
	var alertCount int64
	srv.DB.Model(&models.Alert{}).Where("device_id=?", creds.ID).Count(&alertCount)
	if alertCount != 0 {
		t.Fatalf("未达阈值不应产生告警，got %d", alertCount)
	}

	// 2) 第 3 次失败：产生 critical 告警 + 修复任务
	firstFail := time.Now().UTC().Add(-90 * time.Second)
	st, body, _ = httpDo(t, "POST", "/api/v1/internal/tunnel-health", mustJSON(t, map[string]any{
		"device_id": creds.ID, "port": 18222, "alive": false,
		"consecutive_failures": 3, "first_failure_at": firstFail,
		"request_repair": true, "repair_idem_key": "tunnel-repair-test-1",
	}), hdr)
	if st != 200 {
		t.Fatalf("上报失败 %d %v", st, body)
	}
	if _, has := body["task_id"]; !has {
		t.Fatalf("达阈值应下发修复任务: %v", body)
	}
	alertCount = 0
	srv.DB.Model(&models.Alert{}).Where("device_id=? AND kind=? AND acked_at IS NULL", creds.ID, "remote_ssh_down").Count(&alertCount)
	if alertCount != 1 {
		t.Fatalf("应恰好 1 条未确认 remote_ssh_down 告警，got %d", alertCount)
	}

	// 3) 持续失败重复上报：告警去重（仍 1 条），同幂等键修复任务不重复
	st, body, _ = httpDo(t, "POST", "/api/v1/internal/tunnel-health", mustJSON(t, map[string]any{
		"device_id": creds.ID, "port": 18222, "alive": false,
		"consecutive_failures": 4, "request_repair": true,
		"repair_idem_key": "tunnel-repair-test-1",
	}), hdr)
	if st != 200 {
		t.Fatalf("上报失败 %d %v", st, body)
	}
	alertCount = 0
	srv.DB.Model(&models.Alert{}).Where("device_id=? AND kind=? AND acked_at IS NULL", creds.ID, "remote_ssh_down").Count(&alertCount)
	if alertCount != 1 {
		t.Fatalf("重复上报应去重为 1 条，got %d", alertCount)
	}
	var taskCount int64
	srv.DB.Model(&models.Task{}).Where("device_id=? AND type='repair_mgmt_tunnel'", creds.ID).Count(&taskCount)
	if taskCount != 1 {
		t.Fatalf("同幂等键应只生成 1 个修复任务，got %d", taskCount)
	}

	// 4) 设备字段已落库
	var dev models.Device
	if err := srv.DB.Where("id=?", creds.ID).First(&dev).Error; err != nil {
		t.Fatal(err)
	}
	if dev.RemoteSSHAlive == nil || *dev.RemoteSSHAlive {
		t.Fatalf("remote_ssh_alive 应为 false: %v", dev.RemoteSSHAlive)
	}
	if dev.LastRemoteSSHCheckAt == nil {
		t.Fatalf("last_remote_ssh_check_at 应非空")
	}

	// 5) 恢复：字段翻转 + 告警自动 ack 关闭环
	st, body, _ = httpDo(t, "POST", "/api/v1/internal/tunnel-health", mustJSON(t, map[string]any{
		"device_id": creds.ID, "port": 18222, "alive": true,
	}), hdr)
	if st != 200 {
		t.Fatalf("恢复上报失败 %d %v", st, body)
	}
	alertCount = 0
	srv.DB.Model(&models.Alert{}).Where("device_id=? AND kind=? AND acked_at IS NULL", creds.ID, "remote_ssh_down").Count(&alertCount)
	if alertCount != 0 {
		t.Fatalf("恢复后告警应被自动确认，剩 %d 条未确认", alertCount)
	}
	var ackedBy string
	srv.DB.Raw(`SELECT acked_by FROM alerts WHERE device_id=? AND kind='remote_ssh_down' ORDER BY id DESC LIMIT 1`, creds.ID).Scan(&ackedBy)
	if ackedBy != "tunnel-probe" {
		t.Fatalf("告警应被 tunnel-probe 自动确认，got %q", ackedBy)
	}
	if err := srv.DB.Where("id=?", creds.ID).First(&dev).Error; err != nil {
		t.Fatal(err)
	}
	if dev.RemoteSSHAlive == nil || !*dev.RemoteSSHAlive {
		t.Fatalf("恢复后 remote_ssh_alive 应为 true: %v", dev.RemoteSSHAlive)
	}

	// 6) 修复任务类型与参数核对（白名单任务 repair_mgmt_tunnel）
	var taskType, params string
	srv.DB.Raw(`SELECT type FROM tasks WHERE device_id=? ORDER BY created_at DESC LIMIT 1`, creds.ID).Scan(&taskType)
	if taskType != "repair_mgmt_tunnel" {
		t.Fatalf("任务类型应为 repair_mgmt_tunnel，got %q", taskType)
	}
	srv.DB.Raw(`SELECT params_json FROM tasks WHERE device_id=? ORDER BY created_at DESC LIMIT 1`, creds.ID).Scan(&params)
	if params == "" {
		t.Fatalf("任务参数不应为空")
	}
}
