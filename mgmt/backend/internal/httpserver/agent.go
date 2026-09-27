package httpserver

// 设备 HMAC 认证（任务书 §7.1）：
//   Authorization: HMAC-SHA256 <hex>
//   canonical = device_id \n timestamp \n nonce \n METHOD \n path \n sha256hex(body)
//   key = 64 个小写 hex 字符的 ASCII 字节（不解码）
//   时间窗 ±120s；验签通过后原子占用 (device_id, nonce)；常量时间比较。

import (
	"bytes"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"time"

	"github.com/gin-gonic/gin"
	"gorm.io/gorm"

	"routermgmt/internal/crypto"
	"routermgmt/internal/models"
	"routermgmt/internal/traffic"
)

const hmacTimeWindow = 120 * time.Second

var reNonce = regexp.MustCompile(`^[0-9a-f]{32}$`)

// CanonicalString 构造 HMAC 签名原文（Go/ash 测试向量共用）。
func CanonicalString(deviceID string, ts int64, nonce, method, path, bodySHA256Hex string) []byte {
	return []byte(fmt.Sprintf("%s\n%d\n%s\n%s\n%s\n%s", deviceID, ts, nonce, method, path, bodySHA256Hex))
}

// SignCanonical 用 HMAC key（64 hex 的 ASCII 字节）签名，返回小写 hex。
func SignCanonical(keyASCII string, canonical []byte) string {
	mac := hmac.New(sha256.New, []byte(keyASCII))
	mac.Write(canonical)
	return hex.EncodeToString(mac.Sum(nil))
}

// VerifyDeviceHMAC 校验签名（常量时间比较）。keyASCII 是解密后的明文 key。
func VerifyDeviceHMAC(keyASCII, deviceID string, ts int64, nonce, method, path string, body []byte, sigHex string) bool {
	if !reNonce.MatchString(nonce) {
		return false
	}
	bodyHash := sha256.Sum256(body)
	canonical := CanonicalString(deviceID, ts, nonce, method, path, hex.EncodeToString(bodyHash[:]))
	expect := SignCanonical(keyASCII, canonical)
	return hmac.Equal([]byte(expect), []byte(sigHex))
}

func (s *Server) deviceHMAC(c *gin.Context) {
	// Agent 接口禁止 query 参数（§7.1）
	if c.Request.URL.RawQuery != "" {
		fail401(c, "Agent 接口不接受 query 参数")
		return
	}
	deviceID := c.GetHeader("X-Device-Id")
	tsStr := c.GetHeader("X-Timestamp")
	nonce := c.GetHeader("X-Nonce")
	authz := c.GetHeader("Authorization")
	if deviceID == "" || tsStr == "" || nonce == "" {
		fail401(c, "缺少设备认证头")
		return
	}
	if !reDeviceID.MatchString(deviceID) {
		fail401(c, "设备 ID 不合法")
		return
	}
	if !reNonce.MatchString(nonce) {
		fail401(c, "nonce 必须是 32 个小写 hex 字符")
		return
	}
	if len(authz) != len("HMAC-SHA256 ")+64 || authz[:12] != "HMAC-SHA256 " {
		fail401(c, "Authorization 格式应为 HMAC-SHA256 <64hex>")
		return
	}
	sigHex := authz[12:]
	ts, err := strconv.ParseInt(tsStr, 10, 64)
	if err != nil {
		fail401(c, "时间戳不合法")
		return
	}
	now := time.Now().UTC()
	if d := now.Sub(time.Unix(ts, 0)); d > hmacTimeWindow || d < -hmacTimeWindow {
		fail401(c, "时间戳超出 ±120s 窗口")
		return
	}

	var dev models.Device
	if err := s.DB.Where("id = ?", deviceID).First(&dev).Error; err != nil {
		fail401(c, "设备不存在")
		return
	}
	if dev.LifecycleStatus != models.LifecycleApproved {
		fail401(c, "设备未批准或已吊销")
		return
	}
	keyASCII, err := crypto.DecryptString(s.MasterKey, dev.AgentHMACKeyEnc, "devices:"+dev.ID+":agent_hmac_key")
	if err != nil || len(keyASCII) != 64 {
		fail401(c, "设备凭据不可用")
		return
	}

	body, err := io.ReadAll(c.Request.Body)
	if err != nil {
		fail401(c, "读取请求体失败")
		return
	}
	c.Request.Body = io.NopCloser(bytes.NewReader(body))

	if !VerifyDeviceHMAC(keyASCII, deviceID, ts, nonce, c.Request.Method, c.Request.URL.Path, body, sigHex) {
		fail401(c, "签名不匹配")
		return
	}

	// 验签通过后原子占用 nonce（重复请求拒绝；保留 ≥300s，持久化防重启清空）
	expiry := now.Add(600 * time.Second)
	res := s.DB.Exec(`INSERT INTO used_nonces (device_id, nonce, expires_at) VALUES (?,?,?) ON CONFLICT DO NOTHING`,
		deviceID, nonce, expiry)
	if res.Error != nil || res.RowsAffected == 0 {
		fail401(c, "重复的 nonce（疑似重放）")
		return
	}

	c.Set("device", &dev)
	c.Set("device_key", keyASCII)
	c.Next()
}

