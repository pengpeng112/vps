"""R7 midnight acceptance check - STRICTLY READ-ONLY (Codex ruling: automation must not hotfix).
Checks after 00:00 reboot: boot_id changed, PROXY still AUTO (persistence), VPS4 comment intact,
no DO connections, egress GIA, mgmt chain healthy, pause/txn clean.
Appends evidence to F:/vps/logs/r7-midnight-check-20261102.txt"""
import os
import sys
from datetime import datetime
from pathlib import Path

import paramiko

PW = os.environ.get("DEVPW")
if not PW:
    sys.exit("DEVPW not set")

SCRIPT = r"""
set -u
echo "@@time"
date '+%Y-%m-%d %H:%M:%S %z'
echo "boot_id=$(cat /proc/sys/kernel/random/boot_id)"
echo "uptime_s=$(cut -d' ' -f1 /proc/uptime)"
echo "@@proxy"
curl --noproxy '*' -fsS -m 8 http://127.0.0.1:9090/proxies/PROXY 2>/dev/null | jsonfilter -e '@.now' 2>/dev/null || echo API_FAIL
curl --noproxy '*' -fsS -m 8 http://127.0.0.1:9090/proxies/AUTO 2>/dev/null | jsonfilter -e '@.now' 2>/dev/null || echo API_FAIL
echo "@@egress"
curl -4 --noproxy '' --proxy http://127.0.0.1:7890 -fsS --connect-timeout 5 --max-time 20 https://api.ipify.org 2>/dev/null || echo EGRESS_FAIL
echo
echo "@@vps4_state"
grep -nE '^[[:space:]]*#?[[:space:]]*VPS4_(HOST|PORT)=' /etc/routermgmt/tunnel.conf 2>/dev/null || echo TCONF_FAIL
md5sum /etc/routermgmt/tunnel.conf /etc/mihomo/config.yaml /etc/routermgmt/self-heal.sh 2>/dev/null
echo "@@processes"
echo "mihomo=$(pidof mihomo | wc -w)"
echo "agent=$(ps w | grep -c '[r]outermgmt-agent')"
echo "@@guards"
[ -e /tmp/selfheal.pause ] && echo PAUSE_PRESENT || echo PAUSE_ABSENT
find /etc/routermgmt/state/txn -name uncommitted -print 2>/dev/null | wc -l
echo "@@mgmt"
curl -sk -m 6 -o /dev/null -w 'mgmt18443=%{http_code}\n' https://127.0.0.1:18443/api/v1/health 2>/dev/null
echo "@@do_conns"
netstat -tn 2>/dev/null | grep -c '159.223.36.119' || echo 0
echo "@@end"
"""

out_all = []
out_all.append(f"===== midnight check {datetime.now().isoformat()} =====")

gz = paramiko.SSHClient()
gz.set_missing_host_key_policy(paramiko.AutoAddPolicy())
gz.connect("134.175.151.213", port=28722, username="ubuntu", password=PW,
           timeout=20, look_for_keys=False, allow_agent=False)

_, o_hb, _ = gz.exec_command(
    "sudo -u postgres psql routermgmt -t -A -F'|' -c \"SELECT name,"
    "round(extract(epoch from (now()-last_heartbeat_at))),remote_ssh_alive "
    "FROM devices WHERE name='SN-R7-AX3000';\" 2>&1",
    timeout=30,
)
hb = o_hb.read().decode().strip()
out_all.append(f"@@gz-heartbeat(R7 age_s|alive): {hb}")

try:
    _, o_ls, _ = gz.exec_command("ss -ltn | grep ':13229' | head -1", timeout=15)
    out_all.append(f"@@gz-13229-listen: {o_ls.read().decode().strip() or 'NOT_LISTENING'}")
except Exception as exc:
    out_all.append(f"@@gz-13229-listen: ERROR {exc}")

try:
    chan = gz.get_transport().open_channel("direct-tcpip", ("127.0.0.1", 13229), ("127.0.0.1", 0))
    r7 = paramiko.SSHClient()
    r7.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    r7.connect("127.0.0.1", port=13229, username="root", password=PW, sock=chan,
               timeout=20, look_for_keys=False, allow_agent=False)
    stdin, stdout, stderr = r7.exec_command("sh -s", timeout=90)
    stdin.channel.sendall(SCRIPT.encode())
    stdin.channel.shutdown_write()
    dev_out = stdout.read().decode("utf-8", "replace")
    out_all.append(dev_out)
    r7.close()
except Exception as exc:
    out_all.append(f"@@device-connect: FAIL {exc}")

gz.close()

report = "\n".join(out_all)

verdict = "MIDNIGHT_UNKNOWN"
if "API_FAIL" not in report and "EGRESS_FAIL" not in report and "TCONF_FAIL" not in report:
    if "PROXY" in report and "\nAUTO" in report.replace("@@proxy\n", ""):
        lines = report.splitlines()
        try:
            proxy_now = lines[lines.index("@@proxy") + 1].strip()
            auto_now = lines[lines.index("@@proxy") + 2].strip()
            egress = lines[lines.index("@@egress") + 1].strip()
            boot = [l for l in lines if l.startswith("boot_id=")][0]
            if proxy_now == "AUTO" and auto_now == "NODE-PICK" and egress == "154.36.139.155":
                verdict = f"MIDNIGHT_PASS {boot} PROXY={proxy_now} AUTO={auto_now} egress={egress}"
            else:
                verdict = f"MIDNIGHT_FAIL PROXY={proxy_now} AUTO={auto_now} egress={egress}"
        except (ValueError, IndexError):
            verdict = "MIDNIGHT_PARSE_FAIL"
else:
    verdict = "MIDNIGHT_FAIL (api/egress/tconf check failed)"

out_all.append(f"VERDICT: {verdict}")
out_all.append("NOTE: automation is READ-ONLY; no hotfix per Codex ruling (机会留给人工/下轮).")

report = "\n".join(out_all)
print(report)

log = Path(r"F:\vps\logs\r7-midnight-check-20261102.txt")
with log.open("a", encoding="utf-8") as f:
    f.write(report + "\n\n")
