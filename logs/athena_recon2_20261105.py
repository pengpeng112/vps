"""Athena round-2 recon: why router-originated TCP fails while LAN/proxy works. READ-ONLY."""
import os
import sys

import paramiko

PW = os.environ.get("ATH_PW")
if not PW:
    sys.exit("ATH_PW not set")

SCRIPT = r"""
echo "@@routes"
ip route show
ip rule show 2>/dev/null | head -8
echo "@@addrs"
ip -4 addr show | grep -E 'inet|^[0-9]+:' | head -14
echo "@@dns"
cat /tmp/resolv.conf.d/resolv.conf.auto 2>/dev/null || cat /tmp/resolv.conf 2>/dev/null | head -6
echo "@@raw_tcp_tests"
for t in 134.175.151.213:28722 223.5.5.5:53 119.29.29.29:53 14.215.177.38:443 74.82.203.175:28722; do
  (timeout 4 bash -c "echo > /dev/tcp/$t" 2>/dev/null && echo "$t OK") || echo "$t FAIL"
done
echo "@@ping"
ping -c 2 -W 2 223.5.5.5 2>&1 | tail -2
echo "@@mihomo_api"
MP=$(pidof mihomo)
echo "pid=$MP"
curl --noproxy '*' -fsS -m 4 http://127.0.0.1:9090/version 2>/dev/null || echo API9090_FAIL
curl --noproxy '*' -fsS -m 4 http://127.0.0.1:9090/proxies/PROXY 2>/dev/null | jsonfilter -e '@.now' 2>/dev/null || true
curl -4 -sS -m 10 -x http://127.0.0.1:7890 https://api.ipify.org 2>/dev/null; echo " <-exit-via-proxy"
echo "@@mihomo_out_hook"
nft list table inet mihomo 2>/dev/null | grep -E 'hook|jump|redirect|tproxy' | head -12
echo "@@fw4_output"
nft list chain inet fw4 output 2>/dev/null | head -20
echo "@@access_fw_sh"
cat /etc/routermgmt/access-fw.sh 2>/dev/null | head -40
echo "@@tunnel_logs"
ls -lt /etc/routermgmt/logs/ 2>/dev/null | head -8
tail -6 /etc/routermgmt/logs/netprobe.log 2>/dev/null
tail -8 /etc/routermgmt/logs/mgmt-tunnel.log 2>/dev/null || logread 2>/dev/null | grep -i 'mgmt-tunnel' | tail -6
echo "@@ssh_procs_cmdline"
for p in $(pidof ssh); do tr '\0' ' ' < /proc/$p/cmdline 2>/dev/null | cut -c1-110; echo; done
echo "@@done2"
"""

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.2.1", port=22, username="root", password=PW,
          timeout=15, look_for_keys=False, allow_agent=False)
stdin, stdout, stderr = c.exec_command("sh -s", timeout=90)
stdin.channel.sendall(SCRIPT.encode())
stdin.channel.shutdown_write()
print(stdout.read().decode("utf-8", "replace"))
err = stderr.read().decode("utf-8", "replace")
if err.strip():
    print("@@stderr:", err[:300])
c.close()
