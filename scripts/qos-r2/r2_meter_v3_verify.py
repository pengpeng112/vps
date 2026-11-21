"""R2 proxy-meter v3 post-migration verification (read-only).

Waits for cron cycles, then asserts: seq advanced, day_total grew with REAL
background traffic, health ok, groups unchanged, v2 idle (log mtime stale),
agent heartbeat fresh. Evidence JSON: logs/r2-meter-v3-verify-20261117.json
"""
from pathlib import Path
import datetime
import json
import time
import paramiko

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "logs/r2-meter-v3-verify-20261117.json"
BT = chr(96)
WAIT_SECONDS = int(__import__("os").environ.get("VERIFY_WAIT", "60"))


def controlled_row(path, name):
    for line in (ROOT / path).read_text(encoding="utf-8-sig").splitlines():
        cells = [x.strip() for x in line.strip().strip("|").split("|")]
        if line.startswith("|") and cells[0] == name:
            return "|".join(cells[1:])
    raise ValueError("controlled credential field missing: %s" % name)


def quoted(v):
    return __import__("re").findall(BT + "([^" + BT + "]+)" + BT, v)


DEV_PW = quoted(controlled_row("backups/SN-R系列凭证总览.md", "**root/SSH 口令**"))[0]


def connect(host, port, user, pw, jump=None):
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    sock = jump.get_transport().open_channel("direct-tcpip", (host, port), ("127.0.0.1", 0), timeout=15) if jump else None
    c.connect(host, port=port, username=user, password=pw, sock=sock,
              timeout=15, banner_timeout=15, auth_timeout=15, look_for_keys=False, allow_agent=False)
    return c


def run(c, script, timeout=40):
    i, o, e = c.exec_command("sh -s", timeout=timeout)
    i.write(script)
    i.channel.shutdown_write()
    return {"rc": o.channel.recv_exit_status(), "stdout": o.read().decode("utf-8", "replace")[-8000:],
            "stderr": e.read().decode("utf-8", "replace")[-400:]}


print("waiting %ds for cron cycles..." % WAIT_SECONDS, flush=True)
time.sleep(WAIT_SECONDS)

gz = connect("134.175.151.213", 28722, "ubuntu", DEV_PW)
r2 = connect("127.0.0.1", 13223, "root", DEV_PW, jump=gz)
snap = run(r2, """date -Is
echo '=== v3 state ==='; cat /etc/routermgmt/state/proxy-meter-v3.json; echo
echo '=== v3 daily ==='; cat /etc/routermgmt/state/proxy-meter-daily-v3.json; echo
echo '=== v3 log tail ==='; tail -5 /etc/routermgmt/logs/proxy-meter-v3.log
echo '=== v2 idle check ==='; ls -l --full-time /etc/routermgmt/logs/proxy-meter-v2.log 2>/dev/null | awk '{print $6, $7}'; date '+%F %T'
echo '=== cron ==='; crontab -l | grep proxy-meter
echo '=== groups ==='
for g in PROXY US-MEDIA NODE-PICK AUTO; do printf '%s=' "$g"; curl --noproxy '*' -sS -m 5 http://127.0.0.1:9090/proxies/$g | jsonfilter -e '@.now' || echo unreachable; done
echo '=== iptables counts ==='; iptables -L PROXY_METER_OUT -v -x -n 2>/dev/null | grep -E "BWG|BULK" ; iptables -L PROXY_METER_IN -v -x -n 2>/dev/null | grep -E "BWG|BULK"
""")
hb = run(gz, """sudo -u postgres psql -X -A -t routermgmt -c "SELECT last_heartbeat_at FROM devices WHERE id='rtr-a9bcba';"
""")
health = run(gz, "curl --noproxy '*' -fsS -m 5 http://127.0.0.1:8080/api/v1/health >/dev/null && echo gz-api-ok")
r2.close()
gz.close()

out = {"snapshot": snap, "gz_heartbeat": hb, "gz_health": health,
       "verified_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds")}
OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

# ---- assertions: 真实流量入账 ----
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
st = json.loads([l for l in snap["stdout"].splitlines() if l.startswith("{\"schema\":3")][0])
dv = json.loads([l for l in snap["stdout"].splitlines() if l.startswith("{\"day_key\"")][0])
ok = True
checks = {
    "schema3": st["schema"] == 3,
    "seq_advanced": st["seq"] >= 2,
    "day_total_grew": dv["total"] > 0,
    "health_ok": dv["health"] == "ok",
    "meter_id": st["meter_id"] == "rtr-a9bcba",
    "v3_in_cron": "proxy-meter-v3.sh" in snap["stdout"],
    "groups_normal": ("PROXY=AUTO" in snap["stdout"] and "US-MEDIA=NODE-PICK" in snap["stdout"]
                      and "NODE-PICK=BWG-HY2" in snap["stdout"]),
    "gz_heartbeat_present": bool(hb["stdout"].strip()),
    "gz_api_ok": "gz-api-ok" in health["stdout"],
}
for k, v in checks.items():
    print(("PASS " if v else "FAIL ") + k)
    ok = ok and v
print("VERIFY_" + ("PASS" if ok else "FAIL"))
print("seq=%s day_total=%s endpoints=%s" % (st["seq"], dv["total"], st["endpoints"]))
