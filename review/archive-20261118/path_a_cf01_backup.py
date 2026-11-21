"""CF01 controlled backups: GZ baseline + R2 config original + state snapshot.

Writes into backups/cf2d-golive-20261118/ (gitignored). Config originals contain
credentials and stay in the controlled dir; review/ gets only sanitized evidence.
"""
from pathlib import Path
import datetime as dt
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "backups" / "cf2d-golive-20261118"
HELPER = ROOT / "review/fleet-qos-readonly-audit-20261117.py"

ns = {"__file__": str(HELPER)}
prefix, sep, _ = HELPER.read_text(encoding="utf-8-sig").partition("\ntry:\n")
exec(compile(prefix, "<readonly-helper>", "exec"), ns)
connect, run, get_json = ns["connect"], ns["run"], ns["get_json"]


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def save(name: str, text: str) -> dict:
    path = OUT / name
    if path.exists():
        raise SystemExit(f"refusing to overwrite {path}")
    path.write_text(text, encoding="utf-8", newline="")
    return {"file": name, "bytes": len(text.encode()), "sha256": sha256_text(text)}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    index = {"created": dt.datetime.now().astimezone().isoformat(timespec="seconds"), "items": []}

    # 1) GZ baseline (read-only SQL)
    gz = connect("134.175.151.213", 28722, "ubuntu", ns["DEV_PW"])
    try:
        base = run(gz, """date -Is
systemctl is-active gz-api nginx postgresql
sha256sum /opt/mgmt/server
sudo -u postgres psql routermgmt -X -t -A -F '|' <<'SQL'
SELECT 'schema',COALESCE(max(version),0) FROM schema_migrations;
SELECT 'dev',id,current_config_version,desired_config_version,highest_accepted_version,
to_char(last_heartbeat_at,'MM-DD HH24:MI'),lifecycle_status FROM devices ORDER BY id;
SELECT 'node',id,name,host,port,proto,purpose,priority,enabled FROM nodes ORDER BY id;
SELECT 'assign',a.device_id,a.node_id,a.is_primary,a.failover_priority
FROM node_assignments a ORDER BY a.device_id,a.is_primary DESC,a.failover_priority,a.node_id;
SELECT 'task_active',count(*) FROM tasks WHERE status IN ('pending','running');
SELECT 'ver_max',max(version) FROM configs;
SELECT 'mig_record',version,name FROM schema_migrations ORDER BY version;
SQL
""", timeout=40)
        index["items"].append(save("gz-baseline-2038.txt", base["stdout"] + "\n"))

        # 2) GZ DB custom dump (postgres-owned dir per 分册03 §3.1)
        dump = run(gz, """set -e
D=/tmp/cf2d-backup-$$
sudo -u postgres mkdir -p "$D"
sudo -u postgres pg_dump routermgmt -Fc -f "$D/routermgmt.dump"
sudo -u postgres pg_restore --list "$D/routermgmt.dump" | head -5
sudo chown ubuntu "$D/routermgmt.dump"
echo "DUMP_PATH=$D/routermgmt.dump"
ls -la "$D/routermgmt.dump"
""", timeout=120)
        index["items"].append(save("gz-dump-pgrestore-list.txt", dump["stdout"] + "\n" + dump["stderr"][:500] + "\n"))
        # keep dump on GZ for isolated rehearsal; record path only (sensitive)

        # 3) R2 original config + state
        r2 = connect("127.0.0.1", 13223, "root", ns["DEV_PW"], gz)
        try:
            cfg = run(r2, "cat /etc/mihomo/config.yaml", timeout=30)
            if cfg["rc"] != 0:
                raise RuntimeError(f"R2 config read failed: {cfg['stderr'][:200]}")
            index["items"].append(save("r2-config-orig-v39.yaml", cfg["stdout"] + "\n"))

            state = run(r2, """date '+%F %T %z'
md5sum /etc/mihomo/config.yaml
uci show dhcp | grep -E "server=|rebind" | head -10
cat /etc/routermgmt/state/proxy-meter-v3.json 2>/dev/null | head -3
ls /etc/routermgmt/state/ 2>/dev/null
""", timeout=30)
            index["items"].append(save("r2-state-2038.txt", state["stdout"] + "\n"))

            groups = {}
            for name in ("PROXY", "AUTO", "NODE-PICK", "US-MEDIA", "DOWNLOAD"):
                groups[name] = get_json(r2, "curl --noproxy '*' -fsS -m 5 http://127.0.0.1:9090/proxies/" + name)
            index["items"].append(save("r2-groups-9090-2038.json", json.dumps(groups, ensure_ascii=False, indent=2)))
        finally:
            r2.close()
    finally:
        gz.close()

    (OUT / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(index, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
