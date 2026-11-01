"""Codex v2 Round C: comment VPS4 lines in tunnel.conf with same-script rollback."""
import os
import sys

import paramiko

PW = os.environ.get("DEVPW")

SCRIPT_C = r"""
set -eu
PAUSE=/tmp/selfheal.pause
TXN_DIR=/etc/routermgmt/state/txn
TCONF=/etc/routermgmt/tunnel.conf
BK=/root/r7fix-20261101

r7_own_pause=0
r7_restore_needed=0
NEW=""

h() { md5sum "$1" | awk '{print $1}'; }
BEFORE="$BK/tunnel.conf.before"
BEFORE_MD5=$(h "$BEFORE")
[ "$BEFORE_MD5" = "8e2b082275964f55400fbbd3348a7e2a" ] || { echo BEFORE_BACKUP_DRIFT; exit 77; }

restore_tconf() {
    cp -p "$BEFORE" "$TCONF" || return 1
    sh -n "$TCONF" || return 1
    [ "$(h "$TCONF")" = "$BEFORE_MD5" ] || return 1
    r7_restore_needed=0
}

r7_finish() {
    r7_rc=$?
    trap - 0
    trap '' HUP INT TERM
    if [ "$r7_restore_needed" -eq 1 ]; then
        if ! restore_tconf; then
            echo 'FATAL: tunnel.conf rollback verification failed'
            r7_rc=81
        fi
    fi
    [ -z "$NEW" ] || [ ! -e "$NEW" ] || rm -f "$NEW"
    if [ "$r7_own_pause" -eq 1 ]; then
        rm -f "$PAUSE" 2>/dev/null
        [ ! -e "$PAUSE" ] || r7_rc=82
    fi
    exit "$r7_rc"
}
trap r7_finish 0
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

txn_gate() { r7_pending=$(find "$TXN_DIR" -name uncommitted -print 2>/dev/null); [ -z "$r7_pending" ]; }
txn_gate || exit 75
[ ! -e "$PAUSE" ] && [ ! -L "$PAUSE" ] || exit 74
r7_own_pause=1
touch "$PAUSE" || exit 76
txn_gate || exit 75
echo GATES_OK

[ "$(h "$TCONF")" = "$BEFORE_MD5" ] || exit 78
[ "$(grep -cE '^[[:space:]]*VPS4_HOST=' "$TCONF")" -eq 1 ] || exit 78
[ "$(grep -cE '^[[:space:]]*VPS4_PORT=' "$TCONF")" -eq 1 ] || exit 78

NEW=$(mktemp /etc/routermgmt/.tunnel.conf.r7.XXXXXX)
cp -p "$TCONF" "$NEW"
sed -e '/^[[:space:]]*VPS4_HOST=/s/^\([[:space:]]*\)/\1# /' \
    -e '/^[[:space:]]*VPS4_PORT=/s/^\([[:space:]]*\)/\1# /' \
    "$TCONF" > "$NEW"

sh -n "$NEW" || exit 79
[ "$(grep -cE '^[[:space:]]*VPS4_(HOST|PORT)=' "$NEW")" -eq 0 ] || exit 79
if command -v diff >/dev/null 2>&1; then
    echo "@@candidate_diff"
    diff -u "$TCONF" "$NEW" || true
fi
sed -e 's/^\([[:space:]]*\)# \(VPS4_HOST=.*\)$/\1\2/' \
    -e 's/^\([[:space:]]*\)# \(VPS4_PORT=.*\)$/\1\2/' \
    "$NEW" > "$BK/tunnel.roundtrip"
cmp -s "$BEFORE" "$BK/tunnel.roundtrip" || { echo ROUNDTRIP_FAIL; exit 79; }
echo CANDIDATE_OK
NEW_MD5=$(h "$NEW")

r7_restore_needed=1
if mv -f "$NEW" "$TCONF" && sh -n "$TCONF" && [ "$(h "$TCONF")" = "$NEW_MD5" ]; then
    r7_restore_needed=0
else
    echo COMMIT_VERIFY_FAIL
    exit 80
fi
sync

echo "@@after"
grep -nE 'VPS4_(HOST|PORT)=' "$TCONF"
[ "$(grep -cE '^#[[:space:]]*VPS4_HOST=' "$TCONF")" -eq 1 ] || exit 81
[ "$(grep -cE '^#[[:space:]]*VPS4_PORT=' "$TCONF")" -eq 1 ] || exit 81
[ "$(grep -cE '^VPS_HOST=134.175.151.213' "$TCONF")" -eq 1 ] || exit 81
[ "$(grep -cE '^VPS5_HOST=154.36.139.155' "$TCONF")" -eq 1 ] || exit 81

H=$(curl --noproxy '*' -sk -m 6 -o /dev/null -w '%{http_code}' https://127.0.0.1:18443/api/v1/health)
echo "mgmt_health=$H"
[ "$H" = "200" ] || exit 63

echo ROUND_C_OK
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
stdin.channel.sendall(SCRIPT_C.encode())
stdin.channel.shutdown_write()
out = stdout.read().decode("utf-8", "replace")
rc = stdout.channel.recv_exit_status()
err = stderr.read().decode("utf-8", "replace")
print("@@roundC-device")
print(out)
print(f"@@device_exit={rc}")
if err.strip():
    print("@@stderr:", err[:300])
r7.close()
gz.close()

if rc != 0:
    sys.exit(f"Round C failed rc={rc} — investigate before proceeding")
print("@@ROUND_C_COMPLETE")
