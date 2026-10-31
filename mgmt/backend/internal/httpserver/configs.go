package httpserver

// 配置发布管线（§7.5 / §19.3）：
//   preview → 掩码 YAML + revision + diff（不发布、不进版本）
//   apply   → 校验 revision 未变，发布不可变版本 + 签名 + 原子入队 switch 任务
//   订阅    → 只返回已发布最新版本；GET 不生成版本、不改生效状态

import (
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
	"gorm.io/gorm"

	"routermgmt/internal/clash"
	"routermgmt/internal/crypto"
	"routermgmt/internal/models"
)

// resolveRenderNodes 解析设备可用的渲染节点：授权绑定且 enabled（§7.6），
// primary 在前；必须有且仅有一个 primary（§19.3）。
func (s *Server) resolveRenderNodes(tx *gorm.DB, dev *models.Device) ([]clash.NodeRender, *models.Node, error) {
	type row struct {
		ID        int64 `gorm:"column:id"`
		IsPrimary bool  `gorm:"column:is_primary"`
	}
	var ids []row
	if err := tx.Raw(`SELECT n.id, a.is_primary FROM node_assignments a
		JOIN nodes n ON n.id=a.node_id
		WHERE a.device_id=? AND n.enabled=true
		ORDER BY a.is_primary DESC, a.failover_priority, n.priority DESC, n.id`, dev.ID).Scan(&ids).Error; err != nil {
		return nil, nil, err
	}
	if len(ids) == 0 {
		return nil, nil, errNoBindableNode
	}
	primaryCount := 0
	for _, r := range ids {
		if r.IsPrimary {
			primaryCount++
		}
	}
	if primaryCount != 1 {
		return nil, nil, fmt.Errorf("设备必须且只能有一个 primary 绑定节点（当前 %d 个）", primaryCount)
	}
	var out []clash.NodeRender
	var primary *models.Node
	for _, r := range ids {
		var n models.Node
		if err := tx.First(&n, r.ID).Error; err != nil {
			return nil, nil, err
		}
		p := s.decryptNodeParams(&n)
		out = append(out, clash.NodeRender{
			ID: n.ID, Name: n.Name, Proto: n.Proto, Host: n.Host, Port: n.Port,
			UUID: p.UUID, PBK: p.PBK, SNI: p.SNI, SID: p.SID, Flow: p.Flow, FP: p.FP,
			Password: p.Password, WSPath: p.WSPath, WSHost: p.WSHost,
		})
		if r.IsPrimary {
			cp := n
			primary = &cp
		}
	}
	return out, primary, nil
}

var errNoBindableNode = fmt.Errorf("没有可用绑定节点，拒绝发布（保留当前配置）")

// mgmtDirectIPs 管理面直连地址（WG endpoint 为 IPv4 字面量时加入）。
func (s *Server) mgmtDirectIPs() []string {
	host := s.Cfg.WGHubEndpoint
	if i := strings.LastIndex(host, ":"); i > 0 {
		host = host[:i]
	}
	if host != "" && isIPv4Literal(host) {
		return []string{host}
	}
	return nil
}

// bindingRevision 绑定/节点参数指纹：apply 时重算并比对，变化则 409（§19.3）。
func (s *Server) bindingRevision(tx *gorm.DB, dev *models.Device) (string, error) {
	nodes, _, err := s.resolveRenderNodes(tx, dev)
	if err != nil {
		return "", err
	}
	var sb strings.Builder
	for _, n := range nodes {
		// 指纹含协议与各自凭据：任何参数变化都触发 apply 重算比对（§19.3）
		sb.WriteString(fmt.Sprintf("%d|%s|%s|%d|%s|%s|%s|%s|%s|%s|%s|%s|%s;", n.ID, n.Name, n.Proto, n.Port, n.Host, n.UUID, n.PBK, n.SNI, n.SID, n.Flow, n.Password, n.WSPath, n.WSHost))
	}
	sb.WriteString(strings.Join(s.mgmtDirectIPs(), ","))
	return crypto.SHA256Hex([]byte(sb.String())), nil
}

