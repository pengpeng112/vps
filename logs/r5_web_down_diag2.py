"""R5 round-2: verify transparent-redirect rules existence + client-path simulation. READ-ONLY."""
import os
import sys

import paramiko

PW = os.environ.get("DEVPW")
if not PW:
    sys.exit("DEVPW not set")

SCRIPT = r"""
echo "@@nft_tables_now"
nft list tables 2>/dev/null
echo "@@mihomo_table_detail"
nft list table inet mihomo 2>/dev/null | head -30 || echo NO_INET_MIHOMO_TABLE
echo "@@iptables_check"
iptables -t nat -S 2>/dev/null | grep -E '7892|MIHOMO' | head -6 || echo no-iptables-rules
echo "@@mihomo_ipt_script"
ls -l /etc/mihomo-ipt.sh /usr/share/firewall/* 2>/dev/null | head -5
uci -q show firewall | grep -i 'mihomo\|include' | head -6
echo "@@fakeip_client_path_sim"
FAKEDNS=192.168.132.1
FA=$(nslookup www.baidu.com $FAKEDNS 2>/dev/null | grep -m1 'Address [0-9]*:' | grep -o '[0-9.]*$')
echo "fake_ip_for_baidu=$FA"
if [ -n "$FA" ]; then
  curl -4 -sS --noproxy '*' -m 8 -o /dev/null -w "via_fakeip_http=%{http_code} t=%{time_total}s\n" "http://$FA/" 2>&1 | tail -1
  nc -w 5 "$FA" 80 </dev/null >/dev/null 2>&1 && echo fakeip_tcp80_OK || echo fakeip_tcp80_FAIL
fi
echo "@@conntrack53_unreplied"
[ -f /proc/net/nf_conntrack ] && {
  total=$(grep -c 'dport=53' /proc/net/nf_conntrack 2>/dev/null)
  unrep=$(grep 'dport=53' /proc/net/nf_conntrack 2>/dev/null | grep -c 'UNREPLIED')
  echo "dport53_total=$total unreplied=$unrep"
} || echo no_conntrack_file
echo "@@hb_retry"
echo "@@lan_gw_ping_test"
ping -c 2 -W 2 192.168.132.245 2>&1 | tail -2
echo R5_DONE_R2
"""

gz = paramiko.SSHClient()
gz.set_missing_host_key_policy(paramiko.AutoAddPolicy())
gz.connect("134.175.151.213", port=28722, username="ubuntu", password=PW,
           timeout=20, look_for_keys=False, allow_agent=False)

_, o_hb, e_hb = gz.exec_command(
    "sudo -u postgres psql routermgmt -t -A -F'|' -c \"SELECT name,"
    "round(extract(epoch from (now()-last_heartbeat_at))),current_config_version "
    "FROM devices WHERE name='SN-R5-AX3000';\" 2>&1", timeout=20)
print("@@gz_db:", o_hb.read().decode().strip()[:200])

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
