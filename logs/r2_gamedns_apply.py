"""R2 game-DNS P0 pilot: apply mode (authorized pilot, expected_sha verified against backup)."""
import os
import sys
from pathlib import Path

import paramiko

PW = os.environ.get("DEVPW")
if not PW:
    sys.exit("DEVPW not set")

EXPECTED_SHA = "e34a4de09f577b55576f9ffc81b5ac71df410790d58d5be1842ce49cfa1bd07f"
script_text = Path(r"F:\vps\review\fleet-game-dns-reference-20261116.sh").read_text(encoding="utf-8")

gz = paramiko.SSHClient()
gz.set_missing_host_key_policy(paramiko.AutoAddPolicy())
gz.connect("134.175.151.213", port=28722, username="ubuntu", password=PW,
           timeout=20, look_for_keys=False, allow_agent=False)
chan = gz.get_transport().open_channel("direct-tcpip", ("127.0.0.1", 13223), ("127.0.0.1", 0))
r2 = paramiko.SSHClient()
r2.set_missing_host_key_policy(paramiko.AutoAddPolicy())
r2.connect("127.0.0.1", port=13223, username="root", password=PW, sock=chan,
           timeout=20, look_for_keys=False, allow_agent=False)

# fixed allow-list device id; mode=apply; sha must equal the just-read value
command = "sh -s -- rtr-a9bcba apply " + EXPECTED_SHA
stdin, stdout, stderr = r2.exec_command(command, timeout=120)
stdin.channel.sendall(script_text.encode("utf-8"))
stdin.channel.shutdown_write()
out = stdout.read().decode("utf-8", "replace")
rc = stdout.channel.recv_exit_status()
err = stderr.read().decode("utf-8", "replace")
print(out)
print(f"exit={rc}")
if err.strip():
    print("stderr:", err[:300])
r2.close()
gz.close()