func (s *Server) renderFor(tx *gorm.DB, dev *models.Device) ([]byte, []clash.NodeRender, error) {
	nodes, _, err := s.resolveRenderNodes(tx, dev)
	if err != nil {
		return nil, nil, err
	}
	yamlBytes, err := clash.Render(clash.RenderInput{
		AirportSubURL: s.Cfg.AirportSubURL, AirportGeneration: s.Cfg.AirportGeneration,
		DeviceID: dev.ID, Nodes: nodes, MgmtDirectIPs: s.mgmtDirectIPs(),
	})
	if err != nil {
		return nil, nil, err
	}
	return yamlBytes, nodes, nil
}

// ---- preview / apply ----

func (s *Server) handleConfigPreview(c *gin.Context) {
	var dev models.Device
	if err := s.DB.Where("id=?", c.Param("id")).First(&dev).Error; err != nil {
		fail404(c, "设备不存在")
		return
	}
	var yamlBytes []byte
	var revision string
	err := s.DB.Transaction(func(tx *gorm.DB) error {
		var e error
		yamlBytes, _, e = s.renderFor(tx, &dev)
		if e != nil {
			return e
		}
		revision, e = s.bindingRevision(tx, &dev)
		return e
	})
	if err != nil {
		if err == errNoBindableNode {
			fail409(c, err.Error())
			return
		}
		fail422(c, "渲染失败: "+err.Error())
		return
	}
	// 与当前已发布版本做掩码 diff
	maskedNew := clash.MaskYAML(yamlBytes)
	var oldMasked string
	var oldVersion int64
	if dev.DesiredConfigVersion > 0 {
		var cfg models.Config
		if err := s.DB.Where("device_id=? AND version=?", dev.ID, dev.DesiredConfigVersion).First(&cfg).Error; err == nil {
			if raw, derr := crypto.Decrypt(s.MasterKey, cfg.ContentEnc, fmt.Sprintf("configs:%d:content", cfg.ID)); derr == nil {
				oldMasked = clash.MaskYAML(raw)
				oldVersion = cfg.Version
			}
		}
	}
	nextVersion := dev.DesiredConfigVersion + 1
	c.JSON(http.StatusOK, gin.H{
		"revision":        revision,
		"next_version":    nextVersion,
		"sha256":          crypto.SHA256Hex(yamlBytes),
		"masked_yaml":     maskedNew,
		"current_version": oldVersion,
		"diff":            lineDiff(oldMasked, maskedNew),
	})
}

