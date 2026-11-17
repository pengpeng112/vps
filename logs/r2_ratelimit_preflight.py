"""R2 v2 rate-limit preflight - read-only."""
import os
from pathlib import Path

import paramiko

PW = os.environ["DEVPW"]
gz = paramiko.SSHClient()
gz.set_missing_host_key_policy(paramiko.AutoAddPolicy())
gz.connect("134.175.151.213", port=28722, username="ubuntu", password=PW,
           timeout=20, look_for_keys=False, allow_agent=False)
_, o, _ = gz.exec_command(
    "sudo -u postgres psql routermgmt -t -A -F'|' -c \"BEGIN READ ONLY;"
    "SELECT id,vps_limit_kbps,round(extract(epoch FROM now()-last_heartbeat_at)) "
    "FROM devices WHERE id='rtr-a9bcba';"
    "SELECT count(*) FROM tasks WHERE device_id='rtr-a9bcba' AND status IN ('pending','running');"
    "COMMIT;\" 2>&1", timeout=30)
print("@@db")
print(o.read().decode().strip())
chan = gz.get_transport().open_channel("direct-tcpip", ("127.0.0.1", 13223), ("127.0.0.1", 0))
d = paramiko.SSHClient()
d.set_missing_host_key_policy(paramiko.AutoAddPolicy())
d.connect("127.0.0.1", port=13223, username="root", password=PW, sock=chan,
          timeout=20, look_for_keys=False, allow_agent=False)
payload = Path(r"F:\vps\logs\r2_ratelimit_preflight.sh").read_bytes()
stdin, stdout, stderr = d.exec_command("sh -s", timeout=90)
stdin.channel.sendall(payload)
stdin.channel.shutdown_write()
print(stdout.read().decode("utf-8", "replace").strip())
err = stderr.read().decode("utf-8", "replace").strip()
if err:
    print("stderr:", err[:200])
d.close()
gz.close()
