WITH s AS (
  SELECT device_id, ts, exit_node, proxy_counter_epoch,
         proxy_rx_bytes AS prb, proxy_tx_bytes AS ptxb,
         LAG(proxy_rx_bytes) OVER w AS prx, LAG(proxy_tx_bytes) OVER w AS ptx,
         LAG(proxy_counter_epoch) OVER w AS pepoch
  FROM metrics
  WHERE ts >= now() - interval '30 days'
    AND proxy_rx_bytes IS NOT NULL AND proxy_tx_bytes IS NOT NULL
  WINDOW w AS (PARTITION BY device_id ORDER BY ts)
), d AS (
  SELECT device_id, ts, exit_node,
         CASE WHEN proxy_counter_epoch = pepoch THEN GREATEST(prb - prx, 0) ELSE 0 END AS rx,
         CASE WHEN proxy_counter_epoch = pepoch THEN GREATEST(ptxb - ptx, 0) ELSE 0 END AS tx
  FROM s WHERE prx IS NOT NULL
), agg AS (
  SELECT dn.name AS dev, d.exit_node AS node,
         date_trunc('day', d.ts AT TIME ZONE 'Asia/Shanghai') AS day,
         SUM(d.rx) AS rx, SUM(d.tx) AS tx
  FROM d JOIN devices dn ON dn.id = d.device_id
  GROUP BY dn.name, d.exit_node, 3
)
-- 1) 30d per-device ranking
SELECT 'RANK30' AS tag, dev AS k,
       round(SUM(rx)/1073741824.0, 2) AS rx_gb,
       round(SUM(tx)/1073741824.0, 2) AS tx_gb,
       round(SUM(rx+tx)/1073741824.0, 2) AS total_gb,
       ''::text AS note
FROM agg GROUP BY dev
UNION ALL
-- 2) attribution by exit node
SELECT 'BYNODE', COALESCE(node, '(null)'),
       round(SUM(rx)/1073741824.0, 2), round(SUM(tx)/1073741824.0, 2),
       round(SUM(rx+tx)/1073741824.0, 2), ''
FROM agg GROUP BY node
UNION ALL
-- 3) daily fleet trend
SELECT 'DAILY', to_char(day, 'MM-DD'),
       NULL, NULL,
       round(SUM(rx+tx)/1073741824.0, 2), 'fleet'
FROM agg GROUP BY day
ORDER BY tag, total_gb DESC NULLS LAST, k;
