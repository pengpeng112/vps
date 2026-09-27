-- 0003: 每设备策略（VPS 限速 / 定时重启 / 授权到期）
-- vps_limit_kbps=0 表示不限速；reboot_cron 空表示不定时重启；
-- license_enabled=false 或 license_expires_at 已过 → 服务停用（禁 VPS，国内直连仍可用）。
ALTER TABLE devices ADD COLUMN IF NOT EXISTS vps_limit_kbps INT NOT NULL DEFAULT 0;
ALTER TABLE devices ADD COLUMN IF NOT EXISTS reboot_cron VARCHAR(64) NOT NULL DEFAULT '';
ALTER TABLE devices ADD COLUMN IF NOT EXISTS license_enabled BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE devices ADD COLUMN IF NOT EXISTS license_expires_at TIMESTAMPTZ;

ALTER TABLE devices DROP CONSTRAINT IF EXISTS devices_vps_limit_kbps_check;
ALTER TABLE devices ADD CONSTRAINT devices_vps_limit_kbps_check CHECK (vps_limit_kbps >= 0 AND vps_limit_kbps <= 1000000);
