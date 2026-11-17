"""R4 Fangzi: foreign-down diagnosis. STRICTLY READ-ONLY."""
import os
import sys

import paramiko

PW = os.environ.get("DEVPW")
if not PW:
    sys.exit("DEVPW not set")

SCRIPT = r"""
echo "@@base"
date '+%m-%d %H:%M:%S'
echo "up_s=$(cut -d' ' -f1 /proc/uptime)"
echo "mihomo=$(ps w | grep -c '[m]ihomo') agent=$(ps w | grep -c '[r]outermgmt-agent')"
echo "@@chains"
echo "jump=$(iptables -t nat -S PREROUTING 2>/dev/null | grep -c 'MIHOMO$')"
echo "dnschain=$(iptables -t nat -S PREROUTING 2>/dev/null | grep -c 'MIHOMO_DNS$')"
echo "redir=$(iptables -t nat -S 2>/dev/null | grep -c 'to-ports 7892')"
echo "@@groups"
for g in PROXY AUTO NODE-PICK; do
  j=$(curl --noproxy '*' -fsS -m 5 "http://127.0.0.1:9090/proxies/$g" 2>/dev/null) || { echo "$g API_FAIL"; continue; }
  echo "$g now=$(jsonfilter -s "$j" -e '@.now' 2>/dev/null) alive=$(jsonfilter -s "$j" -e '@.alive' 2>/dev/null)"
done
echo "@@nodes_active_probe"
for n in US666-CN2GIA-01 BWG-HY2-JPTY-01 GIA-HY2-US-01 BWG-VLESS-JPTY-01 CF-CDN-GIA-01 CF2-CDN-GIA-01; do
  m=$(curl --noproxy '*' -fsS -m 5 "http://127.0.0.1:9090/proxies/$n" 2>/dev/null)
  a=$(jsonfilter -s "$m" -e '@.alive' 2>/dev/null)
  d=$(echo "$m" | grep -o '"delay":[0-9]*' | tail -1 | cut -d: -f2)
  r=$(curl --noproxy '*' -fsS -m 10 -G "http://127.0.0.1:9090/proxies/$n/delay" --data-urlencode 'url=https://www.gstatic.com/generate_204' --data-urlencode 'timeout=6000' 2>/dev/null | grep -o '"delay":[0-9]*' | cut -d: -f2)
  echo "$n alive=$a cached=${d:-NA} active_probe=${r:-FAIL}"
done
echo "airport=$(curl --noproxy '*' -fsS -m 5 http://127.0.0.1:9090/proxies/AIRPORT 2>/dev/null | jsonfilter -e '@.alive')"
echo "@@exit_tests"
for i in 1 2 3; do
  out=$(curl -4 -sS -m 12 -x http://127.0.0.1:7890 https://api.ipify.org 2>/dev/null)
  echo "try$i=${out:-FAIL}"
done
echo "@@domestic_check"
curl -4 -sS --noproxy '*' -m 8 -o /dev/null -w 'baidu_direct=%{http_code} t=%{time_total}s\n' https://www.baidu.com/ 2>&1 | tail -1
echo "@@wan"
tail -2 /etc/routermgmt/logs/wan-watch.log 2>/dev/null
echo "@@errors_recent"
logread 2>/dev/null | grep 'mihomo' | grep -iE 'error' | tail -6
echo "@@error_count_today"
logread 2>/dev/null | grep 'mihomo' | grep 'Nov 16' | grep -icE 'error'
echo "@@guard"
logread 2>/dev/null | grep -c 'mihomo-guard'
echo R4_DIAG_DONE
"""

gz = paramiko.SSHClient()
gz.set_missing_host_key_policy(paramiko.AutoAddPolicy())
gz.connect("134.175.151.213", port=28722, username="ubuntu", password=PW,
           timeout=20, look_for_keys=False, allow_agent=False)

_, o_hb, _ = gz.exec_command(
    "sudo -u postgres psql routermgmt -t -A -F'|' -c \"SELECT name,"
    "round(extract(epoch from (now()-last_heartbeat_at))),current_exit_ip "
    "FROM devices WHERE name='SN-R4-AX3000 坊子网警';\"", timeout=20)
print("@@gz_db:", o_hb.read().decode().strip() or "(empty)")

chan = gz.get_transport().open_channel("direct-tcpip", ("127.0.0.1", 13224), ("127.0.0.1", 0))
r4 = paramiko.SSHClient()
r4.set_missing_host_key_policy(paramiko.AutoAddPolicy())
r4.connect("127.0.0.1", port=13224, username="root", password=PW, sock=chan,
           timeout=20, look_for_keys=False, allow_agent=False)
stdin, stdout, stderr = r4.exec_command("sh -s", timeout=150)
stdin.channel.sendall(SCRIPT.encode())
stdin.channel.shutdown_write()
print(stdout.read().decode("utf-8", "replace"))
err = stderr.read().decode("utf-8", "replace")
if err.strip():
    print("@@stderr:", err[:200])
r4.close()
gz.close()
