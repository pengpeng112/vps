"""Codex v2 Round A: device backup + cache snapshot, GZ relay download, PC bbolt verify."""
import os
import sys
from pathlib import Path

import paramiko

PW = os.environ.get("DEVPW")
if not PW:
    sys.exit("DEVPW not set")

BK = "/root/r7fix-20261101"

SCRIPT_A = r"""
set -eu
for r7_cmd in sh find touch rm cp mv md5sum grep curl jsonfilter ubus sed cmp mktemp awk; do
    command -v "$r7_cmd" >/dev/null 2>&1 || { echo "TOOL_MISSING $r7_cmd"; exit 70; }
done
echo TOOLS_OK

TCONF=/etc/routermgmt/tunnel.conf
TXN_DIR=/etc/routermgmt/state/txn
CFG=/etc/mihomo/config.yaml
CACHE=/etc/mihomo/cache.db

echo "@@companion"
ls -l /etc/mihomo/cache.db* 2>/dev/null

r7_pending=$(find "$TXN_DIR" -name uncommitted -print 2>/dev/null)
[ -z "$r7_pending" ] || { echo "TXN_PENDING: $r7_pending"; exit 75; }
echo TXN_CLEAR

BK=/root/r7fix-20261101
mkdir -p "$BK"

cp -p "$CFG" "$BK/mihomo.config.before"
cp -p /etc/routermgmt/self-heal.sh "$BK/selfheal.before"
cp -p "$TCONF" "$BK/tunnel.conf.before"
md5sum "$BK"/mihomo.config.before "$BK"/selfheal.before "$BK"/tunnel.conf.before

echo "@@vps4_before"
grep -nE '^[[:space:]]*VPS4_(HOST|PORT)=' "$TCONF"

echo "@@api_before"
curl --noproxy '*' -fsS -m 8 http://127.0.0.1:9090/version > "$BK/version.json"
curl --noproxy '*' -fsS -m 8 http://127.0.0.1:9090/proxies/PROXY > "$BK/proxy.before.json"
curl --noproxy '*' -fsS -m 8 http://127.0.0.1:9090/proxies/AUTO > "$BK/auto.before.json"
echo "type=$(jsonfilter -i "$BK/proxy.before.json" -e '@.type')"
echo "PROXY.now=$(jsonfilter -i "$BK/proxy.before.json" -e '@.now')"
echo "AUTO.now=$(jsonfilter -i "$BK/auto.before.json" -e '@.now')"

echo "@@cache_snapshot"
h() { md5sum "$1" | awk '{print $1}'; }
H0=$(h "$CACHE")
cp -p "$CACHE" "$BK/cache.before.db"
HC=$(h "$BK/cache.before.db")
H1=$(h "$CACHE")
echo "cache_hashes H0=$H0 HC=$HC H1=$H1"
[ "$H0" = "$HC" ] && [ "$H0" = "$H1" ] || { echo CACHE_SNAPSHOT_UNSTABLE; exit 71; }

echo "@@egress_before"
curl -4 --noproxy '' --proxy http://127.0.0.1:7890 -fsS --connect-timeout 5 --max-time 20 https://api.ipify.org; echo
echo ROUND_A_OK
"""

gz = paramiko.SSHClient()
gz.set_missing_host_key_policy(paramiko.AutoAddPolicy())
gz.connect("134.175.151.213", port=28722, username="ubuntu", password=PW,
           timeout=20, look_for_keys=False, allow_agent=False)

chan = gz.get_transport().open_channel("direct-tcpip", ("127.0.0.1", 13229), ("127.0.0.1", 0))
r7 = paramiko.SSHClient()
r7.set_missing_host_key_policy(paramiko.AutoAddPolicy())
r7.connect("127.0.0.1", port=13229, username="root", password=PW, sock=chan,
           timeout=20, look_for_keys=False, allow_agent=False)

stdin, stdout, stderr = r7.exec_command("sh -s", timeout=120)
stdin.channel.sendall(SCRIPT_A.encode())
stdin.channel.shutdown_write()
out = stdout.read().decode("utf-8", "replace")
rc = stdout.channel.recv_exit_status()
err = stderr.read().decode("utf-8", "replace")
print("@@roundA-device")
print(out)
print(f"@@device_exit={rc}")
if err.strip():
    print("@@stderr:", err[:300])
r7.close()

if rc != 0:
    gz.close()
    sys.exit(f"Round A device script failed rc={rc} — STOP per Codex rule")

# GZ-side scp download (primary channel per Codex D7 ruling)
FILES = "version.json proxy.before.json auto.before.json tunnel.conf.before mihomo.config.before selfheal.before cache.before.db"
_, o2, e2 = gz.exec_command(
    f'mkdir -p /tmp/r7review-20261101 && cd /tmp/r7review-20261101 && '
    f'for f in {FILES}; do '
    f'  sshpass -e scp -O -P 13229 -o StrictHostKeyChecking=yes -o ConnectTimeout=10 '
    f'    root@127.0.0.1:{BK}/$f $f.part && mv $f.part $f; '
    f'done && md5sum {FILES} && echo GZ_DL_OK',
    timeout=120,
)
o2.channel.sendall(PW.encode())
o2.channel.shutdown_write()
gz_out = o2.read().decode("utf-8", "replace")
gz_err = e2.read().decode("utf-8", "replace")
gz_rc = o2.channel.recv_exit_status()
print("@@gz-download")
print(gz_out)
if gz_err.strip():
    print("@@gz-stderr:", gz_err[:300])
print(f"@@gz_exit={gz_rc}")

# PC-side pull via SFTP
local_dir = Path(r"F:\vps\logs\r7fix-20261101")
local_dir.mkdir(parents=True, exist_ok=True)
sftp = gz.open_sftp()
import hashlib
for f in FILES.split():
    sftp.get(f"/tmp/r7review-20261101/{f}", str(local_dir / f))
    digest = hashlib.md5((local_dir / f).read_bytes()).hexdigest()
    print(f"pc_md5 {f}={digest}")
sftp.close()
gz.close()
print("@@ROUND_A_COMPLETE")
