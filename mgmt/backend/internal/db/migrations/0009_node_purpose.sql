-- 0009_node_purpose.sql: explicit per-node traffic purpose for render isolation.
-- ordinary = normal proxy candidates (PROXY/AUTO/NODE-PICK)
-- video    = video/bulk class only (US-MEDIA members), excluded from ordinary groups
ALTER TABLE nodes ADD COLUMN purpose TEXT NOT NULL DEFAULT 'ordinary';
-- existing BULK node becomes video class (US-MEDIA member, excluded from ordinary groups)
UPDATE nodes SET purpose = 'video' WHERE name LIKE 'BULK-%';
