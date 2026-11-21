"""Path-A preflight probe: read-only GZ baseline (no API login, no writes)."""
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "review/fleet-qos-readonly-audit-20261117.py"

ns = {"__file__": str(HELPER)}
prefix, sep, _ = HELPER.read_text(encoding="utf-8-sig").partition("\ntry:\n")
if not sep:
    raise SystemExit("helper boundary changed")
exec(compile(prefix, "<readonly-helper>", "exec"), ns)
connect, run = ns["connect"], ns["run"]

gz = connect("134.175.151.213", 28722, "ubuntu", ns["DEV_PW"])
try:
    out = run(gz, """date -Is
systemctl is-active gz-api nginx postgresql
stat -c '%y %s' /opt/mgmt/server
sha256sum /opt/mgmt/server
sudo -u postgres psql routermgmt -X -t -A -F '|' <<'SQL'
SELECT 'schema',COALESCE(max(version),0) FROM schema_migrations;
SELECT 'dev',id,current_config_version,desired_config_version,highest_accepted_version,
to_char(last_heartbeat_at,'MM-DD HH24:MI'),lifecycle_status FROM devices ORDER BY id;
SELECT 'task',id,device_id,type,status,created_at FROM tasks WHERE status IN ('pending','running') OR created_at>now()-interval '2 hours' ORDER BY id DESC LIMIT 12;
SELECT 'node',id,name,host,port,proto,purpose,priority,enabled FROM nodes ORDER BY id;
SELECT 'assign',a.device_id,a.node_id,n.name,a.is_primary,a.failover_priority
FROM node_assignments a JOIN nodes n ON n.id=a.node_id ORDER BY a.device_id,a.is_primary DESC,a.failover_priority,n.id;
SELECT 'ver',max(version) FROM configs;
SQL
""", timeout=40)
    print(out["stdout"])
    if out["stderr"]:
        print("STDERR:", out["stderr"][:500])
finally:
    gz.close()
