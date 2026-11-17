"""Athena @192.168.2.1 read-only recon (standard step 1). NO changes."""
import os
import sys

import paramiko

PW = os.environ.get("ATH_PW")
if not PW:
    sys.exit("ATH_PW not set")

SCRIPT = r"""
echo "@@identity"
cat /tmp/sysinfo/model 2>/dev/null
cat /etc/banner 2>/dev/null | head -3
dropbear -V 2>&1 | head -1
echo "uptime_s=$(cut -d' ' -f1 /proc/uptime)"
echo "@@mgmt_inventory"
ls -la /etc/routermgmt/ 2>/dev/null || echo NO_ROUTERMGMT_DIR
echo "@@agent_conf_keys"
[ -f /etc/routermgmt/agent.conf ] && { awk -F= '{print $1}' /etc/routermgmt/agent.conf; grep '^DEVICE_ID=' /etc/routermgmt/agent.conf; } || echo NO_AGENT_CONF
echo "@@tunnel_conf"
[ -f /etc/routermgmt/tunnel.conf ] && cat /etc/routermgmt/tunnel.conf || echo NO_TUNNEL_CONF
echo "@@init_state"
ls -l /etc/init.d/mgmt-tunnel /etc/init.d/routermgmt 2>/dev/null || echo NO_INITS
ls /etc/rc.d/ 2>/dev/null | grep -E 'mgmt-tunnel|routermgmt'
echo "@@procs"
ps w | grep -c '[m]ihomo'
ps w | grep -c '[r]outermgmt-agent'
ps w | grep '[s]sh ' | wc -l
echo "@@nft_tables"
nft list tables 2>/dev/null || echo NFT_FAIL
echo "@@cron"
crontab -l 2>/dev/null | head -10 || echo NO_CRON
echo "@@network"
uci -q get network.wan.proto; uci -q get network.wan.ipaddr 2>/dev/null
ip -4 addr show pppoe-wan 2>/dev/null | grep inet || ip -4 addr show eth1 2>/dev/null | grep inet || true
ip -4 addr show br-lan 2>/dev/null | grep inet
echo "@@egress_probe"
(timeout 5 bash -c 'echo > /dev/tcp/134.175.151.213/28722' 2>/dev/null && echo GZ_28722_OK) || echo GZ_28722_FAIL
(timeout 5 bash -c 'echo > /dev/tcp/154.36.139.155/28722' 2>/dev/null && echo GIA_28722_OK) || echo GIA_28722_FAIL
(timeout 5 bash -c 'echo > /dev/tcp/74.82.203.175/28722' 2>/dev/null && echo BWG_28722_OK) || echo BWG_28722_FAIL
echo "@@proxy_state"
curl -4 -sS -m 8 -o /dev/null -w 'direct_baidu=%{http_code}\n' http://www.baidu.com 2>/dev/null || echo direct_baidu=FAIL
curl -4 -sS -m 8 -x http://127.0.0.1:7890 -o /dev/null -w 'proxy7890_ip=%{http_code}\n' https://api.ipify.org 2>/dev/null || echo proxy7890=FAIL
echo "@@done"
"""

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.2.1", port=22, username="root", password=PW,
          timeout=15, look_for_keys=False, allow_agent=False)
stdin, stdout, stderr = c.exec_command("sh -s", timeout=60)
stdin.channel.sendall(SCRIPT.encode())
stdin.channel.shutdown_write()
print(stdout.read().decode("utf-8", "replace"))
err = stderr.read().decode("utf-8", "replace")
if err.strip():
    print("@@stderr:", err[:300])
c.close()
