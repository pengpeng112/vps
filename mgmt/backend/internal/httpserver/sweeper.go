package httpserver

// 后台扫描器（≤10s 周期）：
//   - 离线检测：approved 且 last_heartbeat_at 超过 90s → 去重告警（§19.5）
//   - 任务超期：pending → expired；running 且 deadline 已过 → unknown（§19.2）
//   - nonce 过期清理（保留 ≥300s 由插入时的 600s 过期保证）
//   - 指标保留 90 天：metrics 定期 DELETE；月聚合独立保留（Session 1 不清理）
import (
	"context"
	"log"
	"time"

	"gorm.io/gorm"
)

func (s *Server) sweeperLoop(ctx context.Context) {
	metricsTick := time.NewTicker(1 * time.Hour)
	defer metricsTick.Stop()
	sweep := func() {
		if err := s.sweepOnce(); err != nil {
			log.Printf("sweeper: %v", err)
		}
	}
	sweep()
	for {
		select {
		case <-ctx.Done():
			return
		case <-time.After(s.Cfg.OfflineScanEvery):
			sweep()
		case <-metricsTick.C:
			if err := s.DB.Exec(`DELETE FROM metrics WHERE ts < now() - make_interval(days => ?)`, s.Cfg.MetricsKeepDays).Error; err != nil {
				log.Printf("metrics 清理失败: %v", err)
			}
		}
	}
}

func (s *Server) sweepOnce() error {
	return s.DB.Transaction(func(tx *gorm.DB) error {
		// 离线告警（去重 dedup_key=offline:{id}；恢复上线后重新离线会再次告警，
		// 因为心跳不 ack 告警 —— 由管理员确认；同一条未确认告警不重复产生）
		cutoff := time.Now().UTC().Add(-s.Cfg.HeartbeatTimeout)
		var offlineIDs []string
		if err := tx.Raw(`SELECT id FROM devices
			WHERE lifecycle_status='approved'
			  AND (last_heartbeat_at IS NULL AND created_at < ? OR last_heartbeat_at < ?)`,
			time.Now().UTC().Add(-s.Cfg.HeartbeatTimeout), cutoff).Scan(&offlineIDs).Error; err != nil {
			return err
		}
		// 注意：刚创建还没心跳的设备不立即告警 —— 用 created_at 宽限一个超时周期
		for _, id := range offlineIDs {
			var cnt int64
			if err := tx.Raw(`SELECT COUNT(*) FROM devices WHERE id=? AND created_at < ?`, id, cutoff).Scan(&cnt).Error; err != nil {
				return err
			}
			if cnt == 0 {
				continue
			}
			s.alerts.Raise(tx, "device_offline", &id, "warning",
				"设备 "+id+" 超过超时时间未心跳，判定离线",
				"offline:"+id)
		}

		// 任务超期
		if err := tx.Exec(`UPDATE tasks SET status='expired' WHERE status='pending' AND deadline < now()`).Error; err != nil {
			return err
		}
		if err := tx.Exec(`UPDATE tasks SET status='unknown' WHERE status='running' AND deadline < now()`).Error; err != nil {
			return err
		}
		// nonce 清理（过期 60s 后删除，保守保留超过 300s）
		if err := tx.Exec(`DELETE FROM used_nonces WHERE expires_at < now() - interval '60 seconds'`).Error; err != nil {
			return err
		}
		// 授权到期：去重告警（Agent 靠心跳 policy.service_active=false 自动关 VPS）
		var expiredIDs []string
		if err := tx.Raw(`SELECT id FROM devices
			WHERE lifecycle_status='approved' AND license_enabled=true
			  AND license_expires_at IS NOT NULL AND license_expires_at <= now()`).Scan(&expiredIDs).Error; err != nil {
			return err
		}
		for _, id := range expiredIDs {
			s.alerts.Raise(tx, "license_expired", &id, "warning",
				"设备 "+id+" 授权已到期，VPS 通道将停用（国内直连不受影响）",
				"license_expired:"+id)
		}
		return nil
	})
}
