"""R5 ChangLe: Douyin slowness diagnosis. READ-ONLY.
Checks: proxy state, DNS resolution path, douyin reachability (direct vs proxy),
rule-match evidence from mihomo logs, WAN link quality, clock."""
import os
import sys

import paramiko

PW = os.environ.get("DEVPW")
if not PW:
    sys.exit("DEVPW not set")

SCRIPT = r"""
echo "@@state"
date '+%H:%M:%S %z'
curl --noproxy '*' -fsS -m 5 http://127.0.0.1:9090/version >/dev/null 2>&1 || { echo API_FAIL; }
echo "PROXY.now=$(curl --noproxy '*' -fsS -m 5 http://127.0.0.1:9090/proxies/PROXY 2>/dev/null | jsonfilter -e '@.now')"
echo "AUTO.now=$(curl --noproxy '*' -fsS -m 5 http://127.0.0.1:9090/proxies/AUTO 2>/dev/null | jsonfilter -e '@.now')"
echo "NP.now=$(curl --noproxy '*' -fsS -m 5 http://127.0.0.1:9090/proxies/NODE-PICK 2>/dev/null | jsonfilter -e '@.now')"
j=$(curl --noproxy '*' -fsS -m 5 http://127.0.0.1:9090/proxies/NODE-PICK 2>/dev/null)
for n in $(jsonfilter -s "$j" -e '@.all[*]' 2>/dev/null); do
  m=$(curl --noproxy '*' -fsS -m 5 "http://127.0.0.1:9090/proxies/$n" 2>/dev/null) || continue
  echo "$n alive=$(jsonfilter -s "$m" -e '@.alive') delay=$(echo "$m" | grep -o '"delay":[0-9]*' | tail -1 | cut -d: -f2)"
done

echo "@@dns_path"
cat /etc/resolv.conf 2>/dev/null | grep -v '^#' | head -4
netstat -tln 2>/dev/null | grep -E ':53|:1053' | head -6
echo "--- resolve douyin via device DNS ---"
nslookup douyin.com 2>&1 | tail -4
nslookup api.amemv.com 2>&1 | tail -4

echo "@@rules_for_douyin"
grep -nE 'douyin|amemv|aweme|bytedance|pstatp|ixigua|cn$' /etc/mihomo/config.yaml 2>/dev/null | grep -iE 'douyin|amemv|aweme|bytedance|pstatp' | head -6
grep -cE 'GEOSITE|GEOIP' /etc/mihomo/config.yaml 2>/dev/null
echo "--- last douyin match from mihomo log ---"
logread 2>/dev/null | grep -iE 'douyin|amemv|aweme' | tail -8 || echo NO_DOUYIN_LOG

echo "@@reachability"
echo "--- direct (bypass proxy, --noproxy) ---"
curl -4 -sS --noproxy '*' -o /dev/null -w 'douyin_direct=%{http_code} t=%{time_total}s\n' -m 10 https://www.douyin.com/ 2>&1 | tail -1
curl -4 -sS --noproxy '*' -o /dev/null -w 'baidu_direct=%{http_code} t=%{time_total}s\n' -m 10 https://www.baidu.com/ 2>&1 | tail -1
echo "--- via proxy 7890 ---"
curl -4 -sS -x http://127.0.0.1:7890 -o /dev/null -w 'douyin_proxy=%{http_code} t=%{time_total}s\n' -m 15 https://www.douyin.com/ 2>&1 | tail -1
curl -4 -sS -x http://127.0.0.1:7890 -o /dev/null -w 'baidu_proxy=%{http_code} t=%{time_total}s\n' -m 15 https://www.baidu.com/ 2>&1 | tail -1

echo "@@wan_quality"
tail -4 /etc/routermgmt/logs/netprobe.log 2>/dev/null
tail -4 /etc/routermgmt/logs/wan-watch.log 2>/dev/null || true
echo "@@egress"
curl -4 -sS -m 12 -x http://127.0.0.1:7890 https://api.ipify.org 2>/dev/null; echo " <-egress"
echo "@@clock"
date -u '+%s %H:%M:%S'
echo R5_DONE
"""

gz = paramiko.SSHClient()
gz.set_missing_host_key_policy(paramiko.AutoAddPolicy())
gz.connect("134.175.151.213", port=28722, username="ubuntu", password=PW,
           timeout=20, look_for_keys=False, allow_agent=False)

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
    print("@@stderr:", err[:300])
r5.close()
gz.close()
