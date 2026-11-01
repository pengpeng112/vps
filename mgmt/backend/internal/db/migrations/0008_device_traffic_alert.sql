-- 0008: 设备级小时流量预警阈值（WAN 下行 MB/小时，0=关闭；2026-10-03 高流量分析阶段一）
-- 评估在 agent 心跳路径（httpserver/agent.go），超标进 alerts（每小时内去重）。
ALTER TABLE devices ADD COLUMN IF NOT EXISTS traffic_alert_hourly_mb INTEGER NOT NULL DEFAULT 0;