func deviceFrom(c *gin.Context) *models.Device {
	v, _ := c.Get("device")
	d, _ := v.(*models.Device)
	return d
}

var reDeviceID = regexp.MustCompile(`^rtr-[0-9a-z]{2,16}$`)

// ---- 心跳 ----

type metricPayload struct {
	CPU          *float64              `json:"cpu"`
	MemUsedMB    *int64                `json:"mem_used_mb"`
	MemTotalMB   *int64                `json:"mem_total_mb"`
	TempC        *float64              `json:"temp_c"`
	DiskFreeMB   *int64                `json:"disk_free_mb"`
	UptimeS      *int64                `json:"uptime_s"`
	WanIP        string                `json:"wan_ip"`
	ExitIP       string                `json:"exit_ip"`
	ExitNode     string                `json:"exit_node"`
	ExitLatencyMs *int64               `json:"exit_latency_ms"`
	ClashAlive   *bool                 `json:"clash_alive"`
	WGAlive      *bool                 `json:"wg_alive"`
	WanRxBytes   *int64                `json:"wan_rx_bytes"`
	WanTxBytes   *int64                `json:"wan_tx_bytes"`
	SampleID     string                `json:"sample_id"`
	BootID       string                `json:"boot_id"`
	ProxyCounterEpoch string           `json:"proxy_counter_epoch"`
	ProxyMeasurementStatus string      `json:"proxy_measurement_status"`
	ProxyRxBytes *int64                `json:"proxy_rx_bytes"`
	ProxyTxBytes *int64                `json:"proxy_tx_bytes"`
	ConfigVersion int64                `json:"config_version"`
}

type heartbeatReq struct {
	DeviceID string         `json:"device_id"`
	TS       int64          `json:"ts"`
	TaskID   string         `json:"task_id,omitempty"`
	LeaseID  string         `json:"lease_id,omitempty"`
	Metrics  metricPayload  `json:"metrics"`
}

