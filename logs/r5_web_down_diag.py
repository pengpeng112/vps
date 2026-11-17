"""R5 web-pages-down diagnosis: WeChat-ok/web-fail pattern. STRICTLY READ-ONLY."""
import os
import sys

import paramiko

PW = os.environ.get("DEVPW")
if not PW:
    sys.exit("DEVPW not set")

SCRIPT = r"""
echo "@@procs_uptime"
date '+%m-%d %H:%M:%S'
echo "uptime_s=$(cut -d' ' -f1 /proc/uptime)"
echo "mihomo=$(ps w | grep -c '[m]ihomo')"
echo "agent=$(ps w | grep -c '[r]outermgmt-agent')"
echo "dnsmasq=$(ps w | grep -c '[d]nsmasq')"
netstat -tln 2>/dev/null | grep -E ':9090|:18443' | head -3
echo "@@mihomo_validate"
mihomo -t -d /etc/mihomo -f /etc/mihomo/config.yaml 2>&1 | tail -3
md5sum /etc/mihomo/config.yaml
ls -l /etc/mihomo/config.yaml
echo "@@mihomo_log_tail"
logread 2>/dev/null | grep -iE 'mihomo.*(FATA|error|parse|fatal|panic)' | tail -6
logread 2>/dev/null | grep -c 'mihomo' 
echo "@@crash_loop_evidence"
logread 2>/dev/null | grep 'mihomo' | head -3
echo "@@dns_state"
netstat -su 2>/dev/null | grep -E 'RcvbufErrors|SndbufErrors|InErrors' | head -4
echo "--- listeners :53 ---"
netstat -tuln 2>/dev/null | grep ':53 ' | head -4
echo "--- udp53 conntrack pile ---"
[ -f /proc/net/nf_conntrack ] && grep -c 'dport=53' /proc/net/nf_conntrack 2>/dev/null || echo n/a
echo "--- resolve via 127.0.0.1 ---"
nslookup www.baidu.com 127.0.0.1 2>&1 | tail -3
nslookup www.douyin.com 127.0.0.1 2>&1 | tail -3
echo "@@dnsmasq_cfg"
uci -q get dhcp.@dnsmasq[0].server | head -2
uci -q get dhcp.@dnsmasq[0].noresolv
netstat -tln 2>/dev/null | grep ':1053' | head -2
echo "@@wan_watch"
tail -3 /etc/routermgmt/logs/wan-watch.log 2>/dev/null
echo "@@selfheal_log"
logread 2>/dev/null | grep -i 'self-heal' | tail -4
echo "@@lan_traffic_now"
logread 2>/dev/null | grep 'mihomo' | tail -4
echo R5_DIAG_DONE
"""

gz = paramiko.SSHClient()
gz.set_missing_host_key_policy(paramiko.AutoAddPolicy())
gz.connect("134.175.151.213", port=28722, username="ubuntu", password=PW,
           timeout=20, look_for_keys=False, allow_agent=False)

_, o_hb, _ = gz.exec_command(
    "sudo -u postgres psql routermgmt -t -A -F'|' -c \"SELECT name,"
    "round(extract(epoch from (now()-last_heartbeat_at))),current_config_version "
    "FROM devices WHERE name='SN-R5-AX3000';\"", timeout=20)
print("@@gz_db:", o_hb.read().decode().strip())

chan = gz.get_transport().open_channel("direct-tcpip", ("127.0.0.1", 13225), ("127.0.0.1", 0))
r5 = paramiko.SSHClient()
r5.set_missing_host_key_policy(paramiko.AutoAddPolicy())
r5.connect("127.0.0.1", port=13225, username="root", password=PW, sock=chan,
           timeout=20, look_for_keys=False, allow_agent=False)
stdin, stdout, stderr = r5.exec_command("sh -s", timeout=150)
stdin.channel.sendall(SCRIPT.encode())
stdin.channel.shutdown_write()
print(stdout.read().decode("utf-8", "replace"))
err = stderr.read().decode("utf-8", "replace")
if err.strip():
    print("@@stderr:", err[:200])
r5.close()
gz.close()
