"""R5 fix: re-run mihomo-ipt.sh after content safety check (no service restarts inside).
Then verify client path end-to-end. Only iptables rule installation - no mihomo/tunnel touched."""
import os
import sys

import paramiko

PW = os.environ.get("DEVPW")
if not PW:
    sys.exit("DEVPW not set")

SCRIPT = r"""
echo "@@script_safety_check"
grep -nE 'restart|reload|init\.d|reboot|kill' /etc/mihomo-ipt.sh && echo HAS_ACTION_KEYWORDS || echo PURE_RULES_ONLY
echo "@@script_head"
head -20 /etc/mihomo-ipt.sh
echo "@@run_decision"
if grep -qE '/etc/init\.d/|restart|reload' /etc/mihomo-ipt.sh; then
    echo ABORT_SCRIPT_HAS_SERVICE_ACTIONS
else
    sh /etc/mihomo-ipt.sh 2>&1 | tail -4
    echo RULES_INSTALLED
fi
echo "@@verify_iptables"
iptables -t nat -S 2>/dev/null | grep -E '7892' | head -6
echo "@@verify_client_path"
FA=$(nslookup www.baidu.com 192.168.132.1 2>/dev/null | grep -m1 'Address [0-9]*:' | grep -o '[0-9.]*$')
echo "fake_ip=$FA"
curl -4 -sS --noproxy '*' -m 8 -o /dev/null -w "via_fakeip_http=%{http_code} t=%{time_total}s\n" "http://$FA/" 2>&1 | tail -1
curl -4 -sS --noproxy '*' -m 10 -o /dev/null -w 'douyin_direct=%{http_code} t=%{time_total}s\n' https://www.douyin.com/ 2>&1 | tail -1
echo "@@mihomo_sees_lan"
sleep 3
logread 2>/dev/null | grep 'mihomo' | grep -E '192\.168\.132|192\.168\.0\.' | tail -3
echo R5_FIX_DONE
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
stdin, stdout, stderr = r5.exec_command("sh -s", timeout=120)
stdin.channel.sendall(SCRIPT.encode())
stdin.channel.shutdown_write()
print(stdout.read().decode("utf-8", "replace"))
err = stderr.read().decode("utf-8", "replace")
if err.strip():
    print("@@stderr:", err[:200])
r5.close()
gz.close()
