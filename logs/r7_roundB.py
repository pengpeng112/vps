"""Codex v2 Round B: PROXY -> AUTO via 9090 API, full guard framework."""
import os
import sys

import paramiko

PW = os.environ.get("DEVPW")
BK = "/root/r7fix-20261101"

SCRIPT_B = r"""
set -eu
PAUSE=/tmp/selfheal.pause
TXN_DIR=/etc/routermgmt/state/txn
CACHE=/etc/mihomo/cache.db
BK=/root/r7fix-20261101
TARGET=AUTO

r7_own_pause=0
r7_finish() {
    r7_rc=$?
    trap - 0
    trap '' HUP INT TERM
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

txn_gate() {
    r7_pending=$(find "$TXN_DIR" -name uncommitted -print 2>/dev/null)
    [ -z "$r7_pending" ]
}
txn_gate || exit 75
[ ! -e "$PAUSE" ] && [ ! -L "$PAUSE" ] || exit 74
r7_own_pause=1
touch "$PAUSE" || exit 76
txn_gate || exit 75
echo GATES_OK

R7_MPID=$(pidof mihomo)
[ -n "$R7_MPID" ] || exit 72
R7_BOOT=$(cat /proc/sys/kernel/random/boot_id)

R7_OLD=$(jsonfilter -i "$BK/proxy.before.json" -e '@.now')
[ -n "$R7_OLD" ] || exit 73
echo "old=$R7_OLD target=$TARGET"

R7_CODE=$(curl --noproxy '*' -sS -m 15 -X PUT -H 'Content-Type: application/json' \
    --data '{"name":"AUTO"}' \
    -o "$BK/proxy.put.body" -w '%{http_code}' \
    http://127.0.0.1:9090/proxies/PROXY)
echo "put_code=$R7_CODE"
[ "$R7_CODE" = "204" ] || { echo PUT_REJECTED; exit 60; }

curl --noproxy '*' -fsS -m 8 http://127.0.0.1:9090/proxies/PROXY > "$BK/proxy.after.json"
curl --noproxy '*' -fsS -m 8 http://127.0.0.1:9090/proxies/AUTO > "$BK/auto.after.json"
P_NOW=$(jsonfilter -i "$BK/proxy.after.json" -e '@.now')
A_NOW=$(jsonfilter -i "$BK/auto.after.json" -e '@.now')
echo "PROXY.now=$P_NOW AUTO.now=$A_NOW"
[ "$P_NOW" = "$TARGET" ] || exit 61
[ "$A_NOW" = "NODE-PICK" ] || exit 61

[ "$(pidof mihomo)" = "$R7_MPID" ] || exit 62
[ "$(cat /proc/sys/kernel/random/boot_id)" = "$R7_BOOT" ] || exit 62
H=$(curl --noproxy '*' -sk -m 6 -o /dev/null -w '%{http_code}' https://127.0.0.1:18443/api/v1/health)
echo "mgmt_health=$H"
[ "$H" = "200" ] || exit 63

h() { md5sum "$1" | awk '{print $1}'; }
S=UNSTABLE
i=1
while [ $i -le 3 ]; do
    H0=$(h "$CACHE")
    cp -p "$CACHE" "$BK/cache.after.db"
    HC=$(h "$BK/cache.after.db")
    H1=$(h "$CACHE")
    echo "cache_try$i H0=$H0 HC=$HC H1=$H1"
    if [ "$H0" = "$HC" ] && [ "$H0" = "$H1" ]; then S=STABLE; break; fi
    sleep 2
    i=$((i+1))
done
echo "cache_snapshot=$S"

EG_OK=0
for t in 1 2; do
    IP=$(curl -4 --noproxy '' --proxy http://127.0.0.1:7890 -fsS --connect-timeout 5 --max-time 20 https://api.ipify.org 2>/dev/null || true)
    echo "egress_try$t=$IP"
    [ "$IP" = "154.36.139.155" ] && { EG_OK=1; break; }
    sleep 5
done
if [ "$EG_OK" != "1" ]; then
    echo EGRESS_FAILED_ROLLBACK
    RB=$(printf '{"name":"%s"}' "$R7_OLD")
    R7_RB=$(curl --noproxy '*' -sS -m 15 -X PUT -H 'Content-Type: application/json' \
        --data "$RB" -o "$BK/proxy.rollback.body" -w '%{http_code}' \
        http://127.0.0.1:9090/proxies/PROXY)
    echo "rollback_code=$R7_RB"
    curl --noproxy '*' -fsS -m 8 http://127.0.0.1:9090/proxies/PROXY > "$BK/proxy.rollback.json" 2>/dev/null || true
    echo "PROXY.after_rollback=$(jsonfilter -i "$BK/proxy.rollback.json" -e '@.now' 2>/dev/null)"
    exit 65
fi

echo ROUND_B_OK
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

stdin, stdout, stderr = r7.exec_command("sh -s", timeout=180)
stdin.channel.sendall(SCRIPT_B.encode())
stdin.channel.shutdown_write()
out = stdout.read().decode("utf-8", "replace")
rc = stdout.channel.recv_exit_status()
err = stderr.read().decode("utf-8", "replace")
print("@@roundB-device")
print(out)
print(f"@@device_exit={rc}")
if err.strip():
    print("@@stderr:", err[:300])
r7.close()
gz.close()

if rc != 0:
    sys.exit(f"Round B failed rc={rc} — STOP, no Round C")
print("@@ROUND_B_COMPLETE")
