// Package traffic 流量统计口径（任务书 §19.5）：
//   - 心跳带 sample_id，(device_id, sample_id) 唯一，重复/乱序样本不重复累加
//   - 同一计数器周期内取相邻有效样本差值；首样本仅建立基线
//   - boot_id 变化重置 WAN 基线；proxy_counter_epoch 变化重置代理基线
//   - 计数器下降重新建立基线，不产生负流量
//   - 月份按 Asia/Shanghai；有效增量归到当前样本所在月份
//   - 缺数据用 null/unavailable 表示，不当作 0
package traffic

import (
	"time"

	"gorm.io/gorm"

	"routermgmt/internal/models"
)

var shanghai *time.Location

func init() {
	var err error
	shanghai, err = time.LoadLocation("Asia/Shanghai")
	if err != nil {
		shanghai = time.FixedZone("CST", 8*3600)
	}
}

// MonthKey 返回 Asia/Shanghai 的 YYYY-MM。
func MonthKey(t time.Time) string {
	return t.In(shanghai).Format("2006-01")
}

type Service struct{}

func New() *Service { return &Service{} }

// Ingest 在事务内完成：指标入库（去重）→ 差值计算 → 月聚合。
// 返回 (是否新样本, 本月代理流量合计, err)。
func (s *Service) Ingest(tx *gorm.DB, m *models.Metric) (bool, int64, error) {
	res := tx.Exec(`INSERT INTO metrics
		(device_id, ts, cpu, mem_used_mb, mem_total_mb, temp_c, disk_free_mb, uptime_s,
		 wan_ip, exit_ip, exit_node, exit_latency_ms, clash_alive, wg_alive,
		 wan_rx_bytes, wan_tx_bytes, proxy_rx_bytes, proxy_tx_bytes, config_version,
		 sample_id, boot_id, proxy_counter_epoch, proxy_measurement_status)
		VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
		ON CONFLICT (device_id, sample_id) DO NOTHING`,
		m.DeviceID, m.TS, m.CPU, m.MemUsedMB, m.MemTotalMB, m.TempC, m.DiskFreeMB, m.UptimeS,
		m.WanIP, m.ExitIP, m.ExitNode, m.ExitLatencyMs, m.ClashAlive, m.WGAlive,
		m.WanRxBytes, m.WanTxBytes, m.ProxyRxBytes, m.ProxyTxBytes, m.ConfigVersion,
		m.SampleID, m.BootID, m.ProxyCounterEpoch, m.ProxyMeasurementStatus)
	if res.Error != nil {
		return false, 0, res.Error
	}
	if res.RowsAffected == 0 {
		return false, 0, nil // 重复样本
	}

	var st models.TrafficState
	err := tx.Where("device_id = ?", m.DeviceID).First(&st).Error
	fresh := err != nil
	bootChanged := fresh || st.BootID != m.BootID
	proxyEpochChanged := fresh || st.CounterEpoch != m.ProxyCounterEpoch || bootChanged

	var dWanRx, dWanTx, dProxyRx, dProxyTx int64
	if !fresh && !bootChanged {
		if m.WanRxBytes != nil && *m.WanRxBytes >= st.WanRxBytes {
			dWanRx = *m.WanRxBytes - st.WanRxBytes
		}
		if m.WanTxBytes != nil && *m.WanTxBytes >= st.WanTxBytes {
			dWanTx = *m.WanTxBytes - st.WanTxBytes
		}
	}
	if !fresh && !proxyEpochChanged && m.ProxyMeasurementStatus != "unavailable" {
		if m.ProxyRxBytes != nil && *m.ProxyRxBytes >= st.ProxyRxBytes {
			dProxyRx = *m.ProxyRxBytes - st.ProxyRxBytes
		}
		if m.ProxyTxBytes != nil && *m.ProxyTxBytes >= st.ProxyTxBytes {
			dProxyTx = *m.ProxyTxBytes - st.ProxyTxBytes
		}
	}

	// 基线总是更新为最新累计值
	newState := models.TrafficState{
		DeviceID: m.DeviceID, BootID: m.BootID, CounterEpoch: m.ProxyCounterEpoch,
		HasBaseline: true, UpdatedAt: m.TS,
	}
	if v := val(m.WanRxBytes); v != nil {
		newState.WanRxBytes = *v
	} else if !bootChanged && !fresh {
		newState.WanRxBytes = st.WanRxBytes
	}
	if v := val(m.WanTxBytes); v != nil {
		newState.WanTxBytes = *v
	} else if !bootChanged && !fresh {
		newState.WanTxBytes = st.WanTxBytes
	}
	if m.ProxyMeasurementStatus != "unavailable" {
		if v := val(m.ProxyRxBytes); v != nil {
			newState.ProxyRxBytes = *v
		} else if !fresh {
			newState.ProxyRxBytes = st.ProxyRxBytes
		}
		if v := val(m.ProxyTxBytes); v != nil {
			newState.ProxyTxBytes = *v
		} else if !fresh {
			newState.ProxyTxBytes = st.ProxyTxBytes
		}
	} else if !fresh {
		newState.ProxyRxBytes = st.ProxyRxBytes
		newState.ProxyTxBytes = st.ProxyTxBytes
	}
	if err := tx.Save(&newState).Error; err != nil {
		return true, 0, err
	}

	// 月聚合：有效增量归到当前样本所在月份
	ym := MonthKey(m.TS)
	if dWanRx+dWanTx+dProxyRx+dProxyTx > 0 {
		if err := tx.Exec(`INSERT INTO monthly_traffic
			(device_id, year_month, wan_rx_bytes, wan_tx_bytes, proxy_rx_bytes, proxy_tx_bytes, updated_at)
			VALUES (?,?,?,?,?,?,?)
			ON CONFLICT (device_id, year_month) DO UPDATE SET
			  wan_rx_bytes = monthly_traffic.wan_rx_bytes + EXCLUDED.wan_rx_bytes,
			  wan_tx_bytes = monthly_traffic.wan_tx_bytes + EXCLUDED.wan_tx_bytes,
			  proxy_rx_bytes = monthly_traffic.proxy_rx_bytes + EXCLUDED.proxy_rx_bytes,
			  proxy_tx_bytes = monthly_traffic.proxy_tx_bytes + EXCLUDED.proxy_tx_bytes,
			  updated_at = EXCLUDED.updated_at`,
			m.DeviceID, ym, dWanRx, dWanTx, dProxyRx, dProxyTx, m.TS).Error; err != nil {
			return true, 0, err
		}
	}
	var monthProxy int64
	if err := tx.Raw(`SELECT COALESCE(proxy_rx_bytes,0)+COALESCE(proxy_tx_bytes,0) FROM monthly_traffic
		WHERE device_id=? AND year_month=?`, m.DeviceID, ym).Scan(&monthProxy).Error; err != nil {
		return true, 0, err
	}
	return true, monthProxy, nil
}

func val(p *int64) *int64 { return p }

// MonthTotalBytes 查询某设备当月代理流量（rx+tx）。
func MonthTotalBytes(db *gorm.DB, deviceID string, t time.Time) (int64, error) {
	var v int64
	err := db.Raw(`SELECT COALESCE(proxy_rx_bytes,0)+COALESCE(proxy_tx_bytes,0)
		FROM monthly_traffic WHERE device_id=? AND year_month=?`, deviceID, MonthKey(t)).Scan(&v).Error
	return v, err
}
