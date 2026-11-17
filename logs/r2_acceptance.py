"""R2 supplemental acceptance: client-view DNS via LAN IP, normal domain, heartbeat x2."""
import os
import time

import paramiko

PW = os.environ.get("DEVPW")
gz = paramiko.SSHClient()
gz.set_missing_host_key_policy(paramiko.AutoAddPolicy())
gz.connect("134.175.151.213", port=28722, username="ubuntu", password=PW,
           timeout=20, look_for_keys=False, allow_agent=False)

chan = gz.get_transport().open_channel("direct-tcpip", ("127.0.0.1", 13223), ("127.0.0.1", 0))
r2 = paramiko.SSHClient()
r2.set_missing_host_key_policy(paramiko.AutoAddPolicy())
r2.connect("127.0.0.1", port=13223, username="root", password=PW, sock=chan,
           timeout=20, look_for_keys=False, allow_agent=False)

s = r"""
LANIP=$(ip -4 addr show br-lan 2>/dev/null | grep -o 'inet [0-9.]*' | head -1 | cut -d' ' -f2)
echo "lan_ip=$LANIP"
echo '@@client_view_games'
for h in gpp-tj100-1.lol.qq.com tqos.gamesafe.qq.com tqos.anticheatexpert.com ied-tqos-tgp.qq.com ied-tqos.qq.com; do
  a=$(nslookup $h $LANIP 2>/dev/null | grep -oE '198\.18\.[0-9]+\.[0-9]+|[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+' | grep -v "^$LANIP" | head -2 | tr '\n' ' ')
  echo "$h -> ${a:-NO_ANSWER}"
done
echo '@@normal_domain_unchanged'
nslookup www.baidu.com $LANIP 2>/dev/null | tail -3
echo '@@upstream_kept'
uci -q get dhcp.@dnsmasq[0].server | tr '\n' '|'; echo
echo '@@uci_pending'
uci changes dhcp | wc -l
echo R2_ACC_DONE
"""
stdin, stdout, stderr = r2.exec_command("sh -s", timeout=90)
stdin.channel.sendall(s.encode())
stdin.channel.shutdown_write()
print(stdout.read().decode("utf-8", "replace").strip())
e = stderr.read().decode().strip()
if e:
    print("stderr:", e[:150])
r2.close()

time.sleep(70)
_, o, _ = gz.exec_command(
    "sudo -u postgres psql routermgmt -t -A -F'|' -c "
    "\"SELECT round(extract(epoch FROM now()-last_heartbeat_at)) FROM devices WHERE id='rtr-a9bcba';\"",
    timeout=20)
print("@@hb_age_after_70s:", o.read().decode().strip(), "s")
gz.close()
print("R2_ACCEPTANCE_DONE")
