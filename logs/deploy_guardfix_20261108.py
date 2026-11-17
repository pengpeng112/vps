"""Deploy fixed mgmt-port-discovery.sh (anchored mihomo-guard) to 8 online devices.
Per device: backup -> atomic push (.discovery.new + mv) -> sh -n gate -> md5 verify -> manual run."""
import os
import sys
import time

import paramiko

PW = os.environ.get("DEVPW")
if not PW:
    sys.exit("DEVPW not set")

NEW_MD5 = "31ba9fa09a184d892d8e408c74725d32"
SRC = r"F:\vps\mgmt\agent\mgmt-port-discovery.sh"
DEVICES = [("R1", 13222), ("R2", 13223), ("R3", 13227), ("R4", 13224),
           ("R5", 13225), ("R6", 13226), ("R8", 13230), ("R9", 13231)]

gz = paramiko.SSHClient()
gz.set_missing_host_key_policy(paramiko.AutoAddPolicy())
gz.connect("134.175.151.213", port=28722, username="ubuntu", password=PW,
           timeout=20, look_for_keys=False, allow_agent=False)

sftp = gz.open_sftp()
sftp.put(SRC, "/tmp/discovery-guardfix-20261108")
st = sftp.stat("/tmp/discovery-guardfix-20261108")
print(f"uploaded to GZ ({st.st_size} bytes)")
sftp.close()

VERIFY = r"""
sh -n /etc/routermgmt/mgmt-port-discovery.sh && echo SYNTAX_OK || echo SYNTAX_FAIL
md5sum /etc/routermgmt/mgmt-port-discovery.sh | cut -d' ' -f1
echo "guard=$(grep -c 'mihomo-guard' /etc/routermgmt/mgmt-port-discovery.sh)"
echo "chains_now: jump=$(iptables -t nat -S PREROUTING 2>/dev/null | grep -c 'MIHOMO$') dns=$(iptables -t nat -S PREROUTING 2>/dev/null | grep -c 'MIHOMO_DNS$')"
out=$(/etc/routermgmt/mgmt-port-discovery.sh 2>&1 | tail -1)
echo "manual_run_last_line=$out"
logread 2>/dev/null | grep -c 'mihomo-guard' || true
echo DEPLOY_OK
"""

for name, port in DEVICES:
    print(f"@@@@@@ {name} ({port})")
    try:
        # 1) backup current
        _, o, _ = gz.exec_command(
            f"SSHPASS='{PW}' sshpass -e ssh -p {port} -o StrictHostKeyChecking=yes "
            f"root@127.0.0.1 'cp -p /etc/routermgmt/mgmt-port-discovery.sh "
            f"/etc/routermgmt/mgmt-port-discovery.sh.bak-guardfix-20261108 && md5sum "
            f"/etc/routermgmt/mgmt-port-discovery.sh.bak-guardfix-20261108 | cut -d\" \" -f1'",
            timeout=30)
        o.channel.sendall(PW.encode())
        o.channel.shutdown_write()
        bak = o.read().decode().strip()
        print(f"backup={bak}")

        # 2) atomic push
        _, o, e = gz.exec_command(
            f"SSHPASS='{PW}' sshpass -e scp -O -P {port} -o StrictHostKeyChecking=yes "
            f"/tmp/discovery-guardfix-20261108 root@127.0.0.1:/etc/routermgmt/.discovery.new && "
            f"SSHPASS='{PW}' sshpass -e ssh -p {port} -o StrictHostKeyChecking=yes "
            f"root@127.0.0.1 'mv /etc/routermgmt/.discovery.new /etc/routermgmt/mgmt-port-discovery.sh'",
            timeout=40)
        o.channel.sendall(PW.encode())
        o.channel.shutdown_write()
        push_out = o.read().decode().strip()
        push_err = e.read().decode().strip()
        if push_err and "post-quantum" not in push_err:
            print("push_stderr:", push_err[:150])

        # 3) verify
        _, o, _ = gz.exec_command(
            f"SSHPASS='{PW}' sshpass -e ssh -p {port} -o StrictHostKeyChecking=yes "
            f"root@127.0.0.1 'sh -s'", timeout=60)
        o.channel.sendall(PW.encode())
        o.channel.sendall(VERIFY.encode())
        o.channel.shutdown_write()
        print(o.read().decode("utf-8", "replace").strip())
    except Exception as exc:
        print("FAIL:", exc)
    sys.stdout.flush()
    time.sleep(2)

gz.close()
print("FLEET_DEPLOY_DONE")
