"""03-b step2: migrate scratch with candidate, verify 0010, old-binary serve compat test."""
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "review/fleet-qos-readonly-audit-20261117.py"
ns = {"__file__": str(HELPER)}
prefix, sep, _ = HELPER.read_text(encoding="utf-8-sig").partition("\ntry:\n")
exec(compile(prefix, "<readonly-helper>", "exec"), ns)
connect, run = ns["connect"], ns["run"]

gz = connect("134.175.151.213", 28722, "ubuntu", ns["DEV_PW"])
try:
    # 1) -migrate on scratch (peer auth via unix socket, no credentials)
    r1 = run(gz, """sudo -u postgres env MIGRATE_DATABASE_URL="host=/var/run/postgresql user=postgres dbname=cf2d_rehearsal" /tmp/server-cf2d-candidate -migrate
echo "MIGRATE_RC=$?"
""", timeout=120)
    print("MIGRATE:", r1["stdout"], r1["stderr"][:300])

    # 2) verify schema/constraints/trigger/data
    r2 = run(gz, """sudo -u postgres psql cf2d_rehearsal -X -t -A -F '|' <<'SQL'
SELECT 'schema',max(version) FROM schema_migrations;
SELECT 'mig10',version FROM schema_migrations WHERE version=10;
SELECT 'purpose_dist',purpose,count(*) FROM nodes GROUP BY purpose;
SELECT 'chk',conname FROM pg_constraint WHERE conname='chk_node_purpose';
SELECT 'trg',tgname,pg_get_triggerdef(oid) FROM pg_trigger WHERE tgname='trg_primary_ordinary';
SELECT 'primary_purpose',n.name,n.purpose FROM node_assignments a JOIN nodes n ON n.id=a.node_id WHERE a.is_primary AND n.purpose<>'ordinary';
SQL
""", timeout=60)
    print("VERIFY:", r2["stdout"])

    # 3) constraint behavior: try inserting video primary (should fail)
    r3 = run(gz, """sudo -u postgres psql cf2d_rehearsal -X -c "BEGIN; INSERT INTO node_assignments(device_id,node_id,is_primary) SELECT 'rtr-a9bcba',14,true ON CONFLICT DO NOTHING; ROLLBACK;" 2>&1 | head -3
""", timeout=30)
    print("TRIGGER-TEST(video primary should raise):", r3["stdout"], r3["stderr"][:200])

    # 4) OLD binary serve-mode compat against migrated scratch (EnsureCurrent applied=10 >= embedded 9)
    r4 = run(gz, """set -e
rm -rf /tmp/cf2d-olddir && mkdir -p /tmp/cf2d-olddir
sudo -u postgres env DATABASE_URL="host=/var/run/postgresql user=postgres dbname=cf2d_rehearsal" \\
  MASTER_KEY=0000000000000000000000000000000000000000000000000000000000000000 \\
  LISTEN=127.0.0.1:18080 DATA_DIR=/tmp/cf2d-olddir \\
  /opt/mgmt/server >/tmp/old-serve.log 2>&1 &
OLDPID=$!
sleep 4
if curl -fsS -m 4 http://127.0.0.1:18080/api/v1/health >/dev/null 2>&1; then echo "OLD_SERVE=HEALTH_OK"; else echo "OLD_SERVE=HEALTH_FAIL"; fi
kill $OLDPID 2>/dev/null || true
sleep 1
tail -5 /tmp/old-serve.log | grep -v "公钥\|ssh"
""", timeout=60)
    print("OLD-COMPAT:", r4["stdout"], r4["stderr"][:300])

    # 5) NEW binary serve-mode on scratch too (full stack sanity, scratch port)
    r5 = run(gz, """set -e
rm -rf /tmp/cf2d-newdir && mkdir -p /tmp/cf2d-newdir
sudo -u postgres env DATABASE_URL="host=/var/run/postgresql user=postgres dbname=cf2d_rehearsal" \\
  MASTER_KEY=0000000000000000000000000000000000000000000000000000000000000000 \\
  LISTEN=127.0.0.1:18081 DATA_DIR=/tmp/cf2d-newdir AIRPORT_SUB_URL= AIRPORT_GENERATION= \\
  /tmp/server-cf2d-candidate >/tmp/new-serve.log 2>&1 &
NEWPID=$!
sleep 4
if curl -fsS -m 4 http://127.0.0.1:18081/api/v1/health >/dev/null 2>&1; then echo "NEW_SERVE=HEALTH_OK"; else echo "NEW_SERVE=HEALTH_FAIL"; fi
kill $NEWPID 2>/dev/null || true
sleep 1
tail -3 /tmp/new-serve.log | grep -v "公钥\|ssh"
""", timeout=60)
    print("NEW-COMPAT:", r5["stdout"], r5["stderr"][:300])
finally:
    gz.close()
