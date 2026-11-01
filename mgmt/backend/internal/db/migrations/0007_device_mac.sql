-- 0007: 设备标签/主 MAC 地址（人工维护，列表展示与物理设备识别；2026-09-28）
ALTER TABLE devices ADD COLUMN IF NOT EXISTS mac_addr VARCHAR(32) DEFAULT '';