// publishAndEnqueue 发布不可变版本并原子入队（同一事务，§19.3）。
// idemKey 可为空（apply 未带幂等键时生成唯一值）。
// 若最新已发布内容与本次完全一致，则复用版本不重复发布（版本号仍单调）。
func (s *Server) publishAndEnqueue(tx *gorm.DB, dev *models.Device, node *models.Node, actor, idemKey string) (string, int64, error) {
	yamlBytes, nodes, err := s.renderFor(tx, dev)
	if err != nil {
		return "", 0, err
	}
	sum := crypto.SHA256Hex(yamlBytes)

	// 复用内容一致的最新版本（重复 switch 同一配置不膨胀版本）
	var existing models.Config
	reuse := false
	if err := tx.Raw(`SELECT * FROM configs WHERE device_id=? ORDER BY version DESC LIMIT 1`, dev.ID).Scan(&existing).Error; err == nil && existing.ID > 0 && existing.SHA256 == sum {
		reuse = true
	}
	var cfg models.Config
	if reuse {
		cfg = existing
	} else {
		var maxVer int64
		tx.Raw(`SELECT COALESCE(MAX(version),0) FROM configs WHERE device_id=?`, dev.ID).Scan(&maxVer)
		cfg = models.Config{
			DeviceID: dev.ID, Kind: "clash_yaml", Version: maxVer + 1,
			SHA256: sum, CreatedBy: actor,
		}
		enc, err := crypto.Encrypt(s.MasterKey, yamlBytes, fmt.Sprintf("configs:%s:v%d:content", dev.ID, cfg.Version))
		if err != nil {
			return "", 0, err
		}
		sig, err := s.signing.Sign(yamlBytes, dev.ID, cfg.Version)
		if err != nil {
			return "", 0, err
		}
		cfg.Signature = sig
		cfg.ContentEnc = enc
		if err := tx.Create(&cfg).Error; err != nil {
			return "", 0, err
		}
		enc, err = crypto.Encrypt(s.MasterKey, yamlBytes, fmt.Sprintf("configs:%d:content", cfg.ID))
		if err != nil {
			return "", 0, err
		}
		// 用记录 ID 作为 AAD 重存（绑定记录 ID，§8）
		if err := tx.Model(&models.Config{}).Where("id=?", cfg.ID).Update("content_enc", enc).Error; err != nil {
			return "", 0, err
		}
	}
	// 期望版本 + 任务入队
	if err := tx.Model(&models.Device{}).Where("id=?", dev.ID).Updates(map[string]any{
		"desired_config_version": cfg.Version,
		"updated_at":             time.Now().UTC(),
	}).Error; err != nil {
		return "", 0, err
	}
	if err := auditTx(tx, "admin", actor, "config_publish", fmt.Sprintf("device:%s:v%d", dev.ID, cfg.Version),
		gin.H{"sha256": sum, "reuse": reuse}); err != nil {
		return "", 0, err
	}
	primary := nodes[0]
	task, err := s.createTask(tx, dev.ID, "switch_node", map[string]any{
		"node_id":        primary.ID,
		"node_name":      primary.Name,
		"expect_exit_ip": primary.Host, // 固定 IP 节点：出口=服务器 IP
	}, &cfg, actor, idemKey, 120*time.Second)
	if err != nil {
		return "", 0, err
	}
	return task.ID, cfg.Version, nil
}