func (s *Server) handleHeartbeat(c *gin.Context) {
	dev := deviceFrom(c)
	var req heartbeatReq
	if err := c.ShouldBindJSON(&req); err != nil {
		fail422(c, "心跳报文不合法")
		return
	}
	if req.DeviceID != dev.ID {
		fail403(c, "body device_id 与认证身份不一致")
		return
	}
	now := time.Now().UTC()
	m := &models.Metric{
		DeviceID: dev.ID, TS: now,
		CPU: req.Metrics.CPU, MemUsedMB: req.Metrics.MemUsedMB, MemTotalMB: req.Metrics.MemTotalMB,
		TempC: req.Metrics.TempC, DiskFreeMB: req.Metrics.DiskFreeMB, UptimeS: req.Metrics.UptimeS,
		WanIP: req.Metrics.WanIP, ExitIP: req.Metrics.ExitIP, ExitNode: req.Metrics.ExitNode,
		ExitLatencyMs: req.Metrics.ExitLatencyMs, ClashAlive: req.Metrics.ClashAlive, WGAlive: req.Metrics.WGAlive,
		WanRxBytes: req.Metrics.WanRxBytes, WanTxBytes: req.Metrics.WanTxBytes,
		ProxyRxBytes: req.Metrics.ProxyRxBytes, ProxyTxBytes: req.Metrics.ProxyTxBytes,
		ConfigVersion: req.Metrics.ConfigVersion,
	}
	if req.Metrics.SampleID == "" {
		fail422(c, "sample_id 必填（用于心跳去重）")
		return
	}
	m.SampleID = req.Metrics.SampleID
	m.BootID = req.Metrics.BootID
	m.ProxyCounterEpoch = req.Metrics.ProxyCounterEpoch
	switch req.Metrics.ProxyMeasurementStatus {
	case "available", "partial":
		m.ProxyMeasurementStatus = req.Metrics.ProxyMeasurementStatus
	default:
		m.ProxyMeasurementStatus = "unavailable"
	}

	var accepted bool
	var monthProxyTotal int64
	var oldExitIP string
	err := s.DB.Transaction(func(tx *gorm.DB) error {
		var e error
		accepted, monthProxyTotal, e = s.traffic.Ingest(tx, m)
		if e != nil {
			return e
		}
		if !accepted {
			return nil // 重复样本：不重复累加
		}
		oldExitIP = dev.CurrentExitIP
		updates := map[string]any{
			"last_heartbeat_at": now,
			"updated_at":        now,
			"current_exit_ip":   req.Metrics.ExitIP,
		}
		// 第一次上线只记一次；期限起算若还空，也用这次上线时间
		if dev.FirstOnlineAt == nil {
			updates["first_online_at"] = now
		}
		if dev.LicenseStartedAt == nil {
			updates["license_started_at"] = now
		}
		if e := tx.Model(&models.Device{}).Where("id = ?", dev.ID).Updates(updates).Error; e != nil {
			return e
		}
		// 出口 IP 变化告警：第一次观测不告警（§7.3）
		if req.Metrics.ExitIP != "" && oldExitIP != "" && req.Metrics.ExitIP != oldExitIP {
			s.alerts.Raise(tx, "exit_ip_changed", &dev.ID, "warning",
				fmt.Sprintf("设备 %s 出口 IP 变化 %s -> %s", dev.ID, oldExitIP, req.Metrics.ExitIP),
				fmt.Sprintf("exit_ip_changed:%s", dev.ID))
		}
		// 运行中任务租约续期（不延长硬 deadline，§19.2）
		if req.TaskID != "" && req.LeaseID != "" {
			tx.Exec(`UPDATE tasks SET lease_expires_at = ? WHERE id=? AND device_id=? AND status='running' AND lease_id=?`,
				now.Add(120*time.Second), req.TaskID, dev.ID, req.LeaseID)
		}
		return nil
	})
	if err != nil {
		fail500(c, "心跳入库失败")
		return
	}
	// 流量阈值告警（阈值外检查，失败不影响心跳）
	if accepted && monthProxyTotal > 0 {
		threshold := int64(s.Cfg.TrafficAlertGB * 1e9)
		if monthProxyTotal >= threshold {
			s.DB.Transaction(func(tx *gorm.DB) error {
				s.alerts.Raise(tx, "traffic_threshold", &dev.ID, "warning",
					fmt.Sprintf("设备 %s 本月节点流量 %.1f GB 已达阈值 %.0f GB", dev.ID, float64(monthProxyTotal)/1e9, s.Cfg.TrafficAlertGB),
					fmt.Sprintf("traffic:%s:%s", dev.ID, traffic.MonthKey(now)))
				return nil
			})
		}
	}
	var fresh models.Device
	if err := s.DB.Where("id=?", dev.ID).First(&fresh).Error; err != nil {
		fresh = *dev
	}
	c.JSON(http.StatusOK, gin.H{"ok": true, "server_ts": now.Unix(), "policy": s.devicePolicy(&fresh)})
}

