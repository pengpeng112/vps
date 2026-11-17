"""Fleet relief pin: PROXY -> BWG-HY2 on all online devices (runtime PUT, reversible).
R4 already pinned this morning - only verify. Athens via LAN."""
import os
import sys
import time

import paramiko

PW = os.environ.get("DEVPW")
if not PW:
    sys.exit("DEVPW not set")

SNR_SCRIPT = r"""
CODE=$(curl --noproxy '*' -sS -m 8 -X PUT -H 'Content-Type: application/json' --data '{"name":"BWG-HY2-JPTY-01"}' -o /dev/null -w '%{http_code}' http://127.0.0.1:9090/proxies/PROXY)
NOW=$(curl --noproxy '*' -fsS -m 5 http://127.0.0.1:9090/proxies/PROXY 2>/dev/null | jsonfilter -e '@.now')
T=$(curl -4 -sS -m 10 -x http://127.0.0.1:7890 -o /dev/null -w '%{http_code}/%{time_total}s' https://www.gstatic.com/generate_204 2>/dev/null)
E=$(curl -4 -sS -m 10 -x http://127.0.0.1:7890 https://api.ipify.org 2>/dev/null)
echo "put=$CODE now=$NOW gstatic=$T exit=$E"
"""

gz = paramiko.SSHClient()
gz.set_missing_host_key_policy(paramiko.AutoAddPolicy())
gz.connect("134.175.151.213", port=28722, username="ubuntu", password=PW,
           timeout=20, look_for_keys=False, allow_agent=False)

DEVICES = [("R1", 13222), ("R2", 13223), ("R3", 13227), ("R4-verify-only", 13224),
           ("R5", 13225), ("R6", 13226), ("R8", 13230), ("R9", 13231), ("GL", 13234)]

for name, port in DEVICES:
    print(f"@@@@@@ {name}")
    try:
        chan = gz.get_transport().open_channel("direct-tcpip", ("127.0.0.1", port), ("127.0.0.1", 0))
        d = paramiko.SSHClient()
        d.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        d.connect("127.0.0.1", port=port, username="root", password=PW, sock=chan,
                  timeout=15, look_for_keys=False, allow_agent=False)
        if "verify" in name:
            script = r"""
NOW=$(curl --noproxy '*' -fsS -m 5 http://127.0.0.1:9090/proxies/PROXY 2>/dev/null | jsonfilter -e '@.now')
T=$(curl -4 -sS -m 10 -x http://127.0.0.1:7890 -o /dev/null -w '%{http_code}/%{time_total}s' https://www.gstatic.com/generate_204 2>/dev/null)
echo "now=$NOW gstatic=$T"
"""
        else:
            script = SNR_SCRIPT
        stdin, stdout, stderr = d.exec_command("sh -s", timeout=60)
        stdin.channel.sendall(script.encode())
        stdin.channel.shutdown_write()
        print(stdout.read().decode("utf-8", "replace").strip())
        d.close()
    except Exception as exc:
        print("FAIL:", exc)
    sys.stdout.flush()
    time.sleep(2.5)

gz.close()

print("@@@@@@ ATHENA (LAN)")
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.2.1", port=22, username="root", password="[REDACTED]",
          timeout=15, look_for_keys=False, allow_agent=False)
stdin, stdout, stderr = c.exec_command("sh -s", timeout=60)
stdin.channel.sendall(SNR_SCRIPT.encode())
stdin.channel.shutdown_write()
print(stdout.read().decode("utf-8", "replace").strip())
c.close()
print("FLEET_PIN_DONE")
