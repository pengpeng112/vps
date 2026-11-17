"""Re-verify guard fix on-device (direct-tcpip stdin pattern) + R9 retry with accept-new."""
import os
import sys
import time

import paramiko

PW = os.environ.get("DEVPW")
NEW_MD5 = "31ba9fa09a184d892d8e408c74725d32"
DEV_PW = PW  # same fleet password

DEVICES = [("R1", 13222), ("R2", 13223), ("R3", 13227), ("R4", 13224),
           ("R5", 13225), ("R6", 13226), ("R8", 13230), ("R9", 13231)]

VERIFY = r"""
echo "md5=$(md5sum /etc/routermgmt/mgmt-port-discovery.sh | cut -d' ' -f1)"
echo "guard=$(grep -c 'mihomo-guard' /etc/routermgmt/mgmt-port-discovery.sh)"
echo "jump=$(iptables -t nat -S PREROUTING 2>/dev/null | grep -c 'MIHOMO$')"
echo "dns=$(iptables -t nat -S PREROUTING 2>/dev/null | grep -c 'MIHOMO_DNS$')"
if iptables -t nat -S PREROUTING 2>/dev/null | grep -q 'MIHOMO$'; then
  echo GUARD_LOGIC_WOULD_PASS
else
  echo GUARD_LOGIC_WOULD_REBUILD
fi
echo VERIFY_DONE
"""

gz = paramiko.SSHClient()
gz.set_missing_host_key_policy(paramiko.AutoAddPolicy())
gz.connect("134.175.151.213", port=28722, username="ubuntu", password=PW,
           timeout=20, look_for_keys=False, allow_agent=False)

# R9 retry: push with accept-new (first connection pins its host key)
print("@@@@@@ R9 retry-push")
try:
    _, o, e = gz.exec_command(
        f"SSHPASS='{PW}' sshpass -e scp -O -P 13231 -o StrictHostKeyChecking=accept-new "
        f"/tmp/discovery-guardfix-20261108 root@127.0.0.1:/etc/routermgmt/.discovery.new && "
        f"SSHPASS='{PW}' sshpass -e ssh -p 13231 -o StrictHostKeyChecking=accept-new "
        f"root@127.0.0.1 'mv /etc/routermgmt/.discovery.new /etc/routermgmt/mgmt-port-discovery.sh && "
        f"sh -n /etc/routermgmt/mgmt-port-discovery.sh && echo R9_SYNTAX_OK'",
        timeout=50)
    o.channel.sendall(PW.encode())
    o.channel.shutdown_write()
    out = o.read().decode("utf-8", "replace").strip()
    err = e.read().decode("utf-8", "replace").strip()
    print("r9_push:", out[-200:] if out else "(no output)")
    if err and "post-quantum" not in err:
        print("r9_err:", err[:200])
except Exception as exc:
    print("r9 FAIL:", exc)
time.sleep(2)

for name, port in DEVICES:
    print(f"@@@@@@ {name}")
    try:
        chan = gz.get_transport().open_channel("direct-tcpip", ("127.0.0.1", port), ("127.0.0.1", 0))
        d = paramiko.SSHClient()
        d.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        d.connect("127.0.0.1", port=port, username="root", password=DEV_PW, sock=chan,
                  timeout=15, look_for_keys=False, allow_agent=False)
        stdin, stdout, stderr = d.exec_command("sh -s", timeout=45)
        stdin.channel.sendall(VERIFY.encode())
        stdin.channel.shutdown_write()
        out = stdout.read().decode("utf-8", "replace").strip()
        status = "OK" if NEW_MD5 in out and "GUARD_LOGIC_WOULD_PASS" in out else "CHECK!"
        print(out)
        print(f"--> {status}")
        d.close()
    except Exception as exc:
        print("FAIL:", exc)
    sys.stdout.flush()
    time.sleep(2)

gz.close()
print("REVERIFY_DONE")
