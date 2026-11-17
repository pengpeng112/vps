"""Run read-only node stability probes on Athena via LAN."""
import os
from pathlib import Path

import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.2.1", port=22, username="root", password=os.environ["ATH_PW"],
          timeout=15, look_for_keys=False, allow_agent=False)
payload = Path(r"F:\vps\logs\athena_node_probe.sh").read_bytes()
stdin, stdout, stderr = c.exec_command("sh -s", timeout=240)
stdin.channel.sendall(payload)
stdin.channel.shutdown_write()
print(stdout.read().decode("utf-8", "replace").strip())
err = stderr.read().decode("utf-8", "replace").strip()
if err:
    print("stderr:", err[:300])
c.close()
