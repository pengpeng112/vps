-- 0006: 反向 SSH 排障口健康（2026-09-24 阶段1C）
-- remote_ssh_alive：VPS 侧 mgmt-rssh-probe 对该设备 -R 回环口（18222/13222…）的
--   banner 探测结果；NULL=从未检测（区别于 false）
-- last_remote_ssh_check_at：最近一次探测时间
ALTER TABLE devices ADD COLUMN IF NOT EXISTS remote_ssh_alive BOOLEAN;
ALTER TABLE devices ADD COLUMN IF NOT EXISTS last_remote_ssh_check_at TIMESTAMPTZ;
