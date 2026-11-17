"""Fleet egress node health check - READ-ONLY.
Passive: mihomo /proxies alive+delay (refreshed every 120s by lazy:false).
Active: real-time delay tests on R7 only.
One GZ connection, direct-tcpip channels per device, paced to avoid sshd rate limit."""
import os
import sys
import time

import paramiko

PW = os.environ.get("DEVPW")
if not PW:
    sys.exit("DEVPW not set")

DEVICES = [
    ("R1", 13222), ("R2", 13223), ("R3", 13227), ("R4", 13224),
    ("R5", 13225), ("R6", 13226), ("R7", 13229), ("R8", 13230),
]

NODES = "US666-CN2GIA-01 EDGE-TUNNEL-V2 GIA-HY2-US-01 BWG-VLESS-JPTY-01 CF-CDN-GIA-01 CF2-CDN-GIA-01"

SCRIPT_PASSIVE = f"""
echo "@@dev"
date '+%H:%M:%S'
echo "mihomo=$(pidof mihomo | wc -w)"
curl --noproxy '*' -fsS -m 5 http://127.0.0.1:9090/version >/dev/null 2>&1 && echo API=OK || {{ echo API=FAIL; exit 0; }}
echo "@@groups"
for g in PROXY AUTO NODE-PICK AIRPORT; do
  j=$(curl --noproxy '*' -fsS -m 5 "http://127.0.0.1:9090/proxies/$g" 2>/dev/null) || {{ echo "$g FAIL"; continue; }}
  now=$(jsonfilter -s "$j" -e '@.now' 2>/dev/null)
  alive=$(jsonfilter -s "$j" -e '@.alive' 2>/dev/null)
  echo "$g now=$now alive=$alive"
done
echo "@@nodes"
for n in {NODES}; do
  j=$(curl --noproxy '*' -fsS -m 5 "http://127.0.0.1:9090/proxies/$n" 2>/dev/null) || {{ echo "$n ABSENT"; continue; }}
  alive=$(jsonfilter -s "$j" -e '@.alive' 2>/dev/null)
  delay=$(echo "$j" | grep -o '"delay":[0-9]*' | tail -1 | cut -d: -f2)
  echo "$n alive=$alive delay=${{delay:-NA}}ms"
done
echo "@@egress"
curl -4 --noproxy '' --proxy http://127.0.0.1:7890 -fsS --connect-timeout 5 --max-time 15 https://api.ipify.org 2>/dev/null || echo EGRESS_FAIL
echo
echo FLEET_DEV_DONE
"""

SCRIPT_ACTIVE = f"""
echo "@@active"
for n in {NODES}; do
  r=$(curl --noproxy '*' -fsS -m 14 -G "http://127.0.0.1:9090/proxies/$n/delay" \
    --data-urlencode 'url=https://www.gstatic.com/generate_204' \
    --data-urlencode 'timeout=8000' 2>/dev/null | grep -o '"delay":[0-9]*' | cut -d: -f2)
  echo "$n -> ${{r:-FAIL/TIMEOUT}}ms"
done
echo ACTIVE_DONE
"""

gz = paramiko.SSHClient()
gz.set_missing_host_key_policy(paramiko.AutoAddPolicy())
gz.connect("134.175.151.213", port=28722, username="ubuntu", password=PW,
           timeout=20, look_for_keys=False, allow_agent=False)

_, o_hb, _ = gz.exec_command(
    "sudo -u postgres psql routermgmt -t -A -F'|' -c \"SELECT name,"
    "round(extract(epoch from (now()-last_heartbeat_at))) FROM devices "
    "ORDER BY name;\" 2>&1",
    timeout=30,
)
print("@@fleet-heartbeat(age_s)")
print(o_hb.read().decode().strip())
print()

for name, port in DEVICES:
    print(f"@@@@@@ {name} ({port})")
    try:
        chan = gz.get_transport().open_channel(
            "direct-tcpip", ("127.0.0.1", port), ("127.0.0.1", 0))
        dev = paramiko.SSHClient()
        dev.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        dev.connect("127.0.0.1", port=port, username="root", password=PW,
                    sock=chan, timeout=20, look_for_keys=False, allow_agent=False)
        stdin, stdout, stderr = dev.exec_command("sh -s", timeout=90)
        stdin.channel.sendall(SCRIPT_PASSIVE.encode())
        stdin.channel.shutdown_write()
        out = stdout.read().decode("utf-8", "replace")
        print(out.strip())
        if name == "R7":
            stdin2, stdout2, _ = dev.exec_command("sh -s", timeout=120)
            stdin2.channel.sendall(SCRIPT_ACTIVE.encode())
            stdin2.channel.shutdown_write()
            print(stdout2.read().decode("utf-8", "replace").strip())
        dev.close()
    except Exception as exc:
        print(f"CONNECT_FAIL: {exc}")
    sys.stdout.flush()
    time.sleep(3)

gz.close()
print("@@FLEET_CHECK_COMPLETE")
