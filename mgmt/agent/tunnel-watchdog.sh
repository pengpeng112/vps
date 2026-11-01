#!/bin/sh
# All-endpoint recovery; never signals reverse SSH (rssh/rssh2), WAN, or sshd.
# Discovery starts this before its failure return. Duplicate calls are rate-limited.
STATE=${RM_WD_STATE_DIR:-/tmp}
LOG=${RM_WD_LOG:-/etc/routermgmt/logs/tunnelwd.log}
LOCK="$STATE/tunnelwd.lock"
CNT="$STATE/tunnelwd.fails"
SAMPLE="$STATE/tunnelwd.sample"
REPAIR="$STATE/tunnelwd.repair"
mkdir -p "$STATE" "$(dirname "$LOG")"
if ! mkdir "$LOCK" 2>/dev/null; then
    _pid=$(cat "$LOCK/pid" 2>/dev/null)
    case "$_pid" in ''|*[!0-9]*) exit 0 ;; esac
    kill -0 "$_pid" 2>/dev/null && exit 0
    rm -f "$LOCK/pid"; rmdir "$LOCK" 2>/dev/null || exit 0
    mkdir "$LOCK" 2>/dev/null || exit 0
fi
echo $$ > "$LOCK/pid"
trap 'rm -f "$LOCK/pid"; rmdir "$LOCK" 2>/dev/null' EXIT
trap 'exit 0' HUP INT TERM
number() { case "$1" in ''|*[!0-9]*) echo 0 ;; *) echo "$1" ;; esac; }
NOW=$(date +%s)
LAST=$(number "$(cat "$SAMPLE" 2>/dev/null)")
[ "$NOW" -ge "$LAST" ] && [ $((NOW - LAST)) -lt 60 ] && exit 0
echo "$NOW" > "$SAMPLE"
for PORT in 18443 18444 18445 18446 18447; do
    if curl -fsk -m 4 -o /dev/null "https://127.0.0.1:$PORT/api/v1/health" 2>/dev/null; then
        rm -f "$CNT"
        exit 0
    fi
done
N=$(number "$(cat "$CNT" 2>/dev/null)")
N=$((N + 1)); echo "$N" > "$CNT"
echo "$(date '+%F %T') all management health checks failed sample=$N" >> "$LOG"
if [ "$(wc -l < "$LOG")" -gt 500 ]; then
    tail -n 400 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi
[ "$N" -ge 3 ] || exit 0
LAST=$(number "$(cat "$REPAIR" 2>/dev/null)")
[ "$NOW" -ge "$LAST" ] && [ $((NOW - LAST)) -lt 600 ] && exit 0
echo "$NOW" > "$REPAIR"
# Signal only -L instances. Unsupported ubus leaves connections untouched;
# never fall back to restarting the entire service.
for INSTANCE in mgmt mgmt2 mgmt3 mgmt4 mgmt5; do
    ubus call service signal "{\"name\":\"mgmt-tunnel\",\"instance\":\"$INSTANCE\",\"signal\":15}" >/dev/null 2>&1 || true
done
logger -t tunnel-wd "rechecked all failed management legs; reverse SSH preserved"
echo "$(date '+%F %T') signaled management instances only; rssh/rssh2 untouched" >> "$LOG"
exit 0
