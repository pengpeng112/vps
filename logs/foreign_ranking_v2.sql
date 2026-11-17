WITH s AS (
  SELECT device_id, ts, proxy_counter_epoch,
         proxy_rx_bytes AS prb, proxy_tx_bytes AS ptxb,
         LAG(proxy_rx_bytes) OVER w AS prx, LAG(proxy_tx_bytes) OVER w AS ptx,
         LAG(proxy_counter_epoch) OVER w AS pepoch
  FROM metrics
  WHERE ts >= now() - interval '14 days'
    AND proxy_rx_bytes IS NOT NULL AND proxy_tx_bytes IS NOT NULL
  WINDOW w AS (PARTITION BY device_id ORDER BY ts)
), d AS (
  SELECT device_id,
         CASE WHEN proxy_counter_epoch = pepoch THEN GREATEST(prb - prx, 0) ELSE 0 END AS rx,
         CASE WHEN proxy_counter_epoch = pepoch THEN GREATEST(ptxb - ptx, 0) ELSE 0 END AS tx
  FROM s WHERE prx IS NOT NULL
)
SELECT dn.name AS device,
       round(SUM(d.rx)/1048576.0) AS foreign_rx_mb,
       round(SUM(d.tx)/1048576.0) AS foreign_tx_mb,
       round((SUM(d.rx)+SUM(d.tx))/1048576.0) AS foreign_total_mb
FROM d JOIN devices dn ON dn.id = d.device_id
GROUP BY dn.name ORDER BY foreign_total_mb DESC;
