package httpserver

// 节点 CRUD：参数 AES-GCM 加密落库；GET/响应只出掩码（§9：uuid/pbk 前后 4 位，sid 前后 2 位）；
// PATCH 省略字段表示保留，禁止把掩码写回凭据（§10）。

import (
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/http"
	"time"

	"github.com/gin-gonic/gin"
	"gorm.io/gorm"

	"routermgmt/internal/clash"
	"routermgmt/internal/crypto"
	"routermgmt/internal/models"
)

type nodeParams struct {
	UUID     string `json:"uuid"`
	PBK      string `json:"pbk"`
	SNI      string `json:"sni"`
	SID      string `json:"sid"`
	Flow     string `json:"flow"`
	FP       string `json:"fp"`
	Password string `json:"password"` // hysteria2
	WSPath   string `json:"ws_path"`  // vless-ws
	WSHost   string `json:"ws_host"`  // vless-ws（空=SNI）
}

// validateForProto 按协议校验必填参数：vless-reality 需要 uuid/pbk/sid；hysteria2 需要 password；vless-ws 需要 uuid/sni/ws_path。
func (p nodeParams) validateForProto(proto string) error {
	switch proto {
	case clash.ProtoHy2:
		if !clash.ValidHy2Password(p.Password) {
			return fmt.Errorf("hysteria2 password 不合法（需 16~64 位字母数字/-_）")
		}
		return nil
	case clash.ProtoVLESSWS:
		if !crypto.ValidUUID(p.UUID) {
			return fmt.Errorf("uuid 参数不合法（需标准 UUID）")
		}
		if !clash.ValidWSHost(p.SNI) {
			return fmt.Errorf("sni 参数不合法（vless-ws 需要中转域名，如 cf.example.top）")
		}
		if !clash.ValidWSPath(p.WSPath) {
			return fmt.Errorf("ws_path 参数不合法（形如 /k3x9）")
		}
		if p.WSHost != "" && !clash.ValidWSHost(p.WSHost) {
			return fmt.Errorf("ws_host 参数不合法")
		}
		if p.Flow != "" {
			return fmt.Errorf("vless-ws 不支持 flow（vision 与 ws 不兼容），请留空")
		}
		return nil
	case "", clash.ProtoVLESS:
		if !crypto.ValidUUID(p.UUID) {
			return fmt.Errorf("uuid 参数不合法（需标准 UUID）")
		}
		if !crypto.ValidPBK(p.PBK) {
			return fmt.Errorf("pbk 参数不合法（Reality public key，43 字符 base64url）")
		}
		if !crypto.ValidSID(p.SID) {
			return fmt.Errorf("sid 参数不合法（偶数长度 hex，≤16 字符）")
		}
		return nil
	default:
		return fmt.Errorf("proto 仅支持 vless-reality / vless-ws / hysteria2")
	}
}

func (s *Server) nodeToDTO(n *models.Node) gin.H {
	params := s.decryptNodeParams(n)
	return gin.H{
		"id": n.ID, "name": n.Name, "host": n.Host, "port": n.Port, "proto": n.Proto,
		"params": gin.H{
			"uuid":     crypto.MaskNodeParam("uuid", params.UUID),
			"pbk":      crypto.MaskNodeParam("pbk", params.PBK),
			"sid":      crypto.MaskNodeParam("sid", params.SID),
			"sni":      params.SNI, "flow": params.Flow, "fp": params.FP,
			"password": crypto.MaskNodeParam("password", params.Password),
			"ws_path":  params.WSPath, "ws_host": params.WSHost,
		},
		"priority": n.Priority, "enabled": n.Enabled,
		"health_status": n.HealthStatus, "last_check_at": n.LastCheckAt, "last_latency_ms": n.LastLatencyMs,
		"created_at": n.CreatedAt,
	}
}

func (s *Server) decryptNodeParams(n *models.Node) nodeParams {
	var p nodeParams
	if n.ProtoParamsEnc == "" {
		return p
	}
	raw, err := crypto.Decrypt(s.MasterKey, n.ProtoParamsEnc, fmt.Sprintf("nodes:%d:proto_params", n.ID))
	if err != nil {
		return p
	}
	json.Unmarshal(raw, &p)
	return p
}

func validateNodeBase(name, host string, port int) error {
	if !clash.ValidateNodeName(name) {
		return fmt.Errorf("节点名不合法（字母数字与 - _，长度 2~32）")
	}
	// Session 1 节点 host 限 IPv4 字面量（§3.2）
	if ip := net.ParseIP(host); ip == nil || ip.To4() == nil {
		return fmt.Errorf("节点 host 必须是 IPv4 字面量（Session 1 限制；域名/IPv6 待解析与直连规则实现）")
	}
	if port < 1 || port > 65535 {
		return fmt.Errorf("端口不合法")
	}
	return nil
}

