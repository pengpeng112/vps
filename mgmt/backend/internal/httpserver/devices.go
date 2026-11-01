package httpserver

// 设备管理：创建（发一次性 enroll_code）、enroll、列表/详情/编辑、
// revoke / rotate / enroll-reset、运维任务下发、指标查询。
// 密钥口径：明文凭据只在创建/enroll/rotate 响应中出现一次（§7.2）。

import (
	"regexp"
	"errors"
	"fmt"
	"net"
	"net/http"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
	"gorm.io/gorm"

	"routermgmt/internal/clash"
	"routermgmt/internal/crypto"
	"routermgmt/internal/models"
)

const (
	wgSubnetPrefix = "10.10.0."
	wgIPMin        = 10
	wgIPMax        = 250
	enrollCodeTTL  = 30 * time.Minute
)

// allocWGIP 分配 10.10.0.10~250 中最小的空闲 IP（与 device_id 绑定后不再变）。
func (s *Server) allocWGIP(tx *gorm.DB) (string, error) {
	var used []string
	if err := tx.Raw(`SELECT wg_ip FROM devices`).Scan(&used).Error; err != nil {
		return "", err
	}
	taken := map[string]bool{}
	for _, ip := range used {
		taken[ip] = true
	}
	for i := wgIPMin; i <= wgIPMax; i++ {
		candidate := fmt.Sprintf("%s%d", wgSubnetPrefix, i)
		if !taken[candidate] {
			return candidate, nil
		}
	}
	return "", errors.New("设备地址池 10.10.0.10~250 已耗尽")
}