func (s *Server) handleConfigApply(c *gin.Context) {
	a := adminFrom(c)
	var dev models.Device
	if err := s.DB.Where("id=?", c.Param("id")).First(&dev).Error; err != nil {
		fail404(c, "设备不存在")
		return
	}
	var req struct {
		Revision string `json:"revision" binding:"required"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		fail422(c, "revision 必填（来自 preview）")
		return
	}
	idemKey := c.GetHeader("Idempotency-Key")
	var taskID string
	var version int64
	err := s.DB.Transaction(func(tx *gorm.DB) error {
		cur, err := s.bindingRevision(tx, &dev)
		if err != nil {
			return err
		}
		if cur != req.Revision {
			return errRevisionChanged
		}
		nodes, primary, err := s.resolveRenderNodes(tx, &dev)
		if err != nil {
			return err
		}
		_ = nodes
		taskID, version, err = s.publishAndEnqueue(tx, &dev, primary, a.Username, idemKey)
		return err
	})
	if err != nil {
		switch err {
		case errRevisionChanged:
			fail409(c, "绑定或节点参数已变化，请重新 preview")
		case errNoBindableNode:
			fail409(c, err.Error())
		case errIdemConflict:
			fail409(c, "相同 Idempotency-Key 但内容不同")
		default:
			fail422(c, "发布失败")
		}
		return
	}
	c.JSON(http.StatusOK, gin.H{"ok": true, "task_id": taskID, "version": version})
}

var errRevisionChanged = fmt.Errorf("revision changed")

// lineDiff 简单行级 diff（掩码内容，变更行足够）。
func lineDiff(oldS, newS string) []gin.H {
	oldL := strings.Split(oldS, "\n")
	newL := strings.Split(newS, "\n")
	oldSet := map[string]int{}
	for _, l := range oldL {
		oldSet[l]++
	}
	newSet := map[string]int{}
	for _, l := range newL {
		newSet[l]++
	}
	var out []gin.H
	for _, l := range oldL {
		if newSet[l] == 0 {
			out = append(out, gin.H{"op": "-", "line": l})
		} else if newSet[l] > 0 {
			newSet[l]--
		}
	}
	for _, l := range newL {
		if oldSet[l] == 0 {
			out = append(out, gin.H{"op": "+", "line": l})
		} else if oldSet[l] > 0 {
			oldSet[l]--
		}
	}
	return out
}

// ---- ops（任务下发）----

var rePingHost = regexp.MustCompile(`^[A-Za-z0-9._-]{1,253}$`)

func asInt(v any) (int, bool) {
	switch n := v.(type) {
	case float64:
		if n != float64(int(n)) {
			return 0, false
		}
		return int(n), true
	case int:
		return n, true
	case int64:
		return int(n), true
	default:
		return 0, false
	}
}

// createTask 建任务（幂等：同 admin+device+key 相同内容返回原任务；内容不同 409）。
func (s *Server) createTask(tx *gorm.DB, deviceID, typ string, params map[string]any, cfg *models.Config, actor, idemKey string, timeout time.Duration) (*models.Task, error) {
	paramsJSON := "{}"
	if params != nil {
		paramsJSON, _ = jsonMarshal(params)
	}
	if idemKey == "" {
		// 未显式提供幂等键（assignments/apply 路径）：生成唯一值，
		// 避免空串命中 (device,created_by,idempotency_key) 部分唯一索引
		idemKey = "auto-" + uuid.NewString()
	}
	if idemKey != "" && !strings.HasPrefix(idemKey, "auto-") {
		var exist models.Task
		err := tx.Raw(`SELECT * FROM tasks WHERE device_id=? AND created_by=? AND idempotency_key=? LIMIT 1`,
			deviceID, actor, idemKey).Scan(&exist).Error
		if err == nil && exist.ID != "" {
			if exist.Type == typ && exist.ParamsJSON == paramsJSON {
				return &exist, nil
			}
			return nil, errIdemConflict
		}
	}
	idHex, _ := crypto.GenerateHex(6)
	t := &models.Task{
		ID: "t-" + idHex, DeviceID: deviceID, Type: typ,
		ParamsJSON: paramsJSON, Status: models.TaskPending,
		CreatedBy: actor, IdempotencyKey: idemKey,
		Deadline: time.Now().UTC().Add(timeout),
	}
	if cfg != nil {
		t.ConfigID = &cfg.ID
		t.ConfigVersion = &cfg.Version
		t.ConfigSHA256 = cfg.SHA256
	}
	if err := tx.Create(t).Error; err != nil {
		return nil, err
	}
	return t, nil
}

func (s *Server) enqueueApplyPolicyTx(tx *gorm.DB, dev *models.Device, actor, idemKey string) (*models.Task, error) {
	pol := s.devicePolicy(dev)
	return s.createTask(tx, dev.ID, "apply_policy", pol, nil, actor, idemKey, 120*time.Second)
}

func (s *Server) enqueueApplyPolicy(dev *models.Device, actor string) (string, error) {
	var id string
	err := s.DB.Transaction(func(tx *gorm.DB) error {
		t, err := s.enqueueApplyPolicyTx(tx, dev, actor, "auto-"+uuid.NewString())
		if err != nil {
			return err
		}
		id = t.ID
		return nil
	})
	return id, err
}

var errIdemConflict = fmt.Errorf("idempotency conflict")

// handleDeviceOps 管理台运维任务入队（需 Idempotency-Key 头）。
func (s *Server) handleDeviceOps(c *gin.Context) {
	a := adminFrom(c)
	id := c.Param("id")
	idemKey := c.GetHeader("Idempotency-Key")
	if idemKey == "" {
		fail422(c, "缺少 Idempotency-Key 头（幂等要求，§19.2）")
		return
	}
	var req struct {
		Type   string         `json:"type" binding:"required"`
		Params map[string]any `json:"params"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		fail422(c, "type 必填")
		return
	}
	var dev models.Device
	if err := s.DB.Where("id=?", id).First(&dev).Error; err != nil {
		fail404(c, "设备不存在")
		return
	}
	if dev.LifecycleStatus != models.LifecycleApproved {
		fail409(c, "设备不是 approved 状态")
		return
	}
	var taskID string
	var version int64
	err := s.DB.Transaction(func(tx *gorm.DB) error {
		switch req.Type {
		case "switch_node":
			nodeID, ok := req.Params["node_id"].(float64)
			if !ok {
				return fmt.Errorf("params.node_id 必填")
			}
			var node models.Node
			if err := tx.Where("id=?", int64(nodeID)).First(&node).Error; err != nil {
				return fmt.Errorf("节点不存在")
			}
			var bound int64
			tx.Raw(`SELECT COUNT(*) FROM node_assignments WHERE device_id=? AND node_id=?`, id, int64(nodeID)).Scan(&bound)
			if bound == 0 {
				return fmt.Errorf("节点未绑定到该设备，先绑定再切换")
			}
			if !node.Enabled {
				return fmt.Errorf("节点已停用")
			}
			// 设 primary 指向所选节点（手动 switch 固定 PROXY 到所选节点，§19.3）
			tx.Exec(`UPDATE node_assignments SET is_primary=false WHERE device_id=? AND is_primary`, id)
			tx.Exec(`INSERT INTO node_assignments (device_id,node_id,is_primary,failover_priority) VALUES (?,?,true,0)
				ON CONFLICT (device_id,node_id) DO UPDATE SET is_primary=true, updated_at=now()`, id, int64(nodeID))
			tx.Model(&models.Device{}).Where("id=?", id).Update("current_node_id", int64(nodeID))
			var err2 error
			taskID, version, err2 = s.publishAndEnqueue(tx, &dev, &node, a.Username, idemKey)
			return err2
		case "reload_proxy":
			if dev.DesiredConfigVersion == 0 {
				return fmt.Errorf("设备尚无已发布配置")
			}
			var cfg models.Config
			if err := tx.Where("device_id=? AND version=?", id, dev.DesiredConfigVersion).First(&cfg).Error; err != nil {
				return fmt.Errorf("目标配置版本不存在")
			}
			t, err := s.createTask(tx, id, "reload_proxy", map[string]any{}, &cfg, a.Username, idemKey, 120*time.Second)
			if err != nil {
				return err
			}
			taskID = t.ID
			version = cfg.Version
			return nil
		case "reboot":
			t, err := s.createTask(tx, id, "reboot", map[string]any{}, nil, a.Username, idemKey, 600*time.Second)
			if err != nil {
				return err
			}
			taskID = t.ID
			return nil
		case "ping_test":
			host, _ := req.Params["host"].(string)
			if !rePingHost.MatchString(host) {
				return fmt.Errorf("params.host 不合法（域名/IP 字符集限制）")
			}
			t, err := s.createTask(tx, id, "ping_test", map[string]any{"host": host}, nil, a.Username, idemKey, 120*time.Second)
			if err != nil {
				return err
			}
			taskID = t.ID
			return nil
		case "set_proxy_state":
			// 远程停用/启用 VPS 代理：停用=PROXY 组切 DIRECT（流量全直连），启用=恢复 AUTO
			enabled, ok := req.Params["enabled"].(bool)
			if !ok {
				return fmt.Errorf("params.enabled 必填（bool）")
			}
			t, err := s.createTask(tx, id, "set_proxy_state", map[string]any{"enabled": enabled}, nil, a.Username, idemKey, 120*time.Second)
			if err != nil {
				return err
			}
			taskID = t.ID
			return nil
		case "set_wifi":
			// 远程开关 Wi-Fi：关闭时 agent 侧布防 300s 自动恢复兜底（见 task_set_wifi）
			wifiOn, ok := req.Params["enabled"].(bool)
			if !ok {
				return fmt.Errorf("params.enabled 必填（bool）")
			}
			t, err := s.createTask(tx, id, "set_wifi", map[string]any{"enabled": wifiOn}, nil, a.Username, idemKey, 120*time.Second)
			if err != nil {
				return err
			}
			taskID = t.ID
			return nil
		case "agent_update":
			// 远程升级 agent：不填版本=目录里最新包；版本在创建时快照进 params
			var version int64
			if v, ok := req.Params["version"]; ok && v != nil {
				switch n := v.(type) {
				case float64:
					version = int64(n)
				case string:
					if _, err := fmt.Sscanf(n, "%d", &version); err != nil {
						return fmt.Errorf("params.version 不合法（纯数字）")
					}
				default:
					return fmt.Errorf("params.version 不合法（纯数字）")
				}
			}
			if version == 0 {
				version = latestAgentBundle(s.Cfg.AgentBundleDir)
				if version == 0 {
					return fmt.Errorf("服务器上没有可用的 agent 升级包")
				}
			}
			// 防误降级（2026-09-25 事故：界面/旧会话下发 2026092415 旧包，把验签修复冲掉）：
			// 显式版本低于目录最新包时拒绝；确需回滚加 params.force=true
			if latest := latestAgentBundle(s.Cfg.AgentBundleDir); latest > 0 && version < latest {
				force, _ := req.Params["force"].(bool)
				if !force {
					return fmt.Errorf("升级包 %d 低于最新版 %d，已拒绝（防误降级）；确需回滚请加 params.force=true", version, latest)
				}
			}
			if _, err := os.Stat(filepath.Join(s.Cfg.AgentBundleDir, fmt.Sprintf("agent-%d.tar.gz", version))); err != nil {
				return fmt.Errorf("升级包 %d 不存在", version)
			}
			t, err := s.createTask(tx, id, "agent_update", map[string]any{"version": version}, nil, a.Username, idemKey, 600*time.Second)
			if err != nil {
				return err
			}
			taskID = t.ID
			return nil
		case "apply_policy":
			t, err := s.enqueueApplyPolicyTx(tx, &dev, a.Username, idemKey)
			if err != nil {
				return err
			}
			taskID = t.ID
			return nil
		case "set_ratelimit":
			kbps, ok := asInt(req.Params["kbps"])
			if !ok || !validKbps(kbps) {
				return fmt.Errorf("params.kbps 范围 0..1000000（0=不限速）")
			}
			if err := tx.Model(&models.Device{}).Where("id=?", id).Updates(map[string]any{
				"vps_limit_kbps": kbps, "updated_at": time.Now().UTC(),
			}).Error; err != nil {
				return err
			}
			dev.VpsLimitKbps = kbps
			t, err := s.enqueueApplyPolicyTx(tx, &dev, a.Username, idemKey)
			if err != nil {
				return err
			}
			taskID = t.ID
			return nil
		case "set_reboot_schedule":
			cron, _ := req.Params["cron"].(string)
			if !validRebootCron(cron) {
				return fmt.Errorf("params.cron 不在白名单")
			}
			if err := tx.Model(&models.Device{}).Where("id=?", id).Updates(map[string]any{
				"reboot_cron": cron, "updated_at": time.Now().UTC(),
			}).Error; err != nil {
				return err
			}
			dev.RebootCron = cron
			t, err := s.enqueueApplyPolicyTx(tx, &dev, a.Username, idemKey)
			if err != nil {
				return err
			}
			taskID = t.ID
			return nil
		case "set_license":
			upd := map[string]any{"updated_at": time.Now().UTC()}
			if v, ok := req.Params["enabled"].(bool); ok {
				upd["license_enabled"] = v
				dev.LicenseEnabled = v
			}
			if days, ok := asInt(req.Params["days"]); ok {
				if days < 1 || days > 3650 {
					return fmt.Errorf("params.days 范围 1..3650")
				}
				now := time.Now().UTC()
				exp := now.Add(time.Duration(days) * 24 * time.Hour)
				upd["license_expires_at"] = exp
				upd["license_started_at"] = now
				upd["license_enabled"] = true
				dev.LicenseExpiresAt = &exp
				dev.LicenseStartedAt = &now
				dev.LicenseEnabled = true
			} else if raw, ok := req.Params["expires_at"].(string); ok {
				if raw == "" {
					upd["license_expires_at"] = nil
					dev.LicenseExpiresAt = nil
				} else {
					t, err := time.Parse(time.RFC3339, raw)
					if err != nil {
						return fmt.Errorf("params.expires_at 必须是 RFC3339")
					}
					tt := t.UTC()
					upd["license_expires_at"] = tt
					dev.LicenseExpiresAt = &tt
				}
			}
			if err := tx.Model(&models.Device{}).Where("id=?", id).Updates(upd).Error; err != nil {
				return err
			}
			t, err := s.enqueueApplyPolicyTx(tx, &dev, a.Username, idemKey)
			if err != nil {
				return err
			}
			taskID = t.ID
			return nil
		case "repair_mgmt_tunnel":
			// VPS 侧探针同款任务开放给管理台手动下发（2026-09-30：远程修复 -R 半开连接）
			t, err := s.createTask(tx, id, "repair_mgmt_tunnel", map[string]any{}, nil, a.Username, idemKey, 120*time.Second)
			if err != nil {
				return err
			}
			taskID = t.ID
			return nil
		case "collect_diag":
			t, err := s.createTask(tx, id, "collect_diag", map[string]any{}, nil, a.Username, idemKey, 120*time.Second)
			if err != nil {
				return err
			}
			taskID = t.ID
			return nil
		case "set_proxy_mode":
			mode, _ := req.Params["mode"].(string)
			if !validProxyMode(mode) {
				return fmt.Errorf("params.mode 只能是 rule / global / direct")
			}
			if err := tx.Model(&models.Device{}).Where("id=?", id).Updates(map[string]any{
				"proxy_mode": mode, "updated_at": time.Now().UTC(),
			}).Error; err != nil {
				return err
			}
			dev.ProxyMode = mode
			t, err := s.enqueueApplyPolicyTx(tx, &dev, a.Username, idemKey)
			if err != nil {
				return err
			}
			taskID = t.ID
			return nil
		case "set_access_mode":
			mode, _ := req.Params["mode"].(string)
			if !validAccessMode(mode) {
				return fmt.Errorf("params.mode 只能是 all / whitelist / blacklist")
			}
			list := dev.AccessList
			if raw, ok := req.Params["list"].(string); ok {
				norm, err := normalizeAccessList(raw)
				if err != nil {
					return err
				}
				list = norm
			}
			if mode == "whitelist" && list == "" {
				return errAccessEmpty
			}
			if err := tx.Model(&models.Device{}).Where("id=?", id).Updates(map[string]any{
				"access_mode": mode, "access_list": list, "updated_at": time.Now().UTC(),
			}).Error; err != nil {
				return err
			}
			dev.AccessMode = mode
			dev.AccessList = list
			t, err := s.enqueueApplyPolicyTx(tx, &dev, a.Username, idemKey)
			if err != nil {
				return err
			}
			taskID = t.ID
			return nil
		default:
			return fmt.Errorf("不支持的任务类型 %s（白名单：reboot/reload_proxy/switch_node/ping_test/set_proxy_state/set_wifi/agent_update/repair_mgmt_tunnel/apply_policy/set_ratelimit/set_reboot_schedule/set_license/collect_diag/set_proxy_mode/set_access_mode）", req.Type)
		}
	})
	if err != nil {
		if err == errIdemConflict {
			fail409(c, "相同 Idempotency-Key 但内容不同")
			return
		}
		fail422(c, err.Error())
		return
	}
	s.audit(c, "admin", a.Username, "task_create", "task:"+taskID, gin.H{"device": id, "type": req.Type, "params": req.Params})
	resp := gin.H{"task_id": taskID, "type": req.Type}
	if version > 0 {
		resp["version"] = version
	}
	c.JSON(http.StatusOK, resp)
}