func (s *Server) handleNodeList(c *gin.Context) {
	page, size, offset := pageArgs(c)
	var total int64
	s.DB.Model(&models.Node{}).Count(&total)
	var ns []models.Node
	s.DB.Order("priority DESC, id").Offset(offset).Limit(size).Find(&ns)
	items := make([]gin.H, 0, len(ns))
	for i := range ns {
		var bound int64
		s.DB.Model(&models.NodeAssignment{}).Where("node_id=?", ns[i].ID).Count(&bound)
		dto := s.nodeToDTO(&ns[i])
		dto["bound_devices"] = bound
		items = append(items, dto)
	}
	c.JSON(http.StatusOK, pageResp(items, total, page, size))
}

func (s *Server) handleNodeCreate(c *gin.Context) {
	a := adminFrom(c)
	var req struct {
		Name     string     `json:"name" binding:"required"`
		Host     string     `json:"host" binding:"required"`
		Port     int        `json:"port" binding:"required"`
		Proto    string     `json:"proto"`
		Params   nodeParams `json:"params" binding:"required"`
		Priority int        `json:"priority"`
		Enabled  *bool      `json:"enabled"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		fail422(c, "字段缺失")
		return
	}
	if req.Proto == "" {
		req.Proto = clash.ProtoVLESS
	}
	if req.Proto != clash.ProtoVLESS && req.Proto != clash.ProtoVLESSWS && req.Proto != clash.ProtoHy2 {
		fail422(c, "proto 仅支持 vless-reality / vless-ws / hysteria2")
		return
	}
	if err := validateNodeBase(req.Name, req.Host, req.Port); err != nil {
		fail422(c, err.Error())
		return
	}
	if err := req.Params.validateForProto(req.Proto); err != nil {
		fail422(c, err.Error())
		return
	}
	enabled := true
	if req.Enabled != nil {
		enabled = *req.Enabled
	}
	n := models.Node{Name: req.Name, Host: req.Host, Port: req.Port, Proto: req.Proto,
		Priority: req.Priority, Enabled: enabled, HealthStatus: "unknown"}
	err := s.DB.Transaction(func(tx *gorm.DB) error {
		if err := tx.Create(&n).Error; err != nil {
			return err
		}
		raw, _ := json.Marshal(req.Params)
		enc, err := crypto.Encrypt(s.MasterKey, raw, fmt.Sprintf("nodes:%d:proto_params", n.ID))
		if err != nil {
			return err
		}
		n.ProtoParamsEnc = enc
		return tx.Model(&n).Update("proto_params_enc", enc).Error
	})
	if err != nil {
		if errors.Is(err, gorm.ErrDuplicatedKey) || stringsContains(err.Error(), "duplicate") || stringsContains(err.Error(), "unique") {
			fail409(c, "节点名已存在")
			return
		}
		fail500(c, "创建节点失败")
		return
	}
	s.audit(c, "admin", a.Username, "node_create", fmt.Sprintf("node:%d", n.ID), gin.H{"name": n.Name, "host": req.Host, "port": req.Port})
	c.JSON(http.StatusOK, s.nodeToDTO(&n))
}

// handleNodePatch 省略字段保留原值；掩码值（含 "..."）写回会被拒绝。
func (s *Server) handleNodePatch(c *gin.Context) {
	a := adminFrom(c)
	var n models.Node
	if err := s.DB.Where("id=?", c.Param("id")).First(&n).Error; err != nil {
		fail404(c, "节点不存在")
		return
	}
	var req struct {
		Name     *string     `json:"name"`
		Host     *string     `json:"host"`
		Port     *int        `json:"port"`
		Params   *nodeParams `json:"params"`
		Priority *int        `json:"priority"`
		Enabled  *bool       `json:"enabled"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		fail422(c, "报文不合法")
		return
	}
	updates := map[string]any{}
	name, host, port := n.Name, n.Host, n.Port
	if req.Name != nil {
		name = *req.Name
		updates["name"] = name
	}
	if req.Host != nil {
		host = *req.Host
		updates["host"] = host
	}
	if req.Port != nil {
		port = *req.Port
		updates["port"] = port
	}
	if req.Priority != nil {
		updates["priority"] = *req.Priority
	}
	if req.Enabled != nil {
		updates["enabled"] = *req.Enabled
	}
	if name != n.Name || host != n.Host || port != n.Port {
		if err := validateNodeBase(name, host, port); err != nil {
			fail422(c, err.Error())
			return
		}
	}
	if req.Params != nil {
		cur := s.decryptNodeParams(&n)
		merged := nodeParams{UUID: cur.UUID, PBK: cur.PBK, SNI: cur.SNI, SID: cur.SID, Flow: cur.Flow, FP: cur.FP, Password: cur.Password}
		// 掩码值写回一律拒绝；空串 = 保留原值
		for field, v := range map[string]string{
			"uuid": req.Params.UUID, "pbk": req.Params.PBK, "sid": req.Params.SID,
			"sni": req.Params.SNI, "flow": req.Params.Flow, "fp": req.Params.FP,
			"password": req.Params.Password,
		} {
			if stringsContains(v, "...") || v == "****" {
				fail422(c, fmt.Sprintf("参数 %s 是掩码值，禁止写回；如需修改请提供完整新值", field))
				return
			}
		}
		if req.Params.UUID != "" {
			merged.UUID = req.Params.UUID
		}
		if req.Params.PBK != "" {
			merged.PBK = req.Params.PBK
		}
		if req.Params.SID != "" {
			merged.SID = req.Params.SID
		}
		if req.Params.SNI != "" {
			merged.SNI = req.Params.SNI
		}
		if req.Params.Flow != "" {
			merged.Flow = req.Params.Flow
		}
		if req.Params.FP != "" {
			merged.FP = req.Params.FP
		}
		if req.Params.Password != "" {
			merged.Password = req.Params.Password
		}
		if err := merged.validateForProto(n.Proto); err != nil {
			fail422(c, err.Error())
			return
		}
		raw, _ := json.Marshal(merged)
		enc, err := crypto.Encrypt(s.MasterKey, raw, fmt.Sprintf("nodes:%d:proto_params", n.ID))
		if err != nil {
			fail500(c, "加密失败")
			return
		}
		updates["proto_params_enc"] = enc
	}
	if len(updates) == 0 {
		c.JSON(http.StatusOK, s.nodeToDTO(&n))
		return
	}
	if err := s.DB.Model(&n).Updates(updates).Error; err != nil {
		fail409(c, "更新失败（节点名冲突？）")
		return
	}
	s.audit(c, "admin", a.Username, "node_update", fmt.Sprintf("node:%d", n.ID), gin.H{"fields": keysOf(updates)})
	var fresh models.Node
	s.DB.First(&fresh, n.ID)
	c.JSON(http.StatusOK, s.nodeToDTO(&fresh))
}