func (s *Server) handleDeviceCreate(c *gin.Context) {
	a := adminFrom(c)
	var req struct {
		Name         string `json:"name" binding:"required"`
		LocationNote string `json:"location_note"`
		MacAddr      string `json:"mac_addr"`
		GroupID      *int64 `json:"group_id"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		fail422(c, "name 必填")
		return
	}
	enrollCode, err := crypto.GenerateEnrollCode()
	if err != nil {
		fail500(c, "生成注册码失败")
		return
	}
	var dev models.Device
	err = s.DB.Transaction(func(tx *gorm.DB) error {
		// 生成唯一设备 ID
		for attempt := 0; attempt < 5; attempt++ {
			idHex, _ := crypto.GenerateHex(3)
			id := "rtr-" + idHex
			var cnt int64
			tx.Model(&models.Device{}).Where("id=?", id).Count(&cnt)
			if cnt == 0 {
				dev.ID = id
				break
			}
		}
		if dev.ID == "" {
			return errors.New("生成设备 ID 失败")
		}
		ip, err := s.allocWGIP(tx)
		if err != nil {
			return err
		}
		dev.Name = req.Name
		dev.LocationNote = req.LocationNote
		dev.MacAddr = req.MacAddr
		dev.GroupID = req.GroupID
		dev.WGIp = ip
		dev.LifecycleStatus = models.LifecyclePending
		dev.EnrollCodeHash = crypto.SHA256Hex([]byte(enrollCode))
		exp := time.Now().UTC().Add(enrollCodeTTL)
		dev.EnrollCodeExpiresAt = &exp
		if err := tx.Create(&dev).Error; err != nil {
			return err
		}
		return auditTx(tx, "admin", a.Username, "device_create", "device:"+dev.ID, gin.H{"name": req.Name, "wg_ip": ip})
	})
	if err != nil {
		fail500(c, "创建设备失败")
		return
	}
	c.JSON(http.StatusOK, gin.H{
		"id":                     dev.ID,
		"name":                   dev.Name,
		"enroll_code":            enrollCode,
		"wg_ip":                  dev.WGIp,
		"enroll_code_expires_at": dev.EnrollCodeExpiresAt,
		"hint":                   "注册码 30 分钟内有效，仅此一次显示；用 provision CLI 代设备 enroll",
	})
}

// handleEnroll 设备凭 enroll_code 换取凭据（无 HMAC）。成功后注册码作废；
// 数据库原子 UPDATE 保证并发 enroll 只有一个成功（§19.1）。
func (s *Server) handleEnroll(c *gin.Context) {
	var req struct {
		EnrollCode  string `json:"enroll_code" binding:"required"`
		HWModel     string `json:"hw_model"`
		FirmwareVer string `json:"firmware_ver"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		fail422(c, "enroll_code 必填")
		return
	}
	if !crypto.ValidEnrollCodeHash(req.EnrollCode) {
		fail422(c, "enroll_code 格式不合法")
		return
	}
	codeHash := crypto.SHA256Hex([]byte(req.EnrollCode))

	var hmacKey, subToken, wgPriv, wgPub string
	var dev models.Device
	err := s.DB.Transaction(func(tx *gorm.DB) error {
		if err := tx.Raw(`SELECT * FROM devices WHERE enroll_code_hash=? LIMIT 1 FOR UPDATE`, codeHash).Scan(&dev).Error; err != nil {
			return errInvalidCode
		}
		if dev.ID == "" {
			return errInvalidCode
		}
		if dev.EnrollCodeExpiresAt == nil || dev.EnrollCodeExpiresAt.Before(time.Now().UTC()) {
			return errExpiredCode
		}
		if dev.LifecycleStatus == models.LifecycleRevoked {
			return errRevoked
		}
		var err error
		hmacKey, err = crypto.GenerateHMACKeyHex()
		if err != nil {
			return err
		}
		subToken, err = crypto.GenerateSubToken()
		if err != nil {
			return err
		}
		wgPriv, wgPub, err = crypto.GenerateWGKeypair()
		if err != nil {
			return err
		}
		hmacEnc, err := crypto.EncryptString(s.MasterKey, hmacKey, "devices:"+dev.ID+":agent_hmac_key")
		if err != nil {
			return err
		}
		wgEnc, err := crypto.EncryptString(s.MasterKey, wgPriv, "devices:"+dev.ID+":wg_priv")
		if err != nil {
			return err
		}
		res := tx.Exec(`UPDATE devices SET
			enroll_code_hash='', enroll_code_expires_at=NULL,
			agent_hmac_key_enc=?, sub_token_hash=?, wg_priv_enc=?, wg_pubkey=?,
			hw_model=?, firmware_ver=?, lifecycle_status='approved', updated_at=now()
			WHERE id=? AND enroll_code_hash=?`,
			hmacEnc, crypto.SHA256Hex([]byte(subToken)), wgEnc, wgPub,
			req.HWModel, req.FirmwareVer, dev.ID, codeHash)
		if res.Error != nil {
			return res.Error
		}
		if res.RowsAffected == 0 {
			return errInvalidCode // 并发竞争失败方
		}
		return auditTx(tx, "device", dev.ID, "enroll", "device:"+dev.ID, gin.H{"hw_model": req.HWModel, "firmware_ver": req.FirmwareVer})
	})
	if err != nil {
		switch err {
		case errInvalidCode:
			fail401(c, "注册码无效或已被使用")
		case errExpiredCode:
			fail401(c, "注册码已过期，请管理员重置")
		case errRevoked:
			fail403(c, "设备已吊销")
		default:
			fail500(c, "enroll 失败")
		}
		return
	}

	resp := gin.H{
		"device_id":  dev.ID,
		"hmac_key":   hmacKey,
		"sub_token":  subToken,
		"sub_url":    fmt.Sprintf("%s/sub/%s?token=%s", s.Cfg.MgmtPublicURL, dev.ID, subToken),
		"sign_public_key": s.SignPubPEM,
		"transport_mode":  s.transportMode(),
		"wg": gin.H{
			"private_key":            wgPriv,
			"address":                dev.WGIp + "/32",
			"peer_public_key":        s.Cfg.WGHubPublicKey,
			"endpoint":               s.Cfg.WGHubEndpoint,
			"allowed_ips":            "10.10.0.0/24",
			"persistent_keepalive":   25,
		},
	}
	c.JSON(http.StatusOK, resp)
}

var (
	errInvalidCode = errors.New("invalid enroll code")
	errExpiredCode = errors.New("expired enroll code")
	errRevoked     = errors.New("revoked")
)

func (s *Server) transportMode() string {
	if s.Cfg.WGHubPublicKey == "" || s.Cfg.WGHubEndpoint == "" {
		return "mock"
	}
	return "wireguard"
}

