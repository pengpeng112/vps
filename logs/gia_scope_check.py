"""Scope check: full-request via GIA vs BWG from R1/R5/R8 (read-only, uses PROXY select + restore)."""
import os
import sys
import time

import paramiko

PW = os.environ.get("DEVPW")
if not PW:
    sys.exit("DEVPW not set")

# per-device script: switch PROXY (select) to node, test, restore to AUTO
TPL = r"""
probe() {
  curl --noproxy '*' -fsS -m 4 -X PUT -H 'Content-Type: application/json' --data "{\"name\":\"$1\"}" http://127.0.0.1:9090/proxies/PROXY >/dev/null 2>&1
  sleep 1
  c1=$(curl -4 -sS -m 10 -x http://127.0.0.1:7890 -o /dev/null -w '%{http_code}/%{time_total}' https://www.gstatic.com/generate_204 2>/dev/null)
  c2=$(curl -4 -sS -m 10 -x http://127.0.0.1:7890 -o /dev/null -w '%{http_code}/%{time_total}' https://www.google.com/ 2>/dev/null)
  echo "$1 gstatic=$c1 google=$c2"
}
probe US666-CN2GIA-01
probe BWG-HY2-JPTY-01
curl --noproxy '*' -fsS -m 4 -X PUT -H 'Content-Type: application/json' --data '{"name":"AUTO"}' http://127.0.0.1:9090/proxies/PROXY >/dev/null 2>&1
echo "restored=$(curl --noproxy '*' -fsS -m 5 http://127.0.0.1:9090/proxies/PROXY 2>/dev/null | jsonfilter -e '@.now')"
"""

gz = paramiko.SSHClient()
gz.set_missing_host_key_policy(paramiko.AutoAddPolicy())
gz.connect("134.175.151.213", port=28722, username="ubuntu", password=PW,
           timeout=20, look_for_keys=False, allow_agent=False)

for name, port in [("R1", 13222), ("R5", 13225), ("R8", 13230)]:
    print(f"@@@@@@ {name}")
    try:
        chan = gz.get_transport().open_channel("direct-tcpip", ("127.0.0.1", port), ("127.0.0.1", 0))
        d = paramiko.SSHClient()
        d.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        d.connect("127.0.0.1", port=port, username="root", password=PW, sock=chan,
                  timeout=15, look_for_keys=False, allow_agent=False)
        stdin, stdout, stderr = d.exec_command("sh -s", timeout=90)
        stdin.channel.sendall(TPL.encode())
        stdin.channel.shutdown_write()
        print(stdout.read().decode("utf-8", "replace").strip())
        d.close()
    except Exception as exc:
        print("FAIL:", exc)
    sys.stdout.flush()
    time.sleep(2)

gz.close()
print("SCOPE_DONE")