// ---- 任务领取 / 结果 ----

func (s *Server) handleAgentTasks(c *gin.Context) {
	dev := deviceFrom(c)
	var claimed *models.Task
	err := s.DB.Transaction(func(tx *gorm.DB) error {
		// 一台设备最多一个有副作用的运行任务（§19.2）
		var running int64
		if err := tx.Raw(`SELECT COUNT(*) FROM tasks WHERE device_id=? AND status='running'`, dev.ID).Scan(&running).Error; err != nil {
			return err
		}
		if running > 0 {
			return nil
		}
		var t models.Task
		err := tx.Raw(`SELECT * FROM tasks WHERE device_id=? AND status='pending' AND deadline > now()
			ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED`, dev.ID).Scan(&t).Error
		if err != nil {
			return err
		}
		if t.ID == "" {
			return nil // 没有可领取任务（Scan 空结果不报错，需判空）
		}
		lease, _ := crypto.GenerateHex(16)
		now := time.Now().UTC()
		// 租约窗口 = min(120s, 任务截止)：reboot 这类长任务（deadline 600s）租约覆盖
		// 整个设备离线周期，重启后的 boot_id 证据才不会被 409 拒掉
		leaseExpire := now.Add(120 * time.Second)
		if t.Deadline.Before(leaseExpire) {
			leaseExpire = t.Deadline
		}
		if err := tx.Exec(`UPDATE tasks SET status='running', lease_id=?, claimed_at=?, lease_expires_at=? WHERE id=? AND status='pending'`,
			lease, now, leaseExpire, t.ID).Error; err != nil {
			return err
		}
		t.Status = models.TaskRunning
		t.LeaseID = lease
		claimed = &t
		return nil
	})
	if err != nil {
		fail500(c, "领取任务失败")
		return
	}
	if claimed == nil {
		c.JSON(http.StatusOK, gin.H{"tasks": []any{}})
		return
	}
	item := gin.H{
		"task_id":        claimed.ID,
		"type":           claimed.Type,
		"params":         jsonRaw(claimed.ParamsJSON),
		"lease_id":       claimed.LeaseID,
		"deadline":       claimed.Deadline.Unix(),
		"config_version": nil,
	}
	if claimed.ConfigVersion != nil {
		item["config_version"] = *claimed.ConfigVersion
		// 任务不保存含 token 的 URL，按需构造 HMAC 下载路径（§19.3）
		item["config_url"] = s.Cfg.MgmtPublicURL + "/api/v1/agent/configs/" + strconv.FormatInt(*claimed.ConfigVersion, 10)
		if claimed.ConfigID != nil {
			var cfg models.Config
			if err := s.DB.Where("device_id=? AND version=?", dev.ID, *claimed.ConfigVersion).First(&cfg).Error; err == nil {
				item["signature"] = cfg.Signature
				item["config_sha256"] = cfg.SHA256
			}
		}
	}
	c.JSON(http.StatusOK, gin.H{"tasks": []gin.H{item}})
}

func jsonRaw(s string) map[string]any {
	var m map[string]any
	if err := jsonUnmarshalStr(s, &m); err != nil {
		return map[string]any{}
	}
	return m
}