// ---- 管理端任务/配置查询 ----

func (s *Server) handleTaskList(c *gin.Context) {
	page, size, offset := pageArgs(c)
	q := s.DB.Model(&models.Task{})
	if v := c.Query("device_id"); v != "" {
		q = q.Where("device_id=?", v)
	}
	if v := c.Query("status"); v != "" {
		q = q.Where("status=?", v)
	}
	var total int64
	q.Count(&total)
	var ts []models.Task
	q.Order("created_at DESC").Offset(offset).Limit(size).Find(&ts)
	items := make([]gin.H, 0, len(ts))
	for i := range ts {
		t := &ts[i]
		items = append(items, gin.H{
			"id": t.ID, "device_id": t.DeviceID, "type": t.Type,
			"params": jsonRaw(t.ParamsJSON), "status": t.Status,
			"result":         jsonRaw(t.ResultJSON),
			"config_version": t.ConfigVersion, "created_by": t.CreatedBy,
			"created_at": t.CreatedAt, "claimed_at": t.ClaimedAt, "executed_at": t.ExecutedAt, "deadline": t.Deadline,
		})
	}
	c.JSON(http.StatusOK, pageResp(items, total, page, size))
}

// handleDeviceConfigMasked 管理端只看掩码 YAML；原始配置仅设备可下（§9）。
func (s *Server) handleDeviceConfigMasked(c *gin.Context) {
	var dev models.Device
	if err := s.DB.Where("id=?", c.Param("id")).First(&dev).Error; err != nil {
		fail404(c, "设备不存在")
		return
	}
	if dev.DesiredConfigVersion == 0 {
		c.JSON(http.StatusOK, gin.H{"version": 0, "yaml": "", "message": "尚未发布配置"})
		return
	}
	var cfg models.Config
	if err := s.DB.Where("device_id=? AND version=?", dev.ID, dev.DesiredConfigVersion).First(&cfg).Error; err != nil {
		fail404(c, "配置不存在")
		return
	}
	raw, err := crypto.Decrypt(s.MasterKey, cfg.ContentEnc, fmt.Sprintf("configs:%d:content", cfg.ID))
	if err != nil {
		fail500(c, "解密失败")
		return
	}
	c.JSON(http.StatusOK, gin.H{
		"version": cfg.Version, "sha256": cfg.SHA256,
		"created_at": cfg.CreatedAt, "yaml": clash.MaskYAML(raw),
	})
}