// handleEnrollReset enroll 响应丢失时：管理员重置 pending 注册并重新生成注册码（§19.1）。
func (s *Server) handleEnrollReset(c *gin.Context) {
	a := adminFrom(c)
	id := c.Param("id")
	var dev models.Device
	if err := s.DB.Where("id=?", id).First(&dev).Error; err != nil {
		fail404(c, "设备不存在")
		return
	}
	if dev.LifecycleStatus != models.LifecyclePending {
		fail409(c, "只有 pending 设备可重置注册码（已 enroll 的请用 rotate）")
		return
	}
	enrollCode, _ := crypto.GenerateEnrollCode()
	exp := time.Now().UTC().Add(enrollCodeTTL)
	err := s.DB.Transaction(func(tx *gorm.DB) error {
		if err := tx.Model(&models.Device{}).Where("id=?", id).Updates(map[string]any{
			"enroll_code_hash":       crypto.SHA256Hex([]byte(enrollCode)),
			"enroll_code_expires_at": exp,
			// 清掉可能残留的半套凭据（响应丢失场景）
			"agent_hmac_key_enc": "", "sub_token_hash": "", "wg_priv_enc": "", "wg_pubkey": "",
			"updated_at": time.Now().UTC(),
		}).Error; err != nil {
			return err
		}
		return auditTx(tx, "admin", a.Username, "enroll_reset", "device:"+id, nil)
	})
	if err != nil {
		fail500(c, "重置失败")
		return
	}
	c.JSON(http.StatusOK, gin.H{"id": id, "enroll_code": enrollCode, "enroll_code_expires_at": exp})
}

// ---- 列表 / 详情 / 编辑 ----

func (s *Server) isOnline(dev *models.Device) bool {
	return dev.LifecycleStatus == models.LifecycleApproved &&
		dev.LastHeartbeatAt != nil && time.Since(*dev.LastHeartbeatAt) < s.Cfg.HeartbeatTimeout
}

type deviceDTO struct {
	ID                    string     `json:"id"`
	Name                  string     `json:"name"`
	GroupID               *int64     `json:"group_id"`
	GroupName             string     `json:"group_name,omitempty"`
	WGIp                  string     `json:"wg_ip"`
	HWModel               string     `json:"hw_model"`
	FirmwareVer           string     `json:"firmware_ver"`
	MacAddr               string     `json:"mac_addr"`
	LocationNote          string     `json:"location_note"`
	LifecycleStatus       string     `json:"lifecycle_status"`
	Online                bool       `json:"online"`
	LastHeartbeatAt       *time.Time `json:"last_heartbeat_at"`
	CurrentNodeID         *int64     `json:"current_node_id"`
	CurrentNodeName       string     `json:"current_node_name,omitempty"`
	CurrentExitIP         string     `json:"current_exit_ip"`
	DesiredConfigVersion  int64      `json:"desired_config_version"`
	CurrentConfigVersion  int64      `json:"current_config_version"`
	HighestAcceptedVersion int64     `json:"highest_accepted_version"`
	VpsLimitKbps          int        `json:"vps_limit_kbps"`
	RebootCron            string     `json:"reboot_cron"`
	LicenseEnabled        bool       `json:"license_enabled"`
	LicenseExpiresAt      *time.Time `json:"license_expires_at"`
	ServiceActive         bool       `json:"service_active"`
	ProxyMode             string     `json:"proxy_mode"`
	AccessMode            string     `json:"access_mode"`
	AccessList            string     `json:"access_list"`
	FirstOnlineAt         *time.Time `json:"first_online_at"`
	LicenseStartedAt      *time.Time `json:"license_started_at"`
	LicenseDaysLeft       *int       `json:"license_days_left"`
	RemoteSSHAlive        *bool      `json:"remote_ssh_alive"`
	LastRemoteSSHCheckAt  *time.Time `json:"last_remote_ssh_check_at"`
	CreatedAt             time.Time  `json:"created_at"`
}

