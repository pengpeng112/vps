"""Fleet NODE-PICK full member scan - READ-ONLY. Includes R9 (back online).
R7 excluded (offline, being watched by parallel session 19:3x)."""
import os
import sys
import time

import paramiko

PW = os.environ.get("DEVPW")
if not PW:
    sys.exit("DEVPW not set")

DEVICES = [
    ("R1", 13222), ("R2", 13223), ("R3", 13227), ("R4", 13224),
    ("R5", 13225), ("R6", 13226), ("R8", 13230), ("R9", 13231),
]

SCRIPT = r"""
echo "@@dev"
date '+%H:%M:%S'
curl --noproxy '*' -fsS -m 5 http://127.0.0.1:9090/version >/dev/null 2>&1 || { echo API_FAIL; exit 0; }
PN=$(curl --noproxy '*' -fsS -m 5 "http://127.0.0.1:9090/proxies/PROXY" | jsonfilter -e '@.now')
echo "PROXY.now=$PN"
echo "@@members"
j=$(curl --noproxy '*' -fsS -m 5 "http://127.0.0.1:9090/proxies/NODE-PICK" 2>/dev/null) || { echo NP_FAIL; exit 0; }
for n in $(jsonfilter -s "$j" -e '@.all[*]' 2>/dev/null); do
  m=$(curl --noproxy '*' -fsS -m 5 "http://127.0.0.1:9090/proxies/$n" 2>/dev/null) || { echo "$n FETCH_FAIL"; continue; }
  a=$(jsonfilter -s "$m" -e '@.alive' 2>/dev/null)
  d=$(echo "$m" | grep -o '"delay":[0-9]*' | tail -1 | cut -d: -f2)
  echo "$n alive=$a delay=${d:-NA}"
done
j2=$(curl --noproxy '*' -fsS -m 5 "http://127.0.0.1:9090/proxies/AIRPORT" 2>/dev/null)
[ -n "$j2" ] && echo "AIRPORT alive=$(jsonfilter -s "$j2" -e '@.alive' 2>/dev/null) nodes=$(jsonfilter -s "$j2" -e '@.all' 2>/dev/null | wc -w)"
echo "@@egress"
curl -4 --noproxy '' --proxy http://127.0.0.1:7890 -fsS --connect-timeout 5 --max-time 15 https://api.ipify.org 2>/dev/null || echo EGRESS_FAIL
echo
echo SCAN_DONE
"""

gz = paramiko.SSHClient()
gz.set_missing_host_key_policy(paramiko.AutoAddPolicy())
gz.connect("134.175.151.213", port=28722, username="ubuntu", password=PW,
           timeout=20, look_for_keys=False, allow_agent=False)

for name, port in DEVICES:
    print(f"@@@@@@ {name} ({port})")
    try:
        chan = gz.get_transport().open_channel(
            "direct-tcpip", ("127.0.0.1", port), ("127.0.0.1", 0))
        dev = paramiko.SSHClient()
        dev.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        dev.connect("127.0.0.1", port=port, username="root", password=PW,
                    sock=chan, timeout=20, look_for_keys=False, allow_agent=False)
        stdin, stdout, stderr = dev.exec_command("sh -s", timeout=100)
        stdin.channel.sendall(SCRIPT.encode())
        stdin.channel.shutdown_write()
        print(stdout.read().decode("utf-8", "replace").strip())
        dev.close()
    except Exception as exc:
        print(f"CONNECT_FAIL: {exc}")
    sys.stdout.flush()
    time.sleep(3)

gz.close()
print("@@MEMBER_SCAN_COMPLETE")
