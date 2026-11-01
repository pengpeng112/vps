"""R7 read-only evidence collection via double-layer Paramiko direct-tcpip.
PC -> Guangzhou(28722) -> direct-tcpip 127.0.0.1:13229 -> R7. No file writes on device."""
import os
import sys
from pathlib import Path

import paramiko

PW = os.environ.get("DEVPW")
if not PW:
    sys.exit("DEVPW not set")

R7_PORT = 13229

gz = paramiko.SSHClient()
gz.set_missing_host_key_policy(paramiko.AutoAddPolicy())
gz.connect("134.175.151.213", port=28722, username="ubuntu", password=PW,
           timeout=20, look_for_keys=False, allow_agent=False)


def run_gz(cmd, t=30):
    _, o, _ = gz.exec_command(cmd, timeout=t)
    return o.read().decode("utf-8", "replace")


print("@@gz-platform")
print(run_gz(
    "systemctl is-active nginx gz-api; "
    "curl -sk -m 6 https://127.0.0.1:8443/api/v1/health; echo; "
    "ss -ltn | grep ':13229' | head -2"
).strip())

print("@@gz-db-fleet")
print(run_gz(
    "sudo -u postgres psql routermgmt -t -A -F'|' -c \"SELECT name,"
    "remote_ssh_port,remote_ssh_alive,status,"
    "to_char(last_heartbeat_at,'MM-DD HH24:MI:SS') FROM devices ORDER BY name;\""
).strip())

chan = gz.get_transport().open_channel("direct-tcpip", ("127.0.0.1", R7_PORT), ("127.0.0.1", 0))
r7 = paramiko.SSHClient()
r7.set_missing_host_key_policy(paramiko.AutoAddPolicy())
r7.connect("127.0.0.1", port=R7_PORT, username="root", password=PW, sock=chan,
           timeout=20, look_for_keys=False, allow_agent=False)

payload = Path(__file__).with_name("r7_check_20261101.sh").read_bytes()
stdin, stdout, stderr = r7.exec_command("sh -s", timeout=120)
stdin.channel.sendall(payload)
stdin.channel.shutdown_write()
out = stdout.read().decode("utf-8", "replace")
err = stderr.read().decode("utf-8", "replace")

print("@@r7-device")
print(out)
if err.strip():
    print("@@r7-stderr-head")
    print(err[:400])

r7.close()
gz.close()
Path(r"F:\vps\logs\r7-check-out-20261101.txt").write_text(out, encoding="utf-8")
print("@@saved")
