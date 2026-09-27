package httpserver

import (
	"net/http"
	"time"

	"github.com/gin-gonic/gin"

	"routermgmt/internal/models"
	"routermgmt/internal/traffic"
)

// handleDashboard 在线数/离线数、本月节点流量（突出）、节点分布、最近告警。
// 「本月代理流量」为设备侧统计/估算，不等同 VPS 账单（§19.5）。
func (s *Server) handleDashboard(c *gin.Context) {
	now := time.Now().UTC()
	cutoff := now.Add(-s.Cfg.HeartbeatTimeout)

	var devices []models.Device
	s.DB.Find(&devices)
	online, offline, pending, revoked := 0, 0, 0, 0
	for i := range devices {
		d := &devices[i]
		switch {
		case d.LifecycleStatus == models.LifecycleRevoked:
			revoked++
		case d.LifecycleStatus == models.LifecyclePending:
			pending++
		case d.LastHeartbeatAt != nil && d.LastHeartbeatAt.After(cutoff):
			online++
		default:
			offline++
		}
	}

	// 本月（Asia/Shanghai）每设备代理流量
	ym := traffic.MonthKey(now)
	var monthly []models.MonthlyTraffic
	s.DB.Where("year_month=?", ym).Find(&monthly)
	monthMap := map[string]models.MonthlyTraffic{}
	for _, m := range monthly {
		monthMap[m.DeviceID] = m
	}
	thresholdBytes := int64(s.Cfg.TrafficAlertGB * 1e9)
	type trafficItem struct {
		DeviceID   string   `json:"device_id"`
		Name       string   `json:"name"`
		ProxyBytes BytesStr `json:"proxy_bytes"`
		Threshold  BytesStr `json:"threshold_bytes"`
		Percent    float64  `json:"percent"`
		OverLimit  bool     `json:"over_limit"`
	}
	var trafficList []trafficItem
	var totalProxy int64
	for i := range devices {
		d := &devices[i]
		if d.LifecycleStatus == models.LifecycleRevoked {
			continue
		}
		m := monthMap[d.ID]
		used := m.ProxyRxBytes + m.ProxyTxBytes
		totalProxy += used
		pct := 0.0
		if thresholdBytes > 0 {
			pct = float64(used) * 100 / float64(thresholdBytes)
		}
		trafficList = append(trafficList, trafficItem{
			DeviceID: d.ID, Name: d.Name,
			ProxyBytes: BytesStr(used), Threshold: BytesStr(thresholdBytes),
			Percent: pct, OverLimit: used >= thresholdBytes,
		})
	}
	// 按用量降序
	for i := 0; i < len(trafficList); i++ {
		for j := i + 1; j < len(trafficList); j++ {
			if trafficList[j].ProxyBytes > trafficList[i].ProxyBytes {
				trafficList[i], trafficList[j] = trafficList[j], trafficList[i]
			}
		}
	}

	// 节点分布
	var nodes []models.Node
	s.DB.Order("priority DESC, id").Find(&nodes)
	type nodeItem struct {
		ID           int64  `json:"id"`
		Name         string `json:"name"`
		Host         string `json:"host"`
		Enabled      bool   `json:"enabled"`
		HealthStatus string `json:"health_status"`
		LastLatencyMs *int64 `json:"last_latency_ms"`
		BoundDevices int64  `json:"bound_devices"`
		ActiveDevices int64 `json:"active_devices"`
	}
	nodeItems := make([]nodeItem, 0, len(nodes))
	for _, n := range nodes {
		var bound, active int64
		s.DB.Model(&models.NodeAssignment{}).Where("node_id=?", n.ID).Count(&bound)
		s.DB.Model(&models.Device{}).Where("current_node_id=?", n.ID).Count(&active)
		nodeItems = append(nodeItems, nodeItem{
			ID: n.ID, Name: n.Name, Host: n.Host, Enabled: n.Enabled,
			HealthStatus: n.HealthStatus, LastLatencyMs: n.LastLatencyMs,
			BoundDevices: bound, ActiveDevices: active,
		})
	}

	// 最近告警（未确认优先）
	var recent []models.Alert
	s.DB.Order("acked_at IS NULL DESC, created_at DESC").Limit(10).Find(&recent)
	alertItems := make([]gin.H, 0, len(recent))
	for _, a := range recent {
		alertItems = append(alertItems, gin.H{
			"id": a.ID, "kind": a.Kind, "device_id": a.DeviceID, "message": a.Message,
			"level": a.Level, "created_at": a.CreatedAt, "acked": a.AckedAt != nil,
		})
	}

	c.JSON(http.StatusOK, gin.H{
		"devices": gin.H{"total": len(devices), "online": online, "offline": offline, "pending": pending, "revoked": revoked},
		"traffic": gin.H{
			"month": ym,
			"note":  "本月代理流量（设备侧统计/估算），不等同 VPS 账单",
			"total_proxy_bytes":   BytesStr(totalProxy),
			"threshold_bytes":     BytesStr(thresholdBytes),
			"threshold_gb":        s.Cfg.TrafficAlertGB,
			"items":               trafficList,
		},
		"nodes":         nodeItems,
		"recent_alerts": alertItems,
		"server_time":   now,
	})
}

