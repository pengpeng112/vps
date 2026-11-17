"""R5 recurrence diagnosis (11-10): DingTalk-ok/web-dead. Did the new guard fire? READ-ONLY."""
import os
import sys

import paramiko

PW = os.environ.get("DEVPW")
if not PW:
    sys.exit("DEVPW not set")

SCRIPT = r"""
echo "@@boot_state"
date '+%m-%d %H:%M:%S'
echo "uptime_s=$(cut -d' ' -f1 /proc/uptime)"
echo "mihomo=$(ps w | grep -c '[m]ihomo') agent=$(ps w | grep -c '[r]outermgmt-agent') dnsmasq=$(ps w | grep -c '[d]nsmasq')"
echo "@@chains"
echo "jump=$(iptables -t nat -S PREROUTING 2>/dev/null | grep -c 'MIHOMO$')"
echo "dnschain=$(iptables -t nat -S PREROUTING 2>/dev/null | grep -c 'MIHOMO_DNS$')"
echo "redir=$(iptables -t nat -S 2>/dev/null | grep -c 'to-ports 7892')"
echo "quic=$(iptables -S 2>/dev/null | grep -c 'mihomo-quic-drop')"
echo "@@guard_activity"
logread 2>/dev/null | grep 'mihomo-guard' | tail -6
echo "guard_fires=$(logread 2>/dev/null | grep -c 'mihomo-guard')"
echo "@@mihomo_health"
curl --noproxy '*' -fsS -m 4 http://127.0.0.1:9090/version 2>/dev/null || echo API9090_FAIL
logread 2>/dev/null | grep -iE 'mihomo.*(FATA|fatal|panic|parse)' | tail -4
echo "@@dns_tests"
LANIP=$(ip -4 addr show br-lan 2>/dev/null | grep -o 'inet [0-9.]*' | head -1 | cut -d' ' -f2)
echo "lan_ip=$LANIP"
nslookup www.baidu.com 127.0.0.1 2>&1 | tail -3
nslookup www.baidu.com $LANIP 2>&1 | tail -3
echo "@@wan"
tail -2 /etc/routermgmt/logs/wan-watch.log 2>/dev/null
echo "@@recent_client_traffic"
logread 2>/dev/null | grep 'mihomo' | grep -cE '192\.168\.132\.'
logread 2>/dev/null | grep 'mihomo' | grep -iE 'error' | tail -2
echo "@@boot_head"
logread 2>/dev/null | head -5
echo R5_RECURRENCE_DONE
"""

gz = paramiko.SSHClient()
gz.set_missing_host_key_policy(paramiko.AutoAddPolicy())
gz.connect("134.175.151.213", port=28722, username="ubuntu", password=PW,
           timeout=20, look_for_keys=False, allow_agent=False)

_, o_hb, _ = gz.exec_command(
    "sudo -u postgres psql routermgmt -t -A -F'|' -c \"SELECT name,"
    "round(extract(epoch from (now()-last_heartbeat_at))) FROM devices "
    "WHERE name='SN-R5-AX3000';\"", timeout=20)
print("@@gz_heartbeat_age:", o_hb.read().decode().strip() or "(empty)")

chan = gz.get_transport().open_channel("direct-tcpip", ("127.0.0.1", 13225), ("127.0.0.1", 0))
r5 = paramiko.SSHClient()
r5.set_missing_host_key_policy(paramiko.AutoAddPolicy())
r5.connect("127.0.0.1", port=13225, username="root", password=PW, sock=chan,
           timeout=20, look_for_keys=False, allow_agent=False)
stdin, stdout, stderr = r5.exec_command("sh -s", timeout=120)
stdin.channel.sendall(SCRIPT.encode())
stdin.channel.shutdown_write()
print(stdout.read().decode("utf-8", "replace"))
err = stderr.read().decode("utf-8", "replace")
if err.strip():
    print("@@stderr:", err[:200])
r5.close()
gz.close()
