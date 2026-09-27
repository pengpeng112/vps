package httpserver

// tunnelhealth.go — VPS 本机探测器 mgmt-rssh-probe 的内部上报入口（2026-09-24 阶段1C）。
//
// 背景：路由器 mgmt-tunnel 把管理 -L 与排障 -R 拆成两个 SSH 会话后，procd 只能保证
// “进程活着”，无法证明 -R 在 VPS 侧真的建立了监听（Dropbear dbclient 无
// ExitOnForwardFailure，绑定失败进程不退出）。VPS 上 systemd timer 每 30s 对
// 18222/13222 做 banner 探测，结果经本接口入库：
//   - devices.remote_ssh_alive / last_remote_ssh_check_at（与 Agent 心跳独立展示）
//   - 连续 ≥3 次失败 → 告警 remote_ssh_down（去重，恢复后自动 ack 关闭环）
//   - 探测器可附带 request_repair → 生成白名单任务 repair_mgmt_tunnel
//     （只重启路由器 mgmt-tunnel 服务；尝试次数上限由探测器侧控制，最多 2 次）
//
// 鉴权：X-Internal-Token 与 INTERNAL_TOKEN 常时比较；未配置 INTERNAL_TOKEN 时
// 整组路由 404（fail-closed）。该接口仅应暴露给容器内 8443 / WG 面，绝不上公网。

import (
	"crypto/subtle"
	"net/http"
	"strconv"
	"time"

	"github.com/gin-gonic/gin"
	"gorm.io/gorm"

	"routermgmt/internal/models"
)

const remoteSSHAlertThreshold = 3

func (s *Server) internalToken(c *gin.Context) {
	if s.Cfg.InternalToken == "" {
		c.AbortWithStatusJSON(http.StatusNotFound, gin.H{"error": "not found"})
		return
	}
	h := c.GetHeader("X-Internal-Token")
	if len(h) == 0 || subtle.ConstantTimeCompare([]byte(h), []byte(s.Cfg.InternalToken)) != 1 {
		c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{"error": "unauthorized"})
		return
	}
	c.Next()
}

type tunnelHealthReport struct {
	DeviceID            string     `json:"device_id" binding:"required"`
	Port                int        `json:"port" binding:"required"`
	Alive               bool       `json:"alive"`
	ConsecutiveFailures int        `json:"consecutive_failures"`
	FirstFailureAt      *time.Time `json:"first_failure_at"`
	RequestRepair       bool       `json:"request_repair"`
	RepairIdemKey       string     `json:"repair_idem_key"`
}

func (s *Server) handleTunnelHealthReport(c *gin.Context) {
	var req tunnelHealthReport
	if err := c.ShouldBindJSON(&req); err != nil {
		fail422(c, "device_id/port 必填")
		return
	}
	if req.Port < 1 || req.Port > 65535 {
		fail422(c, "port 越界")
		return
	}
	var dev models.Device
	if err := s.DB.Where("id=?", req.DeviceID).First(&dev).Error; err != nil {
		fail404(c, "设备不存在")
		return
	}
	now := time.Now().UTC()
	portStr := strconv.Itoa(req.Port)
	resp := gin.H{"ok": true}

	txErr := s.DB.Transaction(func(tx *gorm.DB) error {
		if e := tx.Model(&models.Device{}).Where("id=?", dev.ID).Updates(map[string]any{
			"remote_ssh_alive":         req.Alive,
			"last_remote_ssh_check_at": now,
		}).Error; e != nil {
			return e
		}
		dedup := "remote_ssh:" + dev.ID + ":" + portStr
		if !req.Alive && req.ConsecutiveFailures >= remoteSSHAlertThreshold {
			// 同一故障只留一条未确认告警（探测周期内的重复上报由 dedup 挡住）
			var exist models.Alert
			if e := tx.Where("dedup_key=? AND acked_at IS NULL", dedup).First(&exist).Error; e != nil {
				first := ""
				if req.FirstFailureAt != nil {
					first = req.FirstFailureAt.UTC().Format("01-02 15:04:05")
				}
				msg := "设备 " + dev.Name + "(" + dev.ID + ") 远程排障口 127.0.0.1:" + portStr +
					" 连续 " + strconv.Itoa(req.ConsecutiveFailures) +
					" 次 banner 探测失败（首败 " + first + " UTC），VPS 暂无法经反向 SSH 进入该设备；" +
					"Agent 心跳通道(-L)不受此告警影响"
				if e := tx.Create(&models.Alert{
					Kind: "remote_ssh_down", DeviceID: &dev.ID,
					Message: msg, Level: "critical", DedupKey: dedup,
				}).Error; e != nil {
					return e
				}
			}
		}
		if req.Alive {
			// 恢复 → 自动 ack 同 dedup 的未确认告警（阶段4D 引入 resolved_at 前的闭环）
			if e := tx.Model(&models.Alert{}).
				Where("dedup_key=? AND acked_at IS NULL", dedup).
				Updates(map[string]any{"acked_at": now, "acked_by": "tunnel-probe"}).Error; e != nil {
				return e
			}
		}
		// 白名单修复任务：仅故障态且已达告警阈值才受理；幂等键由探测器提供（按故障轮次）
		if req.RequestRepair && !req.Alive && req.ConsecutiveFailures >= remoteSSHAlertThreshold &&
			dev.LifecycleStatus == models.LifecycleApproved {
			idem := req.RepairIdemKey
			if idem == "" {
				idem = "tunnel-repair-" + dev.ID + "-" + now.Format("20060102150405")
			}
			t, e := s.createTask(tx, dev.ID, "repair_mgmt_tunnel", map[string]any{
				"port": req.Port, "consecutive_failures": req.ConsecutiveFailures,
			}, nil, "tunnel-probe", idem, 300*time.Second)
			if e == nil {
				resp["task_id"] = t.ID
			} else if e == errIdemConflict {
				resp["task_id"] = "(dedup: repair already queued)"
			}
			// 其它错误不中断上报（健康状态入库优先）
		}
		return nil
	})
	if txErr != nil {
		fail500(c, "处理失败")
		return
	}
	c.JSON(http.StatusOK, resp)
}
