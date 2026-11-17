"""Athena egress-node deep recon: node inventory + reachability (real tools). READ-ONLY."""
import os
import sys

import paramiko

PW = os.environ.get("ATH_PW")
if not PW:
    sys.exit("ATH_PW not set")

SCRIPT = r"""
echo "@@heartbeat_state"
curl --noproxy '*' -sk -m 6 -o /dev/null -w 'mgmt18443=%{http_code}\n' https://127.0.0.1:18443/api/v1/health
curl --noproxy '*' -fsS -m 5 http://127.0.0.1:9090/proxies/PROXY 2>/dev/null | jsonfilter -e '@.now'
curl --noproxy '*' -fsS -m 5 http://127.0.0.1:9090/proxies/AUTO 2>/dev/null | jsonfilter -e '@.now'
echo "@@node_inventory"
CFG=$(ls /etc/openclash/config/*.yaml 2>/dev/null | head -1)
[ -z "$CFG" ] && CFG=$(uci -q get openclash.config.config_path 2>/dev/null)
[ -z "$CFG" ] && CFG=/etc/mihomo/config.yaml
echo "cfg=$CFG"
grep -E '^  - name:|^    name:' "$CFG" 2>/dev/null | head -20
echo "@@node_servers"
awk '/^  - name:/{n=$0} /server:|port:|type:/{if(n!=""){print n" "$0; n=""}}' "$CFG" 2>/dev/null | grep -vE 'uuid|password|psk' | sed 's/name: *//' | head -24
echo "@@groups"
grep -nE '^\s*- name: "(PROXY|AUTO|NODE-PICK|AIRPORT|US-MEDIA|CN-MEDIA|DOWNLOAD)"' "$CFG" 2>/dev/null | head -10
echo "@@nc_reach"
probe() {
  nc "$1" "$2" </dev/null >/tmp/ncout 2>&1 &
  P=$!
  sleep 5
  kill $P 2>/dev/null
  if [ -s /tmp/ncout ]; then echo "$1:$2 REACHABLE($(head -c 12 /tmp/ncout | tr -d '\r\n'))"; else echo "$1:$2 NO_DATA"; fi
}
probe 159.223.36.119 443
probe 154.36.139.155 443
probe 154.36.139.155 2053
probe 104.18.32.7 2053
probe 162.159.135.42 2053
probe 134.175.151.213 28722
probe 134.175.151.213 8443
echo "@@bwg_udp_note"
netstat -tn 2>/dev/null | grep -c '74.82.203.175' || true
echo "@@exit_now"
curl -4 -sS -m 12 -x http://127.0.0.1:7890 https://api.ipify.org 2>/dev/null; echo " <-current-exit"
echo "@@done_r"
"""

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.2.1", port=22, username="root", password=PW,
          timeout=15, look_for_keys=False, allow_agent=False)
stdin, stdout, stderr = c.exec_command("sh -s", timeout=120)
stdin.channel.sendall(SCRIPT.encode())
stdin.channel.shutdown_write()
print(stdout.read().decode("utf-8", "replace"))
err = stderr.read().decode("utf-8", "replace")
if err.strip():
    print("@@stderr:", err[:300])
c.close()