// 不返回 enc / 凭据字段（§9）
func (s *Server) deviceToDTO(dev *models.Device, nodeName, groupName string) deviceDTO {
	return deviceDTO{
		ID: dev.ID, Name: dev.Name, GroupID: dev.GroupID, GroupName: groupName,
		WGIp: dev.WGIp, HWModel: dev.HWModel, FirmwareVer: dev.FirmwareVer,
		MacAddr: dev.MacAddr, LocationNote: dev.LocationNote, LifecycleStatus: dev.LifecycleStatus,
		Online: s.isOnline(dev), LastHeartbeatAt: dev.LastHeartbeatAt,
		CurrentNodeID: dev.CurrentNodeID, CurrentNodeName: nodeName,
		CurrentExitIP: dev.CurrentExitIP,
		DesiredConfigVersion: dev.DesiredConfigVersion, CurrentConfigVersion: dev.CurrentConfigVersion,
		HighestAcceptedVersion: dev.HighestAcceptedVersion,
		VpsLimitKbps: dev.VpsLimitKbps, RebootCron: dev.RebootCron,
		LicenseEnabled: dev.LicenseEnabled, LicenseExpiresAt: dev.LicenseExpiresAt,
		ServiceActive: deviceServiceActive(dev, time.Now().UTC()),
		ProxyMode: orDefault(dev.ProxyMode, "rule"), AccessMode: orDefault(dev.AccessMode, "all"),
		AccessList: dev.AccessList,
		FirstOnlineAt: dev.FirstOnlineAt, LicenseStartedAt: dev.LicenseStartedAt,
		LicenseDaysLeft: licenseDaysLeft(dev, time.Now().UTC()),
		RemoteSSHAlive: dev.RemoteSSHAlive, LastRemoteSSHCheckAt: dev.LastRemoteSSHCheckAt,
		CreatedAt: dev.CreatedAt,
	}
}

func (s *Server) handleDeviceList(c *gin.Context) {
	page, size, offset := pageArgs(c)
	q := s.DB.Model(&models.Device{})
	if v := c.Query("group_id"); v != "" {
		q = q.Where("group_id = ?", v)
	}
	if v := c.Query("status"); v != "" {
		cutoff := time.Now().UTC().Add(-s.Cfg.HeartbeatTimeout)
		switch v {
		case "online":
			q = q.Where("lifecycle_status='approved' AND last_heartbeat_at > ?", cutoff)
		case "offline":
			q = q.Where("lifecycle_status='approved' AND (last_heartbeat_at IS NULL OR last_heartbeat_at <= ?)", cutoff)
		case "pending":
			q = q.Where("lifecycle_status='pending'")
		case "revoked":
			q = q.Where("lifecycle_status='revoked'")
		}
	}
	var total int64
	q.Count(&total)
	var devices []models.Device
	if err := q.Order("created_at DESC").Offset(offset).Limit(size).Find(&devices).Error; err != nil {
		fail500(c, "查询失败")
		return
	}
	groups := map[int64]string{}
	var gs []models.DeviceGroup
	s.DB.Find(&gs)
	for _, g := range gs {
		groups[g.ID] = g.Name
	}
	nodes := map[int64]string{}
	var ns []models.Node
	s.DB.Find(&ns)
	for _, n := range ns {
		nodes[n.ID] = n.Name
	}
	items := make([]deviceDTO, 0, len(devices))
	for i := range devices {
		d := &devices[i]
		gname, nodeName := "", ""
		if d.GroupID != nil {
			gname = groups[*d.GroupID]
		}
		if d.CurrentNodeID != nil {
			nodeName = nodes[*d.CurrentNodeID]
		}
		items = append(items, s.deviceToDTO(d, nodeName, gname))
	}
	c.JSON(http.StatusOK, pageResp(items, total, page, size))
}

func (s *Server) handleDeviceGet(c *gin.Context) {
	var dev models.Device
	if err := s.DB.Where("id=?", c.Param("id")).First(&dev).Error; err != nil {
		fail404(c, "设备不存在")
		return
	}
	nodeName, groupName := "", ""
	if dev.CurrentNodeID != nil {
		var n models.Node
		if err := s.DB.First(&n, *dev.CurrentNodeID).Error; err == nil {
			nodeName = n.Name
		}
	}
	if dev.GroupID != nil {
		var g models.DeviceGroup
		if err := s.DB.First(&g, *dev.GroupID).Error; err == nil {
			groupName = g.Name
		}
	}
	// 附带绑定节点列表（掩码）
	type boundNode struct {
		ID int64 `json:"id"`
		Name string `json:"name"`
		IsPrimary bool `json:"is_primary"`
		Enabled bool `json:"enabled"`
		HealthStatus string `json:"health_status"`
		Host string `json:"host"`
	}
	var bounds []boundNode
	s.DB.Raw(`SELECT n.id, n.name, a.is_primary, n.enabled, n.health_status, n.host
		FROM node_assignments a JOIN nodes n ON n.id=a.node_id
		WHERE a.device_id=? ORDER BY a.is_primary DESC, a.failover_priority, n.id`, dev.ID).Scan(&bounds)

	latest := s.latestMetric(dev.ID)
	dto := s.deviceToDTO(&dev, nodeName, groupName)
	c.JSON(http.StatusOK, gin.H{
		"device":         dto,
		"bound_nodes":    bounds,
		"latest_metric":  latest,
	})
}

