"""03-c: production migrate + binary swap + restart + verify (gz-prod lock held).

Sequence (rollback-compatible order):
  0. pre-check: no active tasks, schema=9
  1. -migrate production (peer auth; candidate binary)
  2. verify schema=10
  3. backup old binary, install candidate atomically, restart gz-api
  4. verify active/health/running SHA
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "review/fleet-qos-readonly-audit-20261117.py"
ns = {"__file__": str(HELPER)}
prefix, sep, _ = HELPER.read_text(encoding="utf-8-sig").partition("\ntry:\n")
exec(compile(prefix, "<readonly-helper>", "exec"), ns)
connect, run = ns["connect"], ns["run"]

CAND_SHA = "1d0d88299ea25ba6964bcc32f4f1b01a9684282a2fb9ba5820aca03ef7a01a05"

gz = connect("134.175.151.213", 28722, "ubuntu", ns["DEV_PW"])
try:
    pre = run(gz, """sudo -u postgres psql routermgmt -X -t -A -c "select max(version) from schema_migrations" -c "select count(*) from tasks where status in ('pending','running')"
sha256sum /tmp/server-cf2d-candidate /opt/mgmt/server | awk '{print substr($1,1,16), $2}'
""", timeout=30)
    print("PRE:\n", pre["stdout"])
    schema, active = pre["stdout"].splitlines()[:2]
    if schema.strip() != "9":
        print(f"ABORT: 生产 schema 非 9（实际 {schema}）"); sys.exit(1)
    if active.strip() != "0":
        print(f"ABORT: 有活跃任务 {active}"); sys.exit(1)

    r1 = run(gz, r"""sudo -u postgres env MIGRATE_DATABASE_URL="host=/var/run/postgresql user=postgres dbname=routermgmt" \
  DATA_DIR=/tmp/cf2d-migdir /tmp/server-cf2d-candidate -migrate
echo "MIGRATE_RC=$?"
sudo -u postgres psql routermgmt -X -t -A -c "select max(version) from schema_migrations"
""", timeout=120)
    print("MIGRATE:\n", r1["stdout"], r1["stderr"][:300])
    if "MIGRATE_RC=0" not in r1["stdout"] or not r1["stdout"].rstrip().endswith("10"):
        print("ABORT: 迁移未达 10，保持旧二进制不动"); sys.exit(1)

    r2 = run(gz, rf"""set -e
if sha256sum /tmp/server-cf2d-candidate | grep -q {CAND_SHA[:32]}; then :; else echo CAND_SHA_MISMATCH; exit 1; fi
sudo cp -a /opt/mgmt/server /opt/mgmt/server.bak-cf2d-20261118
sudo install -m 755 /tmp/server-cf2d-candidate /opt/mgmt/server.new-cf2d
sudo mv /opt/mgmt/server.new-cf2d /opt/mgmt/server
sha256sum /opt/mgmt/server | cut -c1-16
sudo systemctl restart gz-api
sleep 3
systemctl is-active gz-api
curl -fsS -m 5 http://127.0.0.1:8080/api/v1/health
echo
ps -o pid,lstart,cmd -C server --no-headers | head -2
""", timeout=90)
    print("SWAP+RESTART:\n", r2["stdout"], r2["stderr"][:400])
finally:
    gz.close()
