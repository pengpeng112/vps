BEGIN READ ONLY;
SET LOCAL statement_timeout='30s';
WITH s AS (
 SELECT device_id,ts,proxy_counter_epoch,proxy_rx_bytes rx,proxy_tx_bytes tx,
 LAG(proxy_rx_bytes) OVER w prx,LAG(proxy_tx_bytes) OVER w ptx,LAG(proxy_counter_epoch) OVER w pe
 FROM metrics WHERE ts >= '2026-10-16 00:00+08' AND proxy_rx_bytes IS NOT NULL AND proxy_tx_bytes IS NOT NULL
 WINDOW w AS (PARTITION BY device_id ORDER BY ts,id)
),d AS (
 SELECT device_id,ts,
 CASE WHEN proxy_counter_epoch=pe THEN GREATEST(rx-prx,0) ELSE 0 END drx,
 CASE WHEN proxy_counter_epoch=pe THEN GREATEST(tx-ptx,0) ELSE 0 END dtx,
 CASE WHEN proxy_counter_epoch=pe AND (rx<prx OR tx<ptx) THEN 1 ELSE 0 END decreases,
 CASE WHEN proxy_counter_epoch IS DISTINCT FROM pe THEN 1 ELSE 0 END epochs
 FROM s WHERE prx IS NOT NULL
),daily AS (
 SELECT device_id,(ts AT TIME ZONE 'Asia/Shanghai')::date AS bucket_day,SUM(drx) AS rx,SUM(dtx) AS tx,SUM(decreases) AS decreases,SUM(epochs) AS epochs,COUNT(*) AS samples FROM d GROUP BY device_id,2
),ranking AS (
 SELECT v.id,v.name,
 round(SUM(d.rx+d.tx) FILTER (WHERE d.bucket_day>='2026-10-17')/1073741824.0,3) AS thirtyday_gib,
 round(SUM(d.rx+d.tx) FILTER (WHERE d.bucket_day>='2026-11-01')/1073741824.0,3) AS november_gib,
 round(SUM(d.rx) FILTER (WHERE d.bucket_day>='2026-11-01')/1073741824.0,3) AS november_rx_gib,
 round(SUM(d.tx) FILTER (WHERE d.bucket_day>='2026-11-01')/1073741824.0,3) AS november_tx_gib,
 round(MAX(d.rx+d.tx) FILTER (WHERE d.bucket_day>='2026-11-01')/1073741824.0,3) AS max_nov_daily_gib,
 SUM(decreases) FILTER (WHERE d.bucket_day>='2026-11-01') AS decreases,
 SUM(samples) FILTER (WHERE d.bucket_day>='2026-11-01') AS samples
 FROM daily d JOIN devices v ON v.id=d.device_id GROUP BY v.id,v.name ORDER BY SUM(d.rx+d.tx) DESC
)
SELECT jsonb_build_object(
'ranking',(SELECT jsonb_agg(to_jsonb(r)) FROM ranking r),
'daily',(SELECT jsonb_agg(to_jsonb(x)) FROM (SELECT v.name,a.* FROM daily a JOIN devices v ON v.id=a.device_id ORDER BY a.bucket_day,a.rx+a.tx DESC) x),
'monthly',(SELECT jsonb_agg(to_jsonb(x)) FROM (SELECT v.id,v.name,m.year_month,m.proxy_rx_bytes,m.proxy_tx_bytes,m.wan_rx_bytes,m.wan_tx_bytes FROM monthly_traffic m JOIN devices v ON v.id=m.device_id WHERE m.year_month='2026-11' ORDER BY m.proxy_rx_bytes+m.proxy_tx_bytes DESC) x)
);
COMMIT;