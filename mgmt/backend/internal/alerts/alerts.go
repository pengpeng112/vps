// Package alerts 告警写入（去重）与 webhook 异步投递。
// 去重规则：同一 dedup_key 只允许一条未确认告警（数据库部分唯一索引保证）。
// webhook 在事务外异步投递，有超时、重试和发送记录；下游故障不阻塞心跳（§19.5）。
package alerts

import (
	"context"
	"encoding/json"
	"log"
	"net/http"
	"time"

	"gorm.io/gorm"

	"routermgmt/internal/models"
)

type Dispatcher struct {
	DB         *gorm.DB
	WebhookURL string
	Signal     chan struct{}
}

func New(db *gorm.DB, webhookURL string) *Dispatcher {
	return &Dispatcher{DB: db, WebhookURL: webhookURL, Signal: make(chan struct{}, 4)}
}

// Raise 在事务内创建告警（去重）。返回是否新建。
func (d *Dispatcher) Raise(tx *gorm.DB, kind string, deviceID *string, level, message, dedupKey string) bool {
	res := tx.Exec(`INSERT INTO alerts (kind, device_id, message, level, dedup_key)
		VALUES (?,?,?,?,?) ON CONFLICT (dedup_key) WHERE acked_at IS NULL DO NOTHING`,
		kind, deviceID, message, level, dedupKey)
	if res.Error != nil || res.RowsAffected == 0 {
		return false
	}
	if d.WebhookURL != "" {
		// 投递记录同事务创建；实际发送在 Worker 中异步进行
		var id int64
		tx.Raw(`SELECT id FROM alerts WHERE dedup_key=? AND acked_at IS NULL ORDER BY id DESC LIMIT 1`, dedupKey).Scan(&id)
		if id > 0 {
			tx.Exec(`INSERT INTO alert_deliveries (alert_id, status) VALUES (?, 'pending')`, id)
		}
	}
	return true
}

// Worker 异步投递循环：超时 5s，最多 3 次（10s/60s 退避），失败不阻塞业务。
func (d *Dispatcher) Worker(ctx context.Context) {
	if d.WebhookURL == "" {
		return
	}
	client := &http.Client{Timeout: 5 * time.Second}
	ticker := time.NewTicker(30 * time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-d.Signal:
		case <-ticker.C:
		}
		d.deliverPending(ctx, client)
	}
}

func (d *Dispatcher) deliverPending(ctx context.Context, client *http.Client) {
	for {
		if ctx.Err() != nil {
			return
		}
		var row struct {
			ID      int64
			Attempt int
		}
		err := d.DB.Raw(`SELECT dv.id, dv.attempt FROM alert_deliveries dv
			WHERE dv.status='pending' AND (dv.next_retry_at IS NULL OR dv.next_retry_at <= now())
			ORDER BY dv.id LIMIT 1`).Scan(&row).Error
		if err != nil || row.ID == 0 {
			return
		}
		d.tryDeliver(ctx, client, row.ID)
	}
}

func (d *Dispatcher) tryDeliver(ctx context.Context, client *http.Client, deliveryID int64) {
	var delivery models.AlertDelivery
	if err := d.DB.First(&delivery, deliveryID).Error; err != nil {
		return
	}
	var alert models.Alert
	if err := d.DB.First(&alert, delivery.AlertID).Error; err != nil {
		d.DB.Model(&delivery).Update("status", "failed")
		return
	}
	payload, _ := json.Marshal(map[string]any{
		"kind": alert.Kind, "level": alert.Level, "message": alert.Message,
		"device_id": alert.DeviceID, "created_at": alert.CreatedAt,
	})
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, d.WebhookURL, bytesReader(payload))
	if err == nil {
		req.Header.Set("Content-Type", "application/json")
		var resp *http.Response
		resp, err = client.Do(req)
		if err == nil {
			resp.Body.Close()
			if resp.StatusCode >= 200 && resp.StatusCode < 300 {
				d.DB.Model(&models.AlertDelivery{}).Where("id=?", deliveryID).Updates(map[string]any{
					"status": "delivered", "delivered_at": time.Now().UTC(), "attempt": delivery.Attempt + 1,
				})
				return
			}
			err = errStatus{resp.StatusCode}
		}
	}
	attempt := delivery.Attempt + 1
	lastErr := ""
	if err != nil {
		lastErr = err.Error()
	}
	if attempt >= 3 {
		d.DB.Model(&models.AlertDelivery{}).Where("id=?", deliveryID).Updates(map[string]any{
			"status": "failed", "attempt": attempt, "last_error": truncate(lastErr, 500),
		})
		log.Printf("webhook 投递失败（放弃）: delivery=%d err=%v", deliveryID, err)
		return
	}
	d.DB.Model(&models.AlertDelivery{}).Where("id=?", deliveryID).Updates(map[string]any{
		"attempt": attempt, "last_error": truncate(lastErr, 500),
		"next_retry_at": time.Now().UTC().Add(time.Duration(attempt*attempt) * 10 * time.Second),
	})
}

type errStatus struct{ code int }

func (e errStatus) Error() string { return "webhook 返回状态码异常" }

func truncate(s string, n int) string {
	if len(s) > n {
		return s[:n]
	}
	return s
}
