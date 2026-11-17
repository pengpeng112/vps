"""R5 round-2: IPv6 blackhole check, QUIC drop rule, DNS flakiness quantification. READ-ONLY."""
import os
import sys

import paramiko

PW = os.environ.get("DEVPW")

SCRIPT = r"""
echo "@@v6_state"
ip -6 route show | head -5
echo "--- default v6 route count: $(ip -6 route show default | wc -l)"
ifstatus wan6 2>/dev/null | jsonfilter -e '@.up' 2>/dev/null || echo no-wan6
ping -6 -c 2 -W 2 2400:3200::1 2>&1 | tail -2
echo "@@dnsmasq_v6"
uci -q get dhcp.lan.ra; uci -q get dhcp.lan.dhcpv6
netstat -tln | grep -c ':::53\|:*:53'
echo "@@quic_drop_rule"
nft list table inet mihomo 2>/dev/null | grep -E 'udp dport 443|drop' | head -4
echo "@@dns_repeat"
ok=0; fail=0
i=1
while [ $i -le 8 ]; do
  if nslookup douyin.com 127.0.0.1 2>/dev/null | grep -q 'Address'; then ok=$((ok+1)); else fail=$((fail+1)); fi
  i=$((i+1))
done
echo "douyin.com via dnsmasq: ok=$ok fail=$fail"
ok=0; fail=0; i=1
while [ $i -le 8 ]; do
  if nslookup api.amemv.com 127.0.0.1 2>/dev/null | grep -q 'Address'; then ok=$((ok+1)); else fail=$((fail+1)); fi
  i=$((i+1))
done
echo "api.amemv.com via dnsmasq: ok=$ok fail=$fail"
echo "@@upstream_direct"
for d in 119.29.29.29 180.76.76.76 223.5.5.5; do
  r=$(nslookup douyin.com $d 2>&1 | grep -c 'Address')
  echo "upstream $d answers=$r"
done
echo "@@v6_first_look"
nslookup api.amemv.com 127.0.0.1 2>/dev/null | tail -6
echo "@@wifi_clients"
iw dev 2>/dev/null | grep -c Interface
iwinfo 2>/dev/null | grep -E 'ESSID|Access Point|Signal|Noise' | head -8
echo R5_DONE2
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
    print("@@stderr:", err[:200])
r5.close()
gz.close()
