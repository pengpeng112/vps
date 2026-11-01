#!/bin/sh
# fleet-inspect GZ 侧（Codex 2026-11-01）：sshd 限流排查 + DB 三方对拍（只读）
date -u
systemctl is-active nginx gz-api
ss -ltnp | awk 'NR==1 || $4 ~ /:(8443|8080|132[0-9][0-9])$/'

sshd -T |
    awk '/^(maxstartups|persourcemaxstartups|persourcepenalties|maxsessions|logingracetime) /'
journalctl -u ssh -u sshd --since '-15min' --no-pager |
    grep -Ei 'MaxStartups|drop connection|penalt|refus|throttl' || true

sudo -u postgres psql -X -v ON_ERROR_STOP=1 -d routermgmt <<'SQL'
BEGIN READ ONLY;
SELECT clock_timestamp() AS observed_at;

SELECT d.name,d.id,
       round(extract(epoch FROM (now()-d.last_heartbeat_at))) AS hb_age_s,
       d.current_node_id,d.current_config_version,d.desired_config_version,
       d.highest_accepted_version,c.sha256 AS desired_sha256
FROM devices d LEFT JOIN configs c
  ON c.device_id=d.id AND c.version=d.desired_config_version
WHERE d.lifecycle_status='approved'
ORDER BY d.name;

SELECT d.name,a.node_id,n.name,n.host,n.port,
       a.is_primary,a.failover_priority,n.priority
FROM node_assignments a
JOIN devices d ON d.id=a.device_id
JOIN nodes n ON n.id=a.node_id
WHERE d.lifecycle_status='approved' AND n.enabled=true
ORDER BY d.name,a.is_primary DESC,a.failover_priority,n.priority DESC,n.id;
ROLLBACK;
SQL
