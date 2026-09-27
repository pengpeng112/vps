-- 0004: 运行模式 + 访问控制（远程可改）
-- proxy_mode: rule=策略分流 / global=全局走 VPS / direct=全部直连
-- access_mode: all=全部设备 / whitelist=仅名单走代理 / blacklist=名单直连
ALTER TABLE devices ADD COLUMN IF NOT EXISTS proxy_mode VARCHAR(16) NOT NULL DEFAULT 'rule';
ALTER TABLE devices ADD COLUMN IF NOT EXISTS access_mode VARCHAR(16) NOT NULL DEFAULT 'all';
ALTER TABLE devices ADD COLUMN IF NOT EXISTS access_list TEXT NOT NULL DEFAULT '';

ALTER TABLE devices DROP CONSTRAINT IF EXISTS devices_proxy_mode_check;
ALTER TABLE devices ADD CONSTRAINT devices_proxy_mode_check CHECK (proxy_mode IN ('rule', 'global', 'direct'));
ALTER TABLE devices DROP CONSTRAINT IF EXISTS devices_access_mode_check;
ALTER TABLE devices ADD CONSTRAINT devices_access_mode_check CHECK (access_mode IN ('all', 'whitelist', 'blacklist'));
