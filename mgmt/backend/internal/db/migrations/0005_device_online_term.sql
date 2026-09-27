-- 0005: 上线时间 + 期限起算（均可人工改）
-- first_online_at：第一次心跳写入，之后心跳不再覆盖
-- license_started_at：本轮期限开始；续期时更新为现在
ALTER TABLE devices ADD COLUMN IF NOT EXISTS first_online_at TIMESTAMPTZ;
ALTER TABLE devices ADD COLUMN IF NOT EXISTS license_started_at TIMESTAMPTZ;

UPDATE devices
   SET first_online_at = last_heartbeat_at
 WHERE first_online_at IS NULL AND last_heartbeat_at IS NOT NULL;

UPDATE devices
   SET license_started_at = COALESCE(first_online_at, created_at)
 WHERE license_started_at IS NULL;
