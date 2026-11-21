"""R2 meter-v3 migration precheck (read-only on production state).

- GZ: service health; read-only psql (schema_migrations/R2 tasks/node-14 bindings);
      isolated scratch DB applies migrations 0001..0010 and asserts 0010 constraints,
      then drops the scratch DB (never touches routermgmt DB rows).
- R2 (via GZ 13223 jump): cron/trigger references for the dangerous v1 controller,
      hashes/state of deployed meter, iptables chains/jumps, python3/TZ, group state,
      endpoints generator PREVIEW written only under /tmp (no production file writes).
Credentials parsed in memory from the controlled archive; no secrets in output.
"""
from pathlib import Path
import datetime
import json
import paramiko

ROOT = Path(__file__).resolve().parents[2]
MIGR = ROOT / "mgmt/backend/internal/db/migrations"
OUT = ROOT / "logs/r2-meter-v3-precheck-20261117.json"
SCRATCH = "routermgmt_migtest_20261117"
BT = chr(96)


def controlled_row(path, name):
    for line in (ROOT / path).read_text(encoding="utf-8-sig").splitlines():
        cells = [x.strip() for x in line.strip().strip("|").split("|")]
        if line.startswith("|") and cells[0] == name:
            return "|".join(cells[1:])
    raise ValueError("controlled credential field missing: %s" % name)


def quoted(value):
    return __import__("re").findall(BT + "([^" + BT + "]+)" + BT, value)


DEV_PW = quoted(controlled_row("backups/SN-R系列凭证总览.md", "**root/SSH 口令**"))[0]


def connect(host, port, user, pw, jump=None):
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    sock = None
    if jump:
        sock = jump.get_transport().open_channel("direct-tcpip", (host, port), ("127.0.0.1", 0), timeout=15)
    try:
        c.connect(host, port=port, username=user, password=pw, sock=sock,
                  timeout=15, banner_timeout=15, auth_timeout=15,
                  look_for_keys=False, allow_agent=False)
    except Exception:
        c.close()
        if sock:
            sock.close()
        raise
    return c


def run(c, script, timeout=40):
    i, o, e = c.exec_command("sh -s", timeout=timeout)
    i.write(script)
    i.channel.shutdown_write()
    return {"rc": o.channel.recv_exit_status(),
            "stdout": o.read().decode("utf-8", "replace")[-8000:],
            "stderr": e.read().decode("utf-8", "replace")[-800:]}


RES = {"started": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
       "production_state_writes": False, "scratch_db": SCRATCH, "hosts": {}}

gz = connect("134.175.151.213", 28722, "ubuntu", DEV_PW)
RES["hosts"]["GZ"] = {"health": run(gz, """date -Is
systemctl is-active gz-api nginx postgresql
curl --noproxy '*' -fsS -m 5 http://127.0.0.1:8080/api/v1/health || true
""")}
RES["hosts"]["GZ"]["db_readonly"] = run(gz, """sudo -u postgres psql -X -A -t routermgmt <<'SQL'
SELECT 'migrations', string_agg(version::text, ',' ORDER BY version) FROM schema_migrations;
SELECT 'r2row', id, config_version, highest_accepted, lifecycle_status FROM devices WHERE id='rtr-a9bcba';
SELECT 'r2tasks', count(*) FILTER (WHERE status IN ('pending','running')), max(created_at)
  FROM tasks WHERE device_id='rtr-a9bcba' AND status IN ('pending','running');
SELECT 'node14bind', device_id, is_primary FROM node_assignments WHERE node_id=14;
SELECT 'purpose14', id, name, purpose FROM nodes WHERE id IN (7,8,9,4,10,11,14) ORDER BY id;
SQL
""")

# ---- isolated scratch DB: apply 0001..0010 + assert constraints (never routermgmt) ----
mig_files = sorted(MIGR.glob("*.sql"))
create = run(gz, "sudo -u postgres psql -X -q -c 'DROP DATABASE IF EXISTS %s;' -c 'CREATE DATABASE %s;'" % (SCRATCH, SCRATCH))
RES["scratch_create"] = create
applied = []
for f in mig_files:
    sql = f.read_text(encoding="utf-8")
    r = run(gz, "sudo -u postgres psql -X -q -v ON_ERROR_STOP=1 -1 %s <<'MIGSQL'\n%s\nMIGSQL" % (SCRATCH, sql))
    applied.append({"file": f.name, "rc": r["rc"], "err": r["stderr"][:300] if r["rc"] else ""})
RES["scratch_migrations"] = applied
asserts = run(gz, """sudo -u postgres psql -X -A -t -v ON_ERROR_STOP=0 %s <<'SQL'
\\echo ---seed
INSERT INTO devices (id, wg_ip) VALUES ('dev-x', '10.99.0.99');
INSERT INTO nodes (name, host, port, proto, purpose, priority, enabled, health_status, created_at)
 VALUES ('t-ord','203.0.113.10',443,'vless-reality','ordinary',0,true,'unknown',now());
INSERT INTO nodes (name, host, port, proto, purpose, priority, enabled, health_status, created_at)
 VALUES ('t-vid','203.0.113.11',443,'vless-reality','video',0,true,'unknown',now());
\\echo ---bad_purpose_should_fail
INSERT INTO nodes (name, host, port, proto, purpose, priority, enabled, health_status, created_at)
 VALUES ('t-bad','203.0.113.12',443,'vless-reality','bulk',0,true,'unknown',now());
\\echo ---video_primary_should_fail
INSERT INTO node_assignments (device_id, node_id, is_primary, failover_priority, created_at, updated_at)
 VALUES ('dev-x', (SELECT id FROM nodes WHERE name='t-vid'), true, 0, now(), now());
 \\echo ---ordinary_primary_should_ok
INSERT INTO node_assignments (device_id, node_id, is_primary, failover_priority, created_at, updated_at)
 VALUES ('dev-x', (SELECT id FROM nodes WHERE name='t-ord'), true, 0, now(), now());
SQL
""" % SCRATCH)
RES["scratch_asserts"] = asserts
drop = run(gz, "sudo -u postgres psql -X -q -c 'DROP DATABASE IF EXISTS %s;'" % SCRATCH)
RES["scratch_drop"] = drop

