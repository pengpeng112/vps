"""Comprehensive report: 14d foreign(proxy) traffic ranking, 7d new devices, per-device health + nodes."""
import os
import sys
import time

import paramiko

PW = os.environ.get("DEVPW")
if not PW:
    sys.exit("DEVPW not set")

NODE_SCRIPT = r"""
echo "@@health"
date '+%H:%M:%S'
echo "mihomo=$(ps w | grep -c '[m]ihomo') agent=$(ps w | grep -c '[r]outermgmt-agent')"
echo "jump=$(iptables -t nat -S PREROUTING 2>/dev/null | grep -c 'MIHOMO$')"
echo "wanline=$(tail -1 /etc/routermgmt/logs/wan-watch.log 2>/dev/null | grep -o 'gw_ping=[a-z]* inet_ping=[a-z]* http=[a-z]*')"
echo "@@nodes"
j=$(curl --noproxy '*' -fsS -m 5 http://127.0.0.1:9090/proxies/NODE-PICK 2>/dev/null) || { echo NP_FAIL; exit 0; }
for n in $(jsonfilter -s "$j" -e '@.all[*]' 2>/dev/null); do
  m=$(curl --noproxy '*' -fsS -m 5 "http://127.0.0.1:9090/proxies/$n" 2>/dev/null) || { echo "$n FETCH_FAIL"; continue; }
  a=$(jsonfilter -s "$m" -e '@.alive' 2>/dev/null)
  d=$(echo "$m" | grep -o '"delay":[0-9]*' | tail -1 | cut -d: -f2)
  echo "$n alive=$a delay=${d:-NA}"
done
echo "airport=$(curl --noproxy '*' -fsS -m 5 http://127.0.0.1:9090/proxies/AIRPORT 2>/dev/null | jsonfilter -e '@.alive')"
echo "@@exit"
curl -4 -sS -m 12 -x http://127.0.0.1:7890 https://api.ipify.org 2>/dev/null || echo EXIT_FAIL
echo
echo DEV_DONE
"""

ATH_SCRIPT = r"""
echo "@@health"
echo "mihomo=$(ps w | grep -c '[m]ihomo') agent=$(ps w | grep -c '[r]outermgmt-agent')"
echo "redir=$(nft list table inet mihomo 2>/dev/null | grep -c 'redirect to :7892')"
echo "@@exit"
curl -4 -sS -m 12 -x http://127.0.0.1:7890 https://api.ipify.org 2>/dev/null; echo " <-exit(Do)"
echo "@@mgmt"
curl -sk -m 6 -o /dev/null -w 'mgmt18443=%{http_code}\n' https://127.0.0.1:18443/api/v1/health
echo ATH_DONE
"""

gz = paramiko.SSHClient()
gz.set_missing_host_key_policy(paramiko.AutoAddPolicy())
gz.connect("134.175.151.213", port=28722, username="ubuntu", password=PW,
           timeout=20, look_for_keys=False, allow_agent=False)


def sql(q, t=60):
    _, o, e = gz.exec_command(
        "sudo -u postgres psql routermgmt -t -A -F'|' -c \"" + q.replace('"', '\\"') + "\" 2>&1", timeout=t)
    return o.read().decode("utf-8", "replace").strip()


