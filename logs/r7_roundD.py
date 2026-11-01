"""Codex v2 Round D: final acceptance + evidence snapshot."""
import os
import sys
from pathlib import Path

import paramiko

PW = os.environ.get("DEVPW")

SCRIPT_D = r"""
set -eu
BK=/root/r7fix-20261101
echo "@@final_api"
curl --noproxy '*' -fsS -m 8 http://127.0.0.1:9090/proxies/PROXY > "$BK/proxy.final.json"
curl --noproxy '*' -fsS -m 8 http://127.0.0.1:9090/proxies/AUTO > "$BK/auto.final.json"
echo "PROXY.now=$(jsonfilter -i "$BK/proxy.final.json" -e '@.now')"
echo "AUTO.now=$(jsonfilter -i "$BK/auto.final.json" -e '@.now')"
[ "$(jsonfilter -i "$BK/proxy.final.json" -e '@.now')" = "AUTO" ] || exit 90
[ "$(jsonfilter -i "$BK/auto.final.json" -e '@.now')" = "NODE-PICK" ] || exit 90

echo "@@nodepick_alive"
curl --noproxy '*' -fsS -m 8 http://127.0.0.1:9090/proxies/NODE-PICK > "$BK/nodepick.final.json"
jsonfilter -i "$BK/nodepick.final.json" -e '@.alive'

echo "@@egress"
curl -4 --noproxy '' --proxy http://127.0.0.1:7890 -fsS --connect-timeout 5 --max-time 20 https://api.ipify.org; echo

echo "@@integrity"
md5sum /etc/mihomo/config.yaml /etc/routermgmt/self-heal.sh /etc/routermgmt/tunnel.conf /etc/mihomo/cache.db
[ "$(md5sum /etc/mihomo/config.yaml | awk '{print $1}')" = "53fe16ab876efb9e1bfcc577d4593437" ] || exit 91
[ "$(md5sum /etc/routermgmt/self-heal.sh | awk '{print $1}')" = "329c6081caaa05595c13ff380687187b" ] || exit 91
cp -p /etc/routermgmt/tunnel.conf "$BK/tunnel.conf.after"

echo "@@state"
[ ! -e /tmp/selfheal.pause ] || exit 92
[ -z "$(find /etc/routermgmt/state/txn -name uncommitted -print 2>/dev/null)" ] || exit 92
echo "boot_id=$(cat /proc/sys/kernel/random/boot_id)"
echo "uptime=$(cut -d' ' -f1 /proc/uptime)"
echo "mihomo_pids=$(pidof mihomo | wc -w)"
echo "agent_procs=$(ps w | grep -c '[r]outermgmt-agent')"

echo "@@tunnel_procs"
ps w | grep '[s]sh -i /etc/routermgmt' | wc -l
echo "@@do_conn_now"
netstat -tn 2>/dev/null | grep -c '159.223.36.119' || true

echo ROUND_D_OK
"""

gz = paramiko.SSHClient()
gz.set_missing_host_key_policy(paramiko.AutoAddPolicy())
gz.connect("134.175.151.213", port=28722, username="ubuntu", password=PW,
           timeout=20, look_for_keys=False, allow_agent=False)

# GZ-side: heartbeat freshness for R7 (G1)
_, o_hb, _ = gz.exec_command(
    "sudo -u postgres psql routermgmt -t -A -F'|' -c \"SELECT name,"
    "round(extract(epoch from (now()-last_heartbeat_at))),remote_ssh_alive "
    "FROM devices WHERE name='SN-R7-AX3000';\"",
    timeout=30,
)
print("@@gz-heartbeat")
print(o_hb.read().decode().strip())

chan = gz.get_transport().open_channel("direct-tcpip", ("127.0.0.1", 13229), ("127.0.0.1", 0))
r7 = paramiko.SSHClient()
r7.set_missing_host_key_policy(paramiko.AutoAddPolicy())
r7.connect("127.0.0.1", port=13229, username="root", password=PW, sock=chan,
           timeout=20, look_for_keys=False, allow_agent=False)

stdin, stdout, stderr = r7.exec_command("sh -s", timeout=120)
stdin.channel.sendall(SCRIPT_D.encode())
stdin.channel.shutdown_write()
out = stdout.read().decode("utf-8", "replace")
rc = stdout.channel.recv_exit_status()
err = stderr.read().decode("utf-8", "replace")
print("@@roundD-device")
print(out)
print(f"@@device_exit={rc}")
if err.strip():
    print("@@stderr:", err[:300])
r7.close()

if rc == 0:
    sftp = gz.open_sftp()
    local_dir = Path(r"F:\vps\logs\r7fix-20261101")
    for f in ("proxy.final.json", "auto.final.json", "nodepick.final.json", "tunnel.conf.after"):
        try:
            gz.exec_command(f"SSHPASS='{PW}' sshpass -e scp -O -P 13229 -o StrictHostKeyChecking=yes root@127.0.0.1:/root/r7fix-20261101/{f} /tmp/r7review-20261101/{f}", timeout=30)
            import time
            time.sleep(3)
            sftp.get(f"/tmp/r7review-20261101/{f}", str(local_dir / f))
            print(f"pulled {f}")
        except Exception as exc:
            print(f"pull-skip {f}: {exc}")
    sftp.close()

r7.close() if not r7.get_transport() else None
gz.close()
print("@@ROUND_D_COMPLETE" if rc == 0 else "@@ROUND_D_FAILED")
