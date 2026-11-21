"""03-b step2b: fixed rehearsal (DATA_DIR writable, real trigger test, postgres-owned dirs)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "review/fleet-qos-readonly-audit-20261117.py"
ns = {"__file__": str(HELPER)}
prefix, sep, _ = HELPER.read_text(encoding="utf-8-sig").partition("\ntry:\n")
exec(compile(prefix, "<readonly-helper>", "exec"), ns)
connect, run = ns["connect"], ns["run"]

gz = connect("134.175.151.213", 28722, "ubuntu", ns["DEV_PW"])
try:
    r1 = run(gz, r"""sudo -u postgres mkdir -p /tmp/cf2d-migdir /tmp/cf2d-olddir2 /tmp/cf2d-newdir2
sudo -u postgres env MIGRATE_DATABASE_URL="host=/var/run/postgresql user=postgres dbname=cf2d_rehearsal" \
  DATA_DIR=/tmp/cf2d-migdir /tmp/server-cf2d-candidate -migrate
echo "MIGRATE_RC=$?"
""", timeout=120)
    print("MIGRATE:", r1["stdout"], r1["stderr"][:300])

    r2 = run(gz, """sudo -u postgres psql cf2d_rehearsal -X -t -A -F '|' <<'SQL'
SELECT 'schema',max(version) FROM schema_migrations;
SELECT 'purpose_dist',purpose,count(*) FROM nodes GROUP BY purpose;
SELECT 'chk',conname FROM pg_constraint WHERE conname='chk_node_purpose';
SELECT 'trg',tgname FROM pg_trigger WHERE tgname='trg_primary_ordinary';
SQL
""", timeout=60)
    print("VERIFY:\n", r2["stdout"])

    # real trigger test: node 14 purpose=video as primary must raise exception
    r3 = run(gz, r"""sudo -u postgres psql cf2d_rehearsal -X 2>&1 <<'SQL' | tail -4
BEGIN;
SAVEPOINT s1;
INSERT INTO node_assignments(device_id,node_id,is_primary) VALUES ('rtr-a9bcba',14,true);
ROLLBACK TO SAVEPOINT s1;
INSERT INTO node_assignments(device_id,node_id,is_primary) VALUES ('rtr-a9bcba',14,false);
SQL
echo "TRIGGER_RC=$?"
""", timeout=30)
    print("TRIGGER-TEST:", r3["stdout"], r3["stderr"][:200])

    for tag, port, bindir, datadir in (("OLD", 18080, "/opt/mgmt/server", "/tmp/cf2d-olddir2"),
                                       ("NEW", 18081, "/tmp/server-cf2d-candidate", "/tmp/cf2d-newdir2")):
        r = run(gz, rf"""sudo -u postgres env DATABASE_URL="host=/var/run/postgresql user=postgres dbname=cf2d_rehearsal" \
  MASTER_KEY=0000000000000000000000000000000000000000000000000000000000000000 \
  LISTEN=127.0.0.1:{port} DATA_DIR={datadir} \
  {bindir} >/tmp/{tag.lower()}-serve2.log 2>&1 &
PID=$!
sleep 4
if curl -fsS -m 4 http://127.0.0.1:{port}/api/v1/health >/dev/null 2>&1; then echo "{tag}_SERVE=HEALTH_OK"; else echo "{tag}_SERVE=HEALTH_FAIL"; fi
kill $PID 2>/dev/null; sleep 1
grep -v "公钥\|ssh" /tmp/{tag.lower()}-serve2.log | tail -3
""", timeout=60)
        print(f"{tag}-COMPAT:", r["stdout"], r["stderr"][:200])
finally:
    gz.close()