// ---- 订阅（设备/OpenClash 受控导出，只走 WG/本机开发）----

func (s *Server) handleSubscription(c *gin.Context) {
	id := c.Param("id")
	token := c.Query("token")
	if token == "" {
		fail422(c, "缺少 token")
		return
	}
	var dev models.Device
	if err := s.DB.Where("id=?", id).First(&dev).Error; err != nil {
		fail404(c, "订阅不存在")
		return
	}
	if dev.LifecycleStatus != models.LifecycleApproved {
		fail404(c, "订阅不可用")
		return
	}
	// 只存 hash：sha256(token) 常量时间比对
	if !crypto.ConstantTimeEquals(crypto.SHA256Hex([]byte(token)), dev.SubTokenHash) || dev.SubTokenHash == "" {
		fail401(c, "token 无效")
		return
	}
	// 仅返回已发布最新不可变配置；无绑定/未发布 409，禁止回退全部节点（§7.6）
	if dev.DesiredConfigVersion == 0 {
		fail409(c, "设备尚无已发布配置")
		return
	}
	var cfg models.Config
	if err := s.DB.Where("device_id=? AND version=?", dev.ID, dev.DesiredConfigVersion).First(&cfg).Error; err != nil {
		fail409(c, "设备尚无已发布配置")
		return
	}
	raw, err := crypto.Decrypt(s.MasterKey, cfg.ContentEnc, fmt.Sprintf("configs:%d:content", cfg.ID))
	if err != nil {
		fail500(c, "解密失败")
		return
	}
	// GET 不生成版本、不更改当前生效状态
	c.Header("Content-Type", "text/yaml; charset=utf-8")
	c.Header("Subscription-Userinfo", "version="+fmt.Sprint(cfg.Version))
	c.Data(http.StatusOK, "text/yaml; charset=utf-8", raw)
}

var _ = uuid.NewString