func (s *Server) latestMetric(deviceID string) gin.H {
	var m models.Metric
	if err := s.DB.Where("device_id=?", deviceID).Order("ts DESC").First(&m).Error; err != nil {
		return nil
	}
	return gin.H{
		"ts": m.TS, "cpu": m.CPU, "mem_used_mb": m.MemUsedMB, "mem_total_mb": m.MemTotalMB,
		"temp_c": m.TempC, "disk_free_mb": m.DiskFreeMB, "uptime_s": m.UptimeS,
		"wan_ip": m.WanIP, "exit_ip": m.ExitIP, "exit_node": m.ExitNode,
		"exit_latency_ms": m.ExitLatencyMs, "clash_alive": m.ClashAlive, "wg_alive": m.WGAlive,
		"wan_rx_bytes": BytesStr(ptrVal(m.WanRxBytes)), "wan_tx_bytes": BytesStr(ptrVal(m.WanTxBytes)),
		"proxy_rx_bytes": BytesStr(ptrVal(m.ProxyRxBytes)), "proxy_tx_bytes": BytesStr(ptrVal(m.ProxyTxBytes)),
		"proxy_measurement_status": m.ProxyMeasurementStatus,
		"config_version": m.ConfigVersion, "sample_id": m.SampleID,
	}
}

func ptrVal(p *int64) int64 {
	if p == nil {
		return 0
	}
	return *p
}