r2 = connect("127.0.0.1", 13223, "root", DEV_PW, jump=gz)
RES["hosts"]["R2"] = {"basics": run(r2, """date -Is; uptime
df -h /etc | tail -1
cat /proc/sys/kernel/random/boot_id
command -v python3 || echo python3-missing
command -v jsonfilter || true
date '+%Z %z'; cat /etc/TZ 2>/dev/null; ls -l /etc/localtime 2>/dev/null || echo no-localtime
""")}
RES["hosts"]["R2"]["cron"] = run(r2, "crontab -l 2>/dev/null")
RES["hosts"]["R2"]["trigger_refs"] = run(r2, """grep -rn "ratelimit-ctl\\|proxy-meter" /etc/crontabs/ /etc/rc.local /etc/init.d/ 2>/dev/null | grep -v Binary || echo no-refs
grep -rln "ratelimit" /etc/routermgmt/*.conf /etc/routermgmt/*.sh 2>/dev/null || true
grep -n "ratelimit" /etc/routermgmt/routermgmt-agent.sh 2>/dev/null || true
ps | grep -E "ratelimit|proxy-meter" | grep -v grep || echo no-process
""")
RES["hosts"]["R2"]["meter_state"] = run(r2, """ls -la /etc/routermgmt/ | head -30
sha256sum /etc/routermgmt/proxy-meter-v2.sh /etc/routermgmt/ratelimit-ctl /etc/routermgmt/proxy-meter-rules.conf 2>/dev/null
cat /etc/routermgmt/state/proxy-meter-v2.json 2>/dev/null | head -c 600; echo
cat /etc/routermgmt/state/proxy-meter-daily.json 2>/dev/null; echo
wc -l /etc/routermgmt/proxy-meter-rules.conf 2>/dev/null
""")
RES["hosts"]["R2"]["iptables"] = run(r2, """iptables -S PROXY_METER_OUT 2>/dev/null | head -25
iptables -S PROXY_METER_IN 2>/dev/null | head -25
iptables -S | grep -c "PROXY_METER" 
iptables -L PROXY_METER_OUT -v -x -n 2>/dev/null | head -20
""")
RES["hosts"]["R2"]["groups"] = run(r2, """for g in PROXY US-MEDIA NODE-PICK AUTO AIRPORT; do
  printf '%s=' "$g"
  curl --noproxy '*' -sS -m 5 http://127.0.0.1:9090/proxies/$g 2>/dev/null | jsonfilter -e '@.now' || echo unreachable
done
""")
RES["hosts"]["R2"]["mihomo_files"] = run(r2, """grep -c "server:" /etc/mihomo/config.yaml
ls /etc/mihomo/proxy_providers/ 2>/dev/null
""")
# generator preview to /tmp only（dropbear 无 sftp，经 stdin heredoc 写 /tmp）
gen = (ROOT / "scripts/qos-r2/proxy-meter-endpoints.sh").read_text(encoding="utf-8").replace("\r\n", "\n")


def write_remote(c, path, content, mode=0o755):
    i, o, e = c.exec_command("cat > %s && chmod %o %s" % (path, mode, path), timeout=20)
    i.write(content)
    i.channel.shutdown_write()
    return o.channel.recv_exit_status()


write_remote(r2, "/tmp/proxy-meter-endpoints-preview.sh", gen)
RES["hosts"]["R2"]["generator_preview"] = run(r2, """mkdir -p /tmp/pm3-preview
chmod +x /tmp/proxy-meter-endpoints-preview.sh
EP_RULES_OUT=/tmp/pm3-preview/rules.conf EP_COVER_OUT=/tmp/pm3-preview/coverage.json /tmp/proxy-meter-endpoints-preview.sh
echo ---preview---
head -50 /tmp/pm3-preview/rules.conf
cat /tmp/pm3-preview/coverage.json
wc -l /tmp/pm3-preview/rules.conf
""")
r2.close()
gz.close()
RES["finished"] = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
OUT.write_text(json.dumps(RES, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
import sys as _sys
try:
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
ok_mig = all(m["rc"] == 0 for m in applied)
print("MIGRATIONS_ALL_OK" if ok_mig else "MIGRATION_FAILURE")
print(json.dumps({k: v for k, v in RES.items() if k in ("scratch_create", "scratch_asserts", "scratch_drop")}, ensure_ascii=False, indent=1)[:2500])
print("R2 trigger_refs:", RES["hosts"]["R2"]["trigger_refs"]["stdout"][:800])
print("R2 groups:", RES["hosts"]["R2"]["groups"]["stdout"])
print("generator:", RES["hosts"]["R2"]["generator_preview"]["stdout"][-1500:])
print("saved:", OUT)