print("@@1_14d_foreign_proxy_traffic_ranking (MB, est. from proxy-node counters)")
print(sql(
    "WITH s AS (SELECT device_id, ts, proxy_rx_bytes AS prb, proxy_tx_bytes AS ptb, "
    "LAG(proxy_rx_bytes) OVER w AS prx, LAG(proxy_tx_bytes) OVER w AS ptx, "
    "LAG(proxy_counter_epoch) OVER w AS pepoch FROM metrics "
    "WHERE ts >= now() - interval '14 days' AND proxy_rx_bytes IS NOT NULL "
    "AND proxy_tx_bytes IS NOT NULL WINDOW w AS (PARTITION BY device_id ORDER BY ts)), "
    "d AS (SELECT device_id, ts, CASE WHEN proxy_counter_epoch=pepoch THEN GREATEST(prb-prx,0) ELSE 0 END AS rx, "
    "CASE WHEN proxy_counter_epoch=pepoch THEN GREATEST(ptb-ptx,0) ELSE 0 END AS tx "
    "FROM s WHERE prx IS NOT NULL) "
    "SELECT dn.name, round(SUM(d.rx)/1048576.0) AS rx_mb, round(SUM(d.tx)/1048576.0) AS tx_mb, "
    "round((SUM(d.rx)+SUM(d.tx))/1048576.0) AS total_mb "
    "FROM d JOIN devices dn ON dn.id=d.device_id GROUP BY dn.name ORDER BY total_mb DESC;"
))

print("@@2_14d_wan_total_traffic_ranking (MB)")
print(sql(
    "WITH s AS (SELECT device_id, ts, boot_id, wan_rx_bytes AS wrb, wan_tx_bytes AS wtb, "
    "LAG(wan_rx_bytes) OVER w AS wrx, LAG(wan_tx_bytes) OVER w AS wtx, LAG(boot_id) OVER w AS pboot "
    "FROM metrics WHERE ts >= now() - interval '14 days' AND wan_rx_bytes IS NOT NULL "
    "AND wan_tx_bytes IS NOT NULL WINDOW w AS (PARTITION BY device_id ORDER BY ts)), "
    "d AS (SELECT device_id, CASE WHEN boot_id=pboot THEN GREATEST(wrb-wrx,0) ELSE 0 END AS rx, "
    "CASE WHEN boot_id=pboot THEN GREATEST(wtb-wtx,0) ELSE 0 END AS tx FROM s WHERE wrx IS NOT NULL) "
    "SELECT dn.name, round(SUM(d.rx)/1048576.0) AS rx_mb, round(SUM(d.tx)/1048576.0) AS tx_mb, "
    "round((SUM(d.rx)+SUM(d.tx))/1048576.0) AS total_mb "
    "FROM d JOIN devices dn ON dn.id=d.device_id GROUP BY dn.name ORDER BY total_mb DESC;"
))

print("@@3_new_devices_7d")
print(sql(
    "SELECT name, to_char(created_at,'MM-DD HH24:MI'), to_char(first_online_at,'MM-DD HH24:MI') "
    "FROM devices WHERE created_at > now() - interval '7 days' "
    "OR first_online_at > now() - interval '7 days';"
) or "(none)")

print("@@4_offline_status")
print(sql(
    "SELECT name, to_char(last_heartbeat_at,'MM-DD HH24:MI') FROM devices "
    "WHERE name LIKE 'SN-R%' AND last_heartbeat_at <= now() - interval '3 days' "
    "ORDER BY last_heartbeat_at DESC;"
))

print("@@5_per_device_health_and_nodes")
DEVICES = [("R1", 13222), ("R2", 13223), ("R3", 13227), ("R4", 13224),
           ("R5", 13225), ("R6", 13226), ("R8", 13230), ("R9", 13231)]
for name, port in DEVICES:
    print(f"@@ {name}")
    try:
        chan = gz.get_transport().open_channel("direct-tcpip", ("127.0.0.1", port), ("127.0.0.1", 0))
        d = paramiko.SSHClient()
        d.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        d.connect("127.0.0.1", port=port, username="root", password=PW, sock=chan,
                  timeout=15, look_for_keys=False, allow_agent=False)
        stdin, stdout, stderr = d.exec_command("sh -s", timeout=60)
        stdin.channel.sendall(NODE_SCRIPT.encode())
        stdin.channel.shutdown_write()
        print(stdout.read().decode("utf-8", "replace").strip())
        d.close()
    except Exception as exc:
        print("FAIL:", exc)
    sys.stdout.flush()
    time.sleep(2)

print("@@ ATHENA")
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
print("REPORT_DONE")
