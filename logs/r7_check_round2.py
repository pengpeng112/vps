"""R7 round-2 evidence: agent procs, VPS4, NODE-PICK, GZ DB heartbeat (with stderr)."""
import os
import sys
import time
from pathlib import Path

import paramiko

PW = os.environ.get("DEVPW")
if not PW:
    sys.exit("DEVPW not set")

gz = paramiko.SSHClient()
gz.set_missing_host_key_policy(paramiko.AutoAddPolicy())
gz.connect("134.175.151.213", port=28722, username="ubuntu", password=PW,
           timeout=20, look_for_keys=False, allow_agent=False)


def run_gz(cmd, t=30):
    _, o, e = gz.exec_command(cmd, timeout=t)
    return o.read().decode("utf-8", "replace") + e.read().decode("utf-8", "replace")


print("@@gz-epoch-now")
print(int(time.time()), "local-pc-epoch")
print(run_gz("date -u '+%s %H:%M:%S'").strip())

print("@@gz-db-r7-retry")
print(run_gz(
    "sudo -u postgres psql routermgmt -t -A -F'|' -c \"SELECT name,"
    "remote_ssh_port,remote_ssh_alive,"
    "to_char(last_heartbeat_at,'MM-DD HH24:MI:SS'),"
    "round(extract(epoch from (now()-last_heartbeat_at))) AS age_s "
    "FROM devices WHERE name IN ('R7','R8') ORDER BY name;\" 2>&1"
).strip())

chan = gz.get_transport().open_channel("direct-tcpip", ("127.0.0.1", 13229), ("127.0.0.1", 0))
r7 = paramiko.SSHClient()
r7.set_missing_host_key_policy(paramiko.AutoAddPolicy())
r7.connect("127.0.0.1", port=13229, username="root", password=PW, sock=chan,
           timeout=20, look_for_keys=False, allow_agent=False)

SCRIPT = r"""
echo "@@agent_procs"
ps w | grep '[r]outermgmt-agent' | wc -l
ps w | grep -i 'agent' | grep -v grep | head -4
echo "@@vps4_lines"
grep -n 'VPS4' /etc/init.d/mgmt-tunnel 2>/dev/null | head -5
echo "@@endpoints_conf"
grep -c 'VPS[0-9]*_HOST=' /etc/routermgmt/tunnel.conf 2>/dev/null
grep -o 'VPS[0-9]*_HOST=[0-9.]*' /etc/routermgmt/tunnel.conf 2>/dev/null
echo "@@nodepick"
curl -s -m 5 http://127.0.0.1:9090/proxies/NODE-PICK 2>/dev/null | head -c 420
echo
echo "@@airport_head"
curl -s -m 5 http://127.0.0.1:9090/proxies/AIRPORT 2>/dev/null | head -c 240
echo
echo "@@cfg_proxy_names"
grep -E '^  - name:|^    name:' /etc/mihomo/config.yaml 2>/dev/null | head -14
echo "@@crontab"
cat /etc/crontabs/root 2>/dev/null
echo "@@done2"
"""

stdin, stdout, stderr = r7.exec_command("sh -s", timeout=90)
stdin.channel.sendall(SCRIPT.encode())
stdin.channel.shutdown_write()
out = stdout.read().decode("utf-8", "replace")
err = stderr.read().decode("utf-8", "replace")
print("@@r7-round2")
print(out)
if err.strip():
    print("@@r7-round2-stderr")
    print(err[:300])

r7.close()
gz.close()
print("@@saved2")