func (s *Server) handleDevicePatch(c *gin.Context) {
	a := adminFrom(c)
	var dev models.Device
	if err := s.DB.Where("id=?", c.Param("id")).First(&dev).Error; err != nil {
		fail404(c, "设备不存在")
		return
	}
	var req struct {
		Name             *string `json:"name"`
		MacAddr          *string `json:"mac_addr"`
		LocationNote     *string `json:"location_note"`
		GroupID          *int64  `json:"group_id"`
		VpsLimitKbps     *int    `json:"vps_limit_kbps"`
		RebootCron       *string `json:"reboot_cron"`
		TrafficAlertHourlyMB *int `json:"traffic_alert_hourly_mb"`
		LicenseEnabled   *bool   `json:"license_enabled"`
		LicenseExpiresAt *string `json:"license_expires_at"` // RFC3339；空字符串=永不过期
		LicenseDays      *int    `json:"license_days"`       // 从现在起 N 天（与 expires_at 二选一）
		ProxyMode        *string `json:"proxy_mode"`
		AccessMode       *string `json:"access_mode"`
		AccessList       *string `json:"access_list"`
		FirstOnlineAt    *string `json:"first_online_at"`    // RFC3339；省略=不改
		LicenseStartedAt *string `json:"license_started_at"` // RFC3339；省略=不改
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		fail422(c, "报文不合法")
		return
	}
	updates := map[string]any{"updated_at": time.Now().UTC()}
	if req.Name != nil {
		updates["name"] = *req.Name
	}
	if req.MacAddr != nil {
		if !validMacAddr(*req.MacAddr) {
			fail422(c, "mac_addr 只允许十六进制与 : - 分隔（≤32 字符，空=清除）")
			return
		}
		updates["mac_addr"] = *req.MacAddr
	}
	if req.LocationNote != nil {
		updates["location_note"] = *req.LocationNote
	}
	if req.GroupID != nil {
		updates["group_id"] = *req.GroupID
	}
	if req.VpsLimitKbps != nil {
		if !validKbps(*req.VpsLimitKbps) {
			fail422(c, "vps_limit_kbps 范围 0..1000000（0=不限速）")
			return
		}
		updates["vps_limit_kbps"] = *req.VpsLimitKbps
	}
	if req.RebootCron != nil {
		if !validRebootCron(*req.RebootCron) {
			fail422(c, "reboot_cron 不在白名单（空/每天0点/每天8点/每8小时/每12小时）")
			return
		}
		updates["reboot_cron"] = *req.RebootCron
	}
	if req.TrafficAlertHourlyMB != nil {
		if *req.TrafficAlertHourlyMB < 0 || *req.TrafficAlertHourlyMB > 10000000 {
			fail422(c, "traffic_alert_hourly_mb 范围 0..10000000（0=关闭）")
			return
		}
		updates["traffic_alert_hourly_mb"] = *req.TrafficAlertHourlyMB
	}
	if req.LicenseEnabled != nil {
		updates["license_enabled"] = *req.LicenseEnabled
	}
	if req.LicenseDays != nil {
		if *req.LicenseDays < 1 || *req.LicenseDays > 3650 {
			fail422(c, "license_days 范围 1..3650")
			return
		}
		now := time.Now().UTC()
		exp := now.Add(time.Duration(*req.LicenseDays) * 24 * time.Hour)
		updates["license_expires_at"] = exp
		updates["license_started_at"] = now
		updates["license_enabled"] = true
	} else if req.LicenseExpiresAt != nil {
		if *req.LicenseExpiresAt == "" {
			updates["license_expires_at"] = nil
		} else {
			t, err := time.Parse(time.RFC3339, *req.LicenseExpiresAt)
			if err != nil {
				fail422(c, "license_expires_at 必须是 RFC3339")
				return
			}
			updates["license_expires_at"] = t.UTC()
		}
	}
	if req.ProxyMode != nil {
		if !validProxyMode(*req.ProxyMode) {
			fail422(c, "proxy_mode 只能是 rule / global / direct")
			return
		}
		updates["proxy_mode"] = *req.ProxyMode
	}
	if req.AccessList != nil {
		norm, err := normalizeAccessList(*req.AccessList)
		if err != nil {
			fail422(c, err.Error())
			return
		}
		updates["access_list"] = norm
		dev.AccessList = norm
	}
	if req.AccessMode != nil {
		if !validAccessMode(*req.AccessMode) {
			fail422(c, "access_mode 只能是 all / whitelist / blacklist")
			return
		}
		list := dev.AccessList
		if v, ok := updates["access_list"].(string); ok {
			list = v
		}
		if *req.AccessMode == "whitelist" && list == "" {
			fail422(c, errAccessEmpty.Error())
			return
		}
		updates["access_mode"] = *req.AccessMode
	}
	if req.FirstOnlineAt != nil && *req.FirstOnlineAt != "" {
		t, err := time.Parse(time.RFC3339, *req.FirstOnlineAt)
		if err != nil {
			fail422(c, "first_online_at 必须是 RFC3339")
			return
		}
		updates["first_online_at"] = t.UTC()
	}
	if req.LicenseStartedAt != nil && *req.LicenseStartedAt != "" {
		t, err := time.Parse(time.RFC3339, *req.LicenseStartedAt)
		if err != nil {
			fail422(c, "license_started_at 必须是 RFC3339")
			return
		}
		updates["license_started_at"] = t.UTC()
	}
	if err := s.DB.Model(&dev).Updates(updates).Error; err != nil {
		fail500(c, "保存失败")
		return
	}
	s.audit(c, "admin", a.Username, "device_update", "device:"+dev.ID, req)

	policyChanged := req.VpsLimitKbps != nil || req.RebootCron != nil || req.LicenseEnabled != nil ||
		req.LicenseExpiresAt != nil || req.LicenseDays != nil ||
		req.ProxyMode != nil || req.AccessMode != nil || req.AccessList != nil
	resp := gin.H{"ok": true}
	if policyChanged && dev.LifecycleStatus == models.LifecycleApproved {
		if err := s.DB.Where("id=?", dev.ID).First(&dev).Error; err == nil {
			if taskID, err := s.enqueueApplyPolicy(&dev, a.Username); err == nil && taskID != "" {
				resp["task_id"] = taskID
			}
		}
	}
	c.JSON(http.StatusOK, resp)
}

