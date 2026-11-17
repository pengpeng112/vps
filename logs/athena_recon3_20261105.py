"""Athena round-3: WG hub liveness + temporary wg0-route probe to GZ + pubkey fetch. READ-ONLY (route auto-reverted)."""
import os
import sys

import paramiko

PW = os.environ.get("ATH_PW")
if not PW:
    sys.exit("ATH_PW not set")

SCRIPT = r"""
echo "@@wg_show"
wg show 2>/dev/null | head -20 || echo WG_FAIL
echo "@@node_port"
grep -A4 'name: "VPS-SG-01"' /etc/openclash/config.yaml 2>/dev/null | grep -E 'name|server|port|type' || grep -rn 'VPS-SG-01' /etc/openclash/*.yaml 2>/dev/null | grep -E 'server|port' | head -4 || echo NODE_CFG_NOT_FOUND
echo "@@do_ports_direct"
for t in 159.223.36.119:22 159.223.36.119:28722; do
  (timeout 4 bash -c "echo > /dev/tcp/$t" 2>/dev/null && echo "$t OK") || echo "$t FAIL"
done
echo "@@wg_route_probe"
ip route add 134.175.151.213/32 via 10.10.0.1 dev wg0 2>/dev/null && echo ROUTE_ADDED || echo ROUTE_ADD_FAIL
sleep 1
(timeout 6 bash -c 'echo > /dev/tcp/134.175.151.213/28722' 2>/dev/null && echo GZ_28722_VIA_WG_OK) || echo GZ_28722_VIA_WG_FAIL
(timeout 6 bash -c 'echo > /dev/tcp/134.175.151.213/8443' 2>/dev/null && echo GZ_8443_VIA_WG_OK) || echo GZ_8443_VIA_WG_FAIL
ip route del 134.175.151.213/32 via 10.10.0.1 dev wg0 2>/dev/null && echo ROUTE_REVERTED || echo ROUTE_DEL_FAIL
echo "@@pubkey"
cat /etc/routermgmt/id_dropbear_mgmt.pub
echo "@@ssh_client"
/usr/bin/ssh -V 2>&1 | head -1
echo "@@done3"
"""

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.2.1", port=22, username="root", password=PW,
          timeout=15, look_for_keys=False, allow_agent=False)
stdin, stdout, stderr = c.exec_command("sh -s", timeout=90)
stdin.channel.sendall(SCRIPT.encode())
stdin.channel.shutdown_write()
print(stdout.read().decode("utf-8", "replace"))
err = stderr.read().decode("utf-8", "replace")
if err.strip():
    print("@@stderr:", err[:300])
c.close()
