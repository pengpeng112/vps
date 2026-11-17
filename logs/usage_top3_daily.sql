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
  SELECT dn.name AS dev,
         date_trunc('day', d.ts AT TIME ZONE 'Asia/Shanghai') AS day,
         SUM(d.rx) AS rx, SUM(d.tx) AS tx
  FROM d JOIN devices dn ON dn.id = d.device_id
  WHERE dn.name LIKE 'SN-R1%' OR dn.name LIKE 'SN-R6%' OR dn.name LIKE '%雅典娜-AX6600'
  GROUP BY dn.name, 2
)
SELECT dev, to_char(day, 'MM-DD') AS d, round(SUM(rx)/1073741824.0,2) AS rx_gb, round(SUM(tx)/1073741824.0,2) AS tx_gb
FROM agg GROUP BY dev, day HAVING SUM(rx+tx) > 104857600
ORDER BY dev, day;
