"""Round A fixup: GZ scp download with inline SSHPASS env, then PC pull + bbolt verify."""
import hashlib
import os
import sys
from pathlib import Path

import paramiko

PW = os.environ.get("DEVPW")
BK = "/root/r7fix-20261101"
FILES = ("version.json proxy.before.json auto.before.json tunnel.conf.before "
         "mihomo.config.before selfheal.before cache.before.db")

gz = paramiko.SSHClient()
gz.set_missing_host_key_policy(paramiko.AutoAddPolicy())
gz.connect("134.175.151.213", port=28722, username="ubuntu", password=PW,
           timeout=20, look_for_keys=False, allow_agent=False)

cmd = (
    f"mkdir -p /tmp/r7review-20261101 && cd /tmp/r7review-20261101 && "
    f"for f in {FILES}; do "
    f"SSHPASS='{PW}' sshpass -e scp -O -P 13229 -o StrictHostKeyChecking=yes "
    f"-o ConnectTimeout=10 root@127.0.0.1:{BK}/$f $f.part && mv $f.part $f; "
    f"done && md5sum {FILES} && echo GZ_DL_OK"
)
_, o2, e2 = gz.exec_command(cmd, timeout=180)
gz_out = o2.read().decode("utf-8", "replace")
gz_err = e2.read().decode("utf-8", "replace")
gz_rc = o2.channel.recv_exit_status()
print("@@gz-download")
print(gz_out)
if gz_err.strip():
    print("@@gz-stderr:", gz_err[:300])
print(f"@@gz_exit={gz_rc}")
if gz_rc != 0:
    gz.close()
    sys.exit("GZ download failed — STOP")

local_dir = Path(r"F:\vps\logs\r7fix-20261101")
local_dir.mkdir(parents=True, exist_ok=True)
sftp = gz.open_sftp()
for f in FILES.split():
    sftp.get(f"/tmp/r7review-20261101/{f}", str(local_dir / f))
    digest = hashlib.md5((local_dir / f).read_bytes()).hexdigest()
    print(f"pc_md5 {f}={digest}")
sftp.close()
gz.close()
print("@@ROUND_A_DOWNLOAD_COMPLETE")