func stringsContains(s, sub string) bool {
	return len(s) >= len(sub) && (s == sub || len(sub) == 0 || indexOfStr(s, sub) >= 0)
}

func indexOfStr(s, sub string) int {
	for i := 0; i+len(sub) <= len(s); i++ {
		if s[i:i+len(sub)] == sub {
			return i
		}
	}
	return -1
}

func keysOf(m map[string]any) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	return out
}

// handleNodeDelete 被绑定或被设备当前节点引用时返回 409（§9）。
func (s *Server) handleNodeDelete(c *gin.Context) {
	a := adminFrom(c)
	id := c.Param("id")
	err := s.DB.Transaction(func(tx *gorm.DB) error {
		var n models.Node
		if err := tx.Where("id=?", id).First(&n).Error; err != nil {
			return errNotFound
		}
		var bound int64
		tx.Model(&models.NodeAssignment{}).Where("node_id=?", id).Count(&bound)
		if bound > 0 {
			return errNodeInUse
		}
		var active int64
		tx.Raw(`SELECT COUNT(*) FROM devices WHERE current_node_id=?`, id).Scan(&active)
		if active > 0 {
			return errNodeInUse
		}
		if err := tx.Delete(&n).Error; err != nil {
			return err
		}
		return auditTx(tx, "admin", a.Username, "node_delete", fmt.Sprintf("node:%s", id), gin.H{"name": n.Name})
	})
	if err != nil {
		switch err {
		case errNotFound:
			fail404(c, "节点不存在")
		case errNodeInUse:
			fail409(c, "节点被设备绑定或正在使用，先解绑再删除")
		default:
			fail500(c, "删除失败")
		}
		return
	}
	c.JSON(http.StatusOK, gin.H{"ok": true})
}

var errNodeInUse = fmt.Errorf("node in use")

