"""Deploy fixed discovery (v2) - robust: fresh GZ connection per device, no stdin games, paced."""
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

VERIFY = (
    "sh -n /etc/routermgmt/mgmt-port-discovery.sh && echo SYNTAX_OK || echo SYNTAX_FAIL; "
    "md5sum /etc/routermgmt/mgmt-port-discovery.sh | cut -d' ' -f1; "
    "echo guard=$(grep -c 'mihomo-guard' /etc/routermgmt/mgmt-port-discovery.sh); "
    "echo jump=$(iptables -t nat -S PREROUTING 2>/dev/null | grep -c 'MIHOMO$'); "
    "echo dns=$(iptables -t nat -S PREROUTING 2>/dev/null | grep -c 'MIHOMO_DNS$')"
)


def gz_connect():
    g = paramiko.SSHClient()
    g.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    g.connect("134.175.151.213", port=28722, username="ubuntu", password=PW,
              timeout=20, look_for_keys=False, allow_agent=False)
    return g


def run(g, cmd, t=40):
    _, o, e = g.exec_command(cmd, timeout=t)
    out = o.read().decode("utf-8", "replace").strip()
    err = e.read().decode("utf-8", "replace").strip()
    return out, err


# upload once
g = gz_connect()
sftp = g.open_sftp()
sftp.put(SRC, "/tmp/discovery-guardfix-20261108")
print("uploaded:", sftp.stat("/tmp/discovery-guardfix-20261108").st_size, "bytes")
sftp.close()
g.close()
time.sleep(2)

ok_devices = []
for name, port in DEVICES:
    print(f"@@@@@@ {name} ({port})")
    try:
        g = gz_connect()

        # 1) backup
        out, err = run(g,
            f"SSHPASS='{PW}' sshpass -e ssh -p {port} -o StrictHostKeyChecking=yes "
            f"root@127.0.0.1 'cp -p /etc/routermgmt/mgmt-port-discovery.sh "
            f"/etc/routermgmt/mgmt-port-discovery.sh.bak-guardfix-20261108 && "
            f"md5sum /etc/routermgmt/mgmt-port-discovery.sh.bak-guardfix-20261108 | cut -d\" \" -f1'")
        print("backup_md5:", out or err[:120])
        time.sleep(1.5)

        # 2) atomic push
        out, err = run(g,
            f"SSHPASS='{PW}' sshpass -e scp -O -P {port} -o StrictHostKeyChecking=yes "
            f"/tmp/discovery-guardfix-20261108 root@127.0.0.1:/etc/routermgmt/.discovery.new && "
            f"SSHPASS='{PW}' sshpass -e ssh -p {port} -o StrictHostKeyChecking=yes "
            f"root@127.0.0.1 'mv /etc/routermgmt/.discovery.new /etc/routermgmt/mgmt-port-discovery.sh'",
            t=50)
        if err and "post-quantum" not in err:
            print("push_err:", err[:120])
        time.sleep(1.5)

        # 3) verify
        out, err = run(g,
            f"SSHPASS='{PW}' sshpass -e ssh -p {port} -o StrictHostKeyChecking=yes "
            f"root@127.0.0.1 \"{VERIFY}\"")
        print(out)
        if NEW_MD5 in out:
            ok_devices.append(name)
        g.close()
    except Exception as exc:
        print("FAIL:", exc)
    sys.stdout.flush()
    time.sleep(3)

print("OK_DEVICES:", ",".join(ok_devices))
print("FLEET_DEPLOY_DONE")