// handleDeviceRevoke 吊销：立即拒绝 HMAC/token/enroll，取消未执行任务；
// WG Peer 删除排队在 Session 2（Hub 就绪后），当前记待办并告警（§19.6）。
func (s *Server) handleDeviceRevoke(c *gin.Context) {
	a := adminFrom(c)
	id := c.Param("id")
	err := s.DB.Transaction(func(tx *gorm.DB) error {
		var dev models.Device
		if err := tx.Where("id=?", id).First(&dev).Error; err != nil {
			return errNotFound
		}
		if dev.LifecycleStatus == models.LifecycleRevoked {
			return errAlreadyRevoked
		}
		if err := tx.Model(&dev).Updates(map[string]any{
			"lifecycle_status": models.LifecycleRevoked,
			"enroll_code_hash": "", "sub_token_hash": "",
			"updated_at": time.Now().UTC(),
		}).Error; err != nil {
			return err
		}
		// 未执行任务全部取消
		if err := tx.Exec(`UPDATE tasks SET status='cancelled' WHERE device_id=? AND status IN ('pending')`, id).Error; err != nil {
			return err
		}
		if err := auditTx(tx, "admin", a.Username, "device_revoke", "device:"+id, nil); err != nil {
			return err
		}
		// WG Peer 删除待 Session 2 Hub 同步（不宣称完成）
		s.alerts.Raise(tx, "wg_peer_delete_pending", &id, "info",
			fmt.Sprintf("设备 %s 已吊销；WG Peer 删除待 Session 2 Hub 同步", id),
			fmt.Sprintf("wg_peer_delete_pending:%s", id))
		return nil
	})
	if err != nil {
		switch err {
		case errNotFound:
			fail404(c, "设备不存在")
		case errAlreadyRevoked:
			fail409(c, "设备已是吊销状态")
		default:
			fail500(c, "吊销失败")
		}
		return
	}
	c.JSON(http.StatusOK, gin.H{"ok": true, "note": "管理凭据已全部吊销；代理 UUID 状态独立（§19.6）"})
}

var errAlreadyRevoked = errors.New("already revoked")

// handleDeviceRotate 离线重新引导：生成新引导包，旧管理凭据立即作废。
// 设备会暂时失联，需通过可信 LAN 重新导入（§19.6）。
func (s *Server) handleDeviceRotate(c *gin.Context) {
	a := adminFrom(c)
	id := c.Param("id")
	hmacKey, _ := crypto.GenerateHMACKeyHex()
	subToken, _ := crypto.GenerateSubToken()
	wgPriv, wgPub, _ := crypto.GenerateWGKeypair()
	var dev models.Device
	err := s.DB.Transaction(func(tx *gorm.DB) error {
		if err := tx.Where("id=?", id).First(&dev).Error; err != nil {
			return errNotFound
		}
		if dev.LifecycleStatus != models.LifecycleApproved {
			return errNotApproved
		}
		hmacEnc, err := crypto.EncryptString(s.MasterKey, hmacKey, "devices:"+dev.ID+":agent_hmac_key")
		if err != nil {
			return err
		}
		wgEnc, err := crypto.EncryptString(s.MasterKey, wgPriv, "devices:"+dev.ID+":wg_priv")
		if err != nil {
			return err
		}
		if err := tx.Model(&dev).Updates(map[string]any{
			"agent_hmac_key_enc": hmacEnc,
			"sub_token_hash":     crypto.SHA256Hex([]byte(subToken)),
			"wg_priv_enc":        wgEnc, "wg_pubkey": wgPub,
			"last_heartbeat_at":  nil,
			"updated_at":         time.Now().UTC(),
		}).Error; err != nil {
			return err
		}
		// 旧任务不可再用旧凭据回传；清理运行中租约
		tx.Exec(`UPDATE tasks SET status='unknown' WHERE device_id=? AND status IN ('pending','running')`, id)
		return auditTx(tx, "admin", a.Username, "device_rotate", "device:"+id, nil)
	})
	if err != nil {
		switch err {
		case errNotFound:
			fail404(c, "设备不存在")
		case errNotApproved:
			fail409(c, "仅 approved 设备可轮换")
		default:
			fail500(c, "轮换失败")
		}
		return
	}
	c.JSON(http.StatusOK, gin.H{
		"device_id": dev.ID,
		"hmac_key":  hmacKey,
		"sub_token": subToken,
		"sub_url":   fmt.Sprintf("%s/sub/%s?token=%s", s.Cfg.MgmtPublicURL, dev.ID, subToken),
		"sign_public_key": s.SignPubPEM,
		"transport_mode":  s.transportMode(),
		"wg": gin.H{
			"private_key": wgPriv, "address": dev.WGIp + "/32",
			"peer_public_key": s.Cfg.WGHubPublicKey, "endpoint": s.Cfg.WGHubEndpoint,
			"allowed_ips": "10.10.0.0/24", "persistent_keepalive": 25,
		},
		"warning": "设备将暂时失联；请通过可信 LAN 重新导入离线引导包",
	})
}