// handleNodeProbe TCP 探测（超时 3s）；探测类型明确为 TCP 可达，不代表 Reality 鉴权可用（§19.3）。
func (s *Server) handleNodeProbe(c *gin.Context) {
	var n models.Node
	if err := s.DB.Where("id=?", c.Param("id")).First(&n).Error; err != nil {
		fail404(c, "节点不存在")
		return
	}
	addr := net.JoinHostPort(n.Host, fmt.Sprintf("%d", n.Port))
	start := time.Now()
	conn, err := net.DialTimeout("tcp", addr, 3*time.Second)
	latency := time.Since(start).Milliseconds()
	status := "up"
	if err != nil {
		status = "down"
		latency = -1
	} else {
		conn.Close()
	}
	now := time.Now().UTC()
	var latPtr *int64
	if status == "up" {
		latPtr = &latency
	}
	s.DB.Model(&n).Updates(map[string]any{"health_status": status, "last_check_at": now, "last_latency_ms": latPtr})
	c.JSON(http.StatusOK, gin.H{
		"id": n.ID, "name": n.Name, "probe_type": "tcp_connect",
		"health_status": status, "latency_ms": latPtr, "checked_at": now,
		"note": "TCP 可达不代表 Reality 鉴权或代理出口可用",
	})
}

// ---- 绑定 ----

type assignmentResult struct {
	DeviceID string `json:"device_id"`
	OK       bool   `json:"ok"`
	Error    string `json:"error,omitempty"`
	TaskID   string `json:"task_id,omitempty"`
	Version  int64  `json:"version,omitempty"`
}

// handleAssignmentsBatch 批量绑定：默认只更新期望绑定；apply=true 才发布配置并生成任务，
// 响应逐台列明结果，不假装全成功（§19.3）。
func (s *Server) handleAssignmentsBatch(c *gin.Context) {
	a := adminFrom(c)
	var req struct {
		DeviceIDs []string `json:"device_ids" binding:"required"`
		NodeID    int64    `json:"node_id" binding:"required"`
		IsPrimary bool     `json:"is_primary"`
		Apply     bool     `json:"apply"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		fail422(c, "device_ids 与 node_id 必填")
		return
	}
	var node models.Node
	if err := s.DB.Where("id=?", req.NodeID).First(&node).Error; err != nil {
		fail404(c, "节点不存在")
		return
	}
	if !node.Enabled {
		fail409(c, "节点已停用，不能绑定")
		return
	}
	results := make([]assignmentResult, 0, len(req.DeviceIDs))
	for _, deviceID := range req.DeviceIDs {
		r := assignmentResult{DeviceID: deviceID, OK: true}
		err := s.DB.Transaction(func(tx *gorm.DB) error {
			var dev models.Device
			if err := tx.Where("id=?", deviceID).First(&dev).Error; err != nil {
				r.Error, r.OK = "设备不存在", false
				return nil
			}
			if dev.LifecycleStatus != models.LifecycleApproved {
				r.Error, r.OK = "设备不是 approved 状态", false
				return nil
			}
			// 先降级旧 primary，再 upsert 新绑定（顺序错会触发 one_primary 唯一索引）
			if req.IsPrimary {
				if err := tx.Exec(`UPDATE node_assignments SET is_primary=false
					WHERE device_id=? AND node_id<>? AND is_primary`, deviceID, req.NodeID).Error; err != nil {
					r.Error, r.OK = "primary 调整失败", false
					return nil
				}
			}
			if err := tx.Exec(`INSERT INTO node_assignments (device_id, node_id, is_primary, failover_priority)
				VALUES (?,?,?,0)
				ON CONFLICT (device_id, node_id) DO UPDATE SET is_primary=EXCLUDED.is_primary, updated_at=now()`,
				deviceID, req.NodeID, req.IsPrimary).Error; err != nil {
				r.Error, r.OK = "绑定写入失败", false
				return nil
			}
			if req.IsPrimary {
				tx.Model(&models.Device{}).Where("id=?", deviceID).Update("current_node_id", req.NodeID)
			}
			if err := auditTx(tx, "admin", a.Username, "assignment_set", fmt.Sprintf("device:%s", deviceID),
				gin.H{"node_id": req.NodeID, "is_primary": req.IsPrimary, "apply": req.Apply}); err != nil {
				return err
			}
			if req.Apply {
				taskID, version, err := s.publishAndEnqueue(tx, &dev, &node, a.Username, "")
				if err != nil {
					r.Error, r.OK = "发布失败: "+err.Error(), false
					return nil
				}
				r.TaskID, r.Version = taskID, version
			}
			return nil
		})
		if err != nil {
			r.OK, r.Error = false, "事务失败: "+err.Error()
		}
		results = append(results, r)
	}
	c.JSON(http.StatusOK, gin.H{"items": results, "apply": req.Apply})
}