// ---- 告警 ----

func (s *Server) handleAlertList(c *gin.Context) {
	page, size, offset := pageArgs(c)
	q := s.DB.Model(&models.Alert{})
	if v := c.Query("device_id"); v != "" {
		q = q.Where("device_id=?", v)
	}
	switch c.Query("state") {
	case "acked":
		q = q.Where("acked_at IS NOT NULL")
	case "active":
		q = q.Where("acked_at IS NULL")
	}
	var total int64
	q.Count(&total)
	var alerts []models.Alert
	q.Order("created_at DESC").Offset(offset).Limit(size).Find(&alerts)
	items := make([]gin.H, 0, len(alerts))
	for _, a := range alerts {
		items = append(items, gin.H{
			"id": a.ID, "kind": a.Kind, "device_id": a.DeviceID, "message": a.Message,
			"level": a.Level, "created_at": a.CreatedAt, "acked_at": a.AckedAt, "acked_by": a.AckedBy,
		})
	}
	c.JSON(http.StatusOK, pageResp(items, total, page, size))
}

func (s *Server) handleAlertAck(c *gin.Context) {
	a := adminFrom(c)
	var alert models.Alert
	if err := s.DB.Where("id=?", c.Param("id")).First(&alert).Error; err != nil {
		fail404(c, "告警不存在")
		return
	}
	now := time.Now().UTC()
	if err := s.DB.Model(&alert).Updates(map[string]any{"acked_at": &now, "acked_by": a.Username}).Error; err != nil {
		fail500(c, "确认失败")
		return
	}
	s.audit(c, "admin", a.Username, "alert_ack", "alert:"+c.Param("id"), nil)
	c.JSON(http.StatusOK, gin.H{"ok": true})
}

// ---- 审计（append-only，无删除 API）----

func (s *Server) handleAuditList(c *gin.Context) {
	page, size, offset := pageArgs(c)
	q := s.DB.Model(&models.AuditLog{})
	if v := c.Query("action"); v != "" {
		q = q.Where("action=?", v)
	}
	if v := c.Query("target"); v != "" {
		q = q.Where("target=?", v)
	}
	var total int64
	q.Count(&total)
	var logs []models.AuditLog
	q.Order("ts DESC, id DESC").Offset(offset).Limit(size).Find(&logs)
	items := make([]gin.H, 0, len(logs))
	for _, l := range logs {
		items = append(items, gin.H{
			"id": l.ID, "actor_type": l.ActorType, "actor_id": l.ActorID,
			"action": l.Action, "target": l.Target, "detail": jsonRaw(l.DetailJSON),
			"ip": l.IP, "ts": l.TS,
		})
	}
	c.JSON(http.StatusOK, pageResp(items, total, page, size))
}