func (s *Server) handleAgentTaskResult(c *gin.Context) {
	dev := deviceFrom(c)
	taskID := c.Param("id")
	var req struct {
		LeaseID string          `json:"lease_id" binding:"required"`
		Status  string          `json:"status" binding:"required"`
		Detail  map[string]any  `json:"detail"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		fail422(c, "结果报文不合法")
		return
	}
	switch req.Status {
	case models.TaskOK, models.TaskFailed, models.TaskRolledBack:
	default:
		fail422(c, "status 只能是 ok|failed|rolled_back")
		return
	}
	resultJSON, _ := jsonMarshal(map[string]any{"status": req.Status, "detail": req.Detail})
	now := time.Now().UTC()
	var conflict bool
	err := s.DB.Transaction(func(tx *gorm.DB) error {
		var t models.Task
		if err := tx.Where("id = ? AND device_id = ?", taskID, dev.ID).First(&t).Error; err != nil {
			return errNotFoundTask
		}
		// 已有最终结果：相同结果幂等成功，冲突 409
		if isFinalStatus(t.Status) {
			var prev map[string]any
			jsonUnmarshalStr(t.ResultJSON, &prev)
			if prev["status"] == req.Status {
				conflict = false
				return nil
			}
			conflict = true
			return nil
		}
		if t.Status != models.TaskRunning && t.Status != models.TaskUnknown {
			return errNotReportable
		}
		if t.LeaseID != "" && req.LeaseID != t.LeaseID {
			return errLeaseMismatch
		}
		if err := tx.Model(&models.Task{}).Where("id=?", t.ID).Updates(map[string]any{
			"status":      req.Status,
			"result_json": resultJSON,
			"executed_at": now,
		}).Error; err != nil {
			return err
		}
		s.applyTaskSideEffects(tx, &t, req.Status, req.Detail)
		return nil
	})
	if err != nil {
		switch err {
		case errNotFoundTask:
			fail404(c, "任务不存在或不属于该设备")
		case errLeaseMismatch:
			fail409(c, "lease_id 不匹配")
		case errNotReportable:
			fail409(c, "任务状态不接受结果回传")
		default:
			fail500(c, "保存结果失败")
		}
		return
	}
	if conflict {
		fail409(c, "任务已有不同的最终结果")
		return
	}
	c.JSON(http.StatusOK, gin.H{"ok": true})
}

func isFinalStatus(s string) bool {
	switch s {
	case models.TaskOK, models.TaskFailed, models.TaskRolledBack, models.TaskExpired, models.TaskCancelled:
		return true
	}
	return false
}

var (
	errNotFoundTask  = errors.New("task not found")
	errLeaseMismatch = errors.New("lease mismatch")
	errNotReportable = errors.New("not reportable")
)

// applyTaskSideEffects 结果副作用：版本推进 / 回滚计数 / 告警（同事务）。
func (s *Server) applyTaskSideEffects(tx *gorm.DB, t *models.Task, status string, detail map[string]any) {
	isConfigTask := t.Type == "switch_node" || t.Type == "reload_proxy"
	now := time.Now().UTC()
	// 先读当前 streak（更新前），保证「连续 2 次」判定不受同事务更新影响
	var devBefore models.Device
	tx.Where("id=?", t.DeviceID).First(&devBefore)
	updates := map[string]any{"updated_at": now}
	newStreak := devBefore.RollbackStreak
	switch {
	case status == models.TaskOK && isConfigTask && t.ConfigVersion != nil:
		updates["current_config_version"] = *t.ConfigVersion
		if *t.ConfigVersion > 0 {
			updates["highest_accepted_version"] = gorm.Expr("GREATEST(highest_accepted_version, ?)", *t.ConfigVersion)
		}
		updates["rollback_streak"] = 0
		newStreak = 0
	case status == models.TaskRolledBack && isConfigTask:
		updates["rollback_streak"] = gorm.Expr("rollback_streak + 1")
		newStreak = devBefore.RollbackStreak + 1
	}
	tx.Model(&models.Device{}).Where("id=?", t.DeviceID).Updates(updates)

	if status == models.TaskRolledBack && isConfigTask && newStreak >= 2 {
		s.alerts.Raise(tx, "consecutive_rollback", &t.DeviceID, "critical",
			fmt.Sprintf("设备 %s 连续 %d 次配置回滚，请检查节点或配置", t.DeviceID, newStreak),
			fmt.Sprintf("consecutive_rollback:%s", t.DeviceID))
	}
	if rollbackFailed, _ := detail["rollback_failed"].(bool); rollbackFailed {
		s.alerts.Raise(tx, "rollback_failed", &t.DeviceID, "critical",
			fmt.Sprintf("设备 %s 任务 %s 回滚失败，需要人工介入", t.DeviceID, t.ID),
			fmt.Sprintf("rollback_failed:%s:%s", t.DeviceID, t.ID))
	}
	// 任务带出的出口 IP 变化，关联任务 ID（§7.3）
	if exitIP, _ := detail["exit_ip"].(string); exitIP != "" {
		var dev models.Device
		if err := tx.Where("id=?", t.DeviceID).First(&dev).Error; err == nil {
			if dev.CurrentExitIP != "" && dev.CurrentExitIP != exitIP {
				s.alerts.Raise(tx, "exit_ip_changed", &dev.ID, "info",
					fmt.Sprintf("设备 %s 出口 IP 变化 %s -> %s（任务 %s）", dev.ID, dev.CurrentExitIP, exitIP, t.ID),
					fmt.Sprintf("exit_ip_changed:%s:%s", dev.ID, t.ID))
			}
			tx.Model(&models.Device{}).Where("id=?", dev.ID).Update("current_exit_ip", exitIP)
		}
	}
}

// ---- 配置下载（仅本设备指定版本）----

// handleAgentBundle GET /api/v1/agent/bundles/:version —— agent 升级包下发（HMAC 鉴权 + Ed25519 按设备签名）。
// 签名载荷与配置签名同构：sha256(bundle) + "\n" + device_id + "\n" + version，agent 端复用同一验签函数。
func (s *Server) handleAgentBundle(c *gin.Context) {
	dev := deviceFrom(c)
	versionStr := c.Param("version")
	var version int64
	if _, err := fmt.Sscanf(versionStr, "%d", &version); err != nil || version <= 0 {
		fail422(c, "版本号不合法")
		return
	}
	data, err := os.ReadFile(filepath.Join(s.Cfg.AgentBundleDir, fmt.Sprintf("agent-%d.tar.gz", version)))
	if err != nil {
		fail404(c, "升级包不存在")
		return
	}
	sig, err := s.signing.Sign(data, dev.ID, version)
	if err != nil {
		fail500(c, "签名失败")
		return
	}
	c.Header("X-Bundle-Version", strconv.FormatInt(version, 10))
	c.Header("X-Signature", sig)
	c.Header("Cache-Control", "no-store")
	c.Data(http.StatusOK, "application/gzip", data)
}

// latestAgentBundle 扫描升级包目录取最大版本号；无包返回 0。
func latestAgentBundle(dir string) int64 {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return 0
	}
	var best int64
	for _, e := range entries {
		var v int64
		if _, err := fmt.Sscanf(e.Name(), "agent-%d.tar.gz", &v); err == nil && v > best {
			best = v
		}
	}
	return best
}

func (s *Server) handleAgentConfig(c *gin.Context) {
	dev := deviceFrom(c)
	versionStr := c.Param("version")
	var version int64
	if _, err := fmt.Sscanf(versionStr, "%d", &version); err != nil || version <= 0 {
		fail422(c, "版本号不合法")
		return
	}
	var cfg models.Config
	if err := s.DB.Where("device_id = ? AND version = ?", dev.ID, version).First(&cfg).Error; err != nil {
		fail404(c, "配置版本不存在")
		return
	}
	content, err := crypto.Decrypt(s.MasterKey, cfg.ContentEnc, fmt.Sprintf("configs:%d:content", cfg.ID))
	if err != nil {
		fail500(c, "配置解密失败")
		return
	}
	c.Header("X-Config-Version", strconv.FormatInt(cfg.Version, 10))
	c.Header("X-Signature", cfg.Signature)
	c.Header("Cache-Control", "no-store")
	c.Header("Content-Type", "text/yaml; charset=utf-8")
	c.Data(http.StatusOK, "text/yaml; charset=utf-8", content)
}