var errNotApproved = errors.New("not approved")

// ---- 指标查询 ----

func (s *Server) handleDeviceMetrics(c *gin.Context) {
	var dev models.Device
	if err := s.DB.Where("id=?", c.Param("id")).First(&dev).Error; err != nil {
		fail404(c, "设备不存在")
		return
	}
	rng := c.DefaultQuery("range", "1h")
	var since time.Time
	switch rng {
	case "1h":
		since = time.Now().UTC().Add(-time.Hour)
	case "24h":
		since = time.Now().UTC().Add(-24 * time.Hour)
	case "7d":
		since = time.Now().UTC().Add(-7 * 24 * time.Hour)
	default:
		fail422(c, "range 只能是 1h|24h|7d")
		return
	}
	var ms []models.Metric
	if err := s.DB.Where("device_id=? AND ts>?", dev.ID, since).Order("ts ASC").Limit(2000).Find(&ms).Error; err != nil {
		fail500(c, "查询失败")
		return
	}
	items := make([]gin.H, 0, len(ms))
	for i := range ms {
		m := &ms[i]
		items = append(items, gin.H{
			"ts": m.TS.Unix(), "cpu": m.CPU,
			"mem_used_mb": m.MemUsedMB, "mem_total_mb": m.MemTotalMB, "temp_c": m.TempC,
			"exit_latency_ms": m.ExitLatencyMs,
			"wan_rx_bytes": BytesStr(ptrVal(m.WanRxBytes)), "wan_tx_bytes": BytesStr(ptrVal(m.WanTxBytes)),
			"proxy_rx_bytes": BytesStr(ptrVal(m.ProxyRxBytes)), "proxy_tx_bytes": BytesStr(ptrVal(m.ProxyTxBytes)),
			"proxy_measurement_status": m.ProxyMeasurementStatus,
			"clash_alive": m.ClashAlive, "wg_alive": m.WGAlive,
			"exit_ip": m.ExitIP, "wan_ip": m.WanIP,
		})
	}
	c.JSON(http.StatusOK, gin.H{"items": items, "range": rng})
}

// ---- 分组 ----

func (s *Server) handleGroupList(c *gin.Context) {
	page, size, offset := pageArgs(c)
	var total int64
	s.DB.Model(&models.DeviceGroup{}).Count(&total)
	var gs []models.DeviceGroup
	s.DB.Order("id").Offset(offset).Limit(size).Find(&gs)
	items := make([]gin.H, 0, len(gs))
	for _, g := range gs {
		var cnt int64
		s.DB.Model(&models.Device{}).Where("group_id=?", g.ID).Count(&cnt)
		items = append(items, gin.H{"id": g.ID, "name": g.Name, "description": g.Description, "device_count": cnt, "created_at": g.CreatedAt})
	}
	c.JSON(http.StatusOK, pageResp(items, total, page, size))
}

func (s *Server) handleGroupCreate(c *gin.Context) {
	a := adminFrom(c)
	var req struct {
		Name        string `json:"name" binding:"required"`
		Description string `json:"description"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		fail422(c, "name 必填")
		return
	}
	g := models.DeviceGroup{Name: req.Name, Description: req.Description}
	if err := s.DB.Create(&g).Error; err != nil {
		fail409(c, "分组名已存在")
		return
	}
	s.audit(c, "admin", a.Username, "group_create", fmt.Sprintf("group:%d", g.ID), nil)
	c.JSON(http.StatusOK, g)
}

// 检查 IPv4 字面量（节点/探测目标共用）
func isIPv4Literal(s string) bool {
	ip := net.ParseIP(s)
	return ip != nil && ip.To4() != nil
}

var _ = uuid.NewString
var _ = clash.ValidateNodeName

// validMacAddr 人工维护的设备标签 MAC：空=清除；其余允许十六进制与 : - 分隔（≤32）
var reMacAddr = regexp.MustCompile(`^[0-9A-Fa-f][0-9A-Fa-f:\-]{0,31}$`)

func validMacAddr(s string) bool {
	if s == "" {
		return true
	}
	return reMacAddr.MatchString(s)
}
