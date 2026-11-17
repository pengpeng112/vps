"""Fleet-wide client-path health sweep (read-only). 8 SN-R devices + Athena (nft variant)."""
import os
import sys
import time

import paramiko

PW = os.environ.get("DEVPW")
if not PW:
    sys.exit("DEVPW not set")

SNR_SCRIPT = r"""
echo "@@base"
date '+%H:%M:%S'
echo "up_s=$(cut -d' ' -f1 /proc/uptime)"
echo "mihomo=$(ps w | grep -c '[m]ihomo') agent=$(ps w | grep -c '[r]outermgmt-agent')"
echo "@@chains"
echo "jump=$(iptables -t nat -S PREROUTING 2>/dev/null | grep -c 'MIHOMO$')"
echo "dnschain=$(iptables -t nat -S PREROUTING 2>/dev/null | grep -c 'MIHOMO_DNS$')"
echo "redir=$(iptables -t nat -S 2>/dev/null | grep -c 'to-ports 7892')"
echo "quic=$(iptables -S 2>/dev/null | grep -c 'mihomo-quic-drop')"
echo "@@dns_client_path"
LANIP=$(ip -4 addr show br-lan 2>/dev/null | grep -o 'inet [0-9.]*' | head -1 | cut -d' ' -f2)
echo "lan_ip=$LANIP"
A=$(nslookup www.baidu.com $LANIP 2>/dev/null | grep -m1 -o '198\.18\.[0-9]*\.[0-9]*')
echo "fakeip_baidu=${A:-NONE}"
echo "@@lan_traffic"
logread 2>/dev/null | grep 'mihomo' | grep -cE '192\.168\.[0-9]+\.'
echo "errors=$(logread 2>/dev/null | grep 'mihomo' | grep -icE 'error')"
logread 2>/dev/null | grep 'mihomo' | grep -iE 'error' | tail -1
echo "clients=$(logread 2>/dev/null | grep 'mihomo' | grep -oE '192\.168\.[0-9]+\.[0-9]+' | sort -u | wc -l)"
echo "@@wan"
tail -1 /etc/routermgmt/logs/wan-watch.log 2>/dev/null
echo SWEEP_DONE
"""

ATH_SCRIPT = r"""
echo "@@base"
date '+%H:%M:%S'
echo "up_s=$(cut -d' ' -f1 /proc/uptime)"
echo "mihomo=$(ps w | grep -c '[m]ihomo') agent=$(ps w | grep -c '[r]outermgmt-agent')"
echo "@@chains_nft"
echo "redir=$(nft list table inet mihomo 2>/dev/null | grep -c 'redirect to :7892')"
echo "dnsredir=$(nft list table inet mihomo 2>/dev/null | grep -c 'redirect to :1053')"
echo "@@dns_client_path"
A=$(nslookup www.baidu.com 192.168.2.1 2>/dev/null | grep -m1 -o '198\.18\.[0-9]*\.[0-9]*')
echo "fakeip_baidu=${A:-NONE_or_not_fakeip_mode}"
echo "@@lan_traffic"
logread 2>/dev/null | grep -i 'mihomo' | grep -cE '192\.168\.2\.'
echo "errors=$(logread 2>/dev/null | grep -i 'mihomo' | grep -icE 'error')"
logread 2>/dev/null | grep -i 'mihomo' | grep -iE 'error' | tail -1
echo "clients=$(logread 2>/dev/null | grep -i 'mihomo' | grep -oE '192\.168\.2\.[0-9]+' | sort -u | wc -l)"
echo "@@exit"
curl -4 -sS -m 12 -x http://127.0.0.1:7890 https://api.ipify.org 2>/dev/null; echo " <-exit"
echo "@@mgmt"
curl -sk -m 6 -o /dev/null -w 'mgmt18443=%{http_code}\n' https://127.0.0.1:18443/api/v1/health
echo ATH_SWEEP_DONE
"""

gz = paramiko.SSHClient()
gz.set_missing_host_key_policy(paramiko.AutoAddPolicy())
gz.connect("134.175.151.213", port=28722, username="ubuntu", password=PW,
           timeout=20, look_for_keys=False, allow_agent=False)

DEVICES = [("R1", 13222), ("R2", 13223), ("R3", 13227), ("R4", 13224),
           ("R5", 13225), ("R6", 13226), ("R8", 13230), ("R9", 13231)]

for name, port in DEVICES:
    print(f"@@@@@@ {name}")
    try:
        chan = gz.get_transport().open_channel("direct-tcpip", ("127.0.0.1", port), ("127.0.0.1", 0))
        d = paramiko.SSHClient()
        d.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        d.connect("127.0.0.1", port=port, username="root", password=PW, sock=chan,
                  timeout=15, look_for_keys=False, allow_agent=False)
        stdin, stdout, stderr = d.exec_command("sh -s", timeout=60)
        stdin.channel.sendall(SNR_SCRIPT.encode())
        stdin.channel.shutdown_write()
        print(stdout.read().decode("utf-8", "replace").strip())
        d.close()
    except Exception as exc:
        print("FAIL:", exc)
    sys.stdout.flush()
    time.sleep(2.5)

print("@@@@@@ ATHENA (13228)")
try:
    chan = gz.get_transport().open_channel("direct-tcpip", ("127.0.0.1", 13228), ("127.0.0.1", 0))
    a = paramiko.SSHClient()
    a.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    a.connect("127.0.0.1", port=13228, username="root", password="[REDACTED]", sock=chan,
              timeout=15, look_for_keys=False, allow_agent=False)
    stdin, stdout, stderr = a.exec_command("sh -s", timeout=60)
    stdin.channel.sendall(ATH_SCRIPT.encode())
    stdin.channel.shutdown_write()
    print(stdout.read().decode("utf-8", "replace").strip())
    a.close()
except Exception as exc:
    print("FAIL:", exc)

gz.close()
print("FLEET_SWEEP_DONE")
