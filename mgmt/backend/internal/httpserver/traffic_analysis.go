package httpserver

import (
	"net/http"
	"strconv"

	"github.com/gin-gonic/gin"

	"routermgmt/internal/models"
)

// handleDeviceTrafficHourly 高流量分析阶段一（2026-10-03）：
// metrics 相邻样本差值按小时聚合（Asia/Shanghai），供「流量分析」页展示分时段用量与峰值。
// 计数器回退（重启/清零）不产生负流量；peak 为桶内最大样本速率（收+发，Mbps）。
func (s *Server) handleDeviceTrafficHourly(c *gin.Context) {
	var dev models.Device
	if err := s.DB.Where("id=?", c.Param("id")).First(&dev).Error; err != nil {
		fail404(c, "设备不存在")
		return
	}
	hours := 168
	if v := c.Query("hours"); v != "" {
		n, err := strconv.Atoi(v)
		if err != nil || n < 1 || n > 720 {
			fail422(c, "hours 范围 1..720")
			return
		}
		hours = n
	}
	type hourlyRow struct {
		Hour     string  `json:"hour"`
		RxMB     float64 `json:"rx_mb"`
		TxMB     float64 `json:"tx_mb"`
		PeakMbps float64 `json:"peak_mbps"`
		Samples  int64   `json:"samples"`
	}
	var rows []hourlyRow
	err := s.DB.Raw(`
WITH s AS (
	SELECT ts, wan_rx_bytes, wan_tx_bytes,
	       LAG(wan_rx_bytes) OVER w AS prx, LAG(wan_tx_bytes) OVER w AS ptx, LAG(ts) OVER w AS pts
	FROM metrics
	WHERE device_id = ? AND ts >= now() - (? * interval '1 hour')
	  AND wan_rx_bytes IS NOT NULL AND wan_tx_bytes IS NOT NULL
	WINDOW w AS (ORDER BY ts)
), d AS (
	SELECT ts,
	       GREATEST(wan_rx_bytes - prx, 0) AS rx,
	       GREATEST(wan_tx_bytes - ptx, 0) AS tx,
	       EXTRACT(EPOCH FROM (ts - pts)) AS dt
	FROM s WHERE prx IS NOT NULL AND ptx IS NOT NULL
)
SELECT to_char(date_trunc('hour', ts AT TIME ZONE 'Asia/Shanghai'), 'YYYY-MM-DD HH24:00') AS hour,
       COALESCE(SUM(rx), 0) / 1048576.0 AS rx_mb,
       COALESCE(SUM(tx), 0) / 1048576.0 AS tx_mb,
       COALESCE(MAX(CASE WHEN dt > 0 THEN (rx + tx) * 8 / dt / 1000000 ELSE 0 END), 0) AS peak_mbps,
       COUNT(*) AS samples
FROM d GROUP BY hour ORDER BY hour`, dev.ID, hours).Scan(&rows).Error
	if err != nil {
		fail500(c, "查询失败")
		return
	}
	var totalRx, totalTx float64
	for _, r := range rows {
		totalRx += r.RxMB
		totalTx += r.TxMB
	}
	c.JSON(http.StatusOK, gin.H{
		"device_id": dev.ID, "hours": hours, "items": rows,
		"total_rx_mb": totalRx, "total_tx_mb": totalTx,
		"hourly_alert_mb": dev.TrafficAlertHourlyMB,
	})
}
