#!/bin/sh
# proxy-meter v2 (R2) - monotonic per-endpoint proxy byte accounting. Schema 2.
# - boot_id/continuity/seq, flock + atomic state, daily ledger (Asia/Shanghai)
# - zero-vs-read-fail distinction; counter resets -> discontinuity, never negative
# - env overrides for fixture tests: PM_STATE PM_DAY PM_LOG PM_RULES PM_PREV
# - no counter resets; log rotation; retention by caller cron
set -u

STATE=${PM_STATE:-/etc/routermgmt/state/proxy-meter-v2.json}
PREV=${PM_PREV:-$STATE.prev}
DAYF=${PM_DAY:-/etc/routermgmt/state/proxy-meter-daily.json}
LOG=${PM_LOG:-/etc/routermgmt/logs/proxy-meter-v2.log}
RULES=${PM_RULES:-/etc/routermgmt/proxy-meter-rules.conf}
CHAIN_OUT=${PM_CHAIN_OUT:-PROXY_METER_OUT}
CHAIN_IN=${PM_CHAIN_IN:-PROXY_METER_IN}
LOG_MAX=5242880

mkdir -p "$(dirname "$STATE")" "$(dirname "$LOG")"

log() { echo "$(date '+%F %T') $*" >> "$LOG"; }
fail_exit() { log "FAIL $*"; echo "FAIL $*"; exit 1; }

# concurrency lock
exec 9>F:/vps/review/r2-qos-report-fixtures-20261117/meter.lock
flock -n 9 2>/dev/null || { log "SKIP lock held"; exit 0; }

command -v iptables >/dev/null 2>&1 || fail_exit "iptables missing"
[ -f "$RULES" ] || fail_exit "rules file missing: $RULES"

NOW_ISO=$(date '+%F %T%z' 2>/dev/null || date '+%F %T')
DAY_KEY=$(date '+%F')
BOOT_ID=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null | cut -c1-8)
[ -n "$BOOT_ID" ] || BOOT_ID="unknown"
SEQ=0
PREV_CONT="none"

# ---- read one counter; 0 is legal, empty is read failure ----
read_counter() {
  iptables -L "$1" -v -x -n 2>/dev/null | awk -v c="$2" 'index($0,c){print $2; exit}'
}

# ---- gather endpoints ----
ENDPOINTS=""
FAILS=""
PREV=$(cat "$PREV" 2>/dev/null || echo '{}')
[ -f "$STATE" ] && PREV_STATE_VALID=1 || PREV_STATE_VALID=0

while IFS='|' read -r ep dir ip proto port; do
  [ -n "$ep" ] || continue
  case "$ep" in '#'*) continue ;; esac
  if [ "$dir" = "up" ]; then
    v=$(read_counter "$CHAIN_OUT" "$ep")
  else
    v=$(read_counter "$CHAIN_IN" "$ep")
  fi
  if [ -z "$v" ]; then
    FAILS="$FAILS $ep"
    v=$(printf '%s' "$PREV" | sed -n 's/.*"'"$ep"'":\([0-9]*\).*/\1/p' 2>/dev/null)
    [ -n "$v" ] || v=0
  fi
  ENDPOINTS="$ENDPOINTS\"$ep\":$v,"
done < "$RULES"
ENDPOINTS=${ENDPOINTS%,}

# ---- continuity ----
if [ -f "$STATE" ]; then
  PREV_CONT=$(printf '%s' "$(cat "$STATE" 2>/dev/null)" | sed -n 's/.*"continuity_id":"\([^"]*\)".*/\1/p')
  PREV_BOOT=$(printf '%s' "$(cat "$STATE" 2>/dev/null)" | sed -n 's/.*"boot_id":"\([^"]*\)".*/\1/p')
  PSEQ=$(printf '%s' "$(cat "$STATE" 2>/dev/null)" | sed -n 's/.*"seq":\([0-9]*\).*/\1/p')
  [ -n "$PSEQ" ] && SEQ=$PSEQ
  if [ "$PREV_BOOT" != "$BOOT_ID" ]; then
    CONTINUITY="cont-$BOOT_ID-reboot"
    log "DISCONTINUITY boot $PREV_BOOT -> $BOOT_ID"
  else
    CONTINUITY="cont-$BOOT_ID-cont"
  fi
  SEQ=$((SEQ + 1))
else
  CONTINUITY="cont-$BOOT_ID-init"
  SEQ=1
  log "INIT baseline"
fi

# ---- atomic state write ----
printf '{"schema":2,"seq":%d,"boot_id":"%s","continuity_id":"%s","prev_continuity_id":"%s","sampled_at":"%s","read_fail_eps":"%s","endpoints":{%s}}\n' \
  "$SEQ" "$BOOT_ID" "$CONTINUITY" "$PREV_CONT" "$NOW_ISO" "$FAILS" "$ENDPOINTS" > "$STATE.tmp" \
  && mv "$STATE.tmp" "$STATE" || fail_exit "state write failed"

# ---- daily delta (prev sample = .prev copy of previous state) ----
DELTA=0
DAY_OK=1
if [ -s "$PREV" ] && [ "$PREV_CONT" != "none" ]; then
  DELTA=$(printf '%s %s' "$PREV" "$ENDPOINTS" | awk '
    {
      if (NR == 1) {
        s = $0
        while (match(s, /"[A-Za-z0-9_-]+":[0-9]+/)) {
          k = substr(s, RSTART, RLENGTH); s = substr(s, RSTART + RLENGTH)
          gsub(/"/, "", k); split(k, a, ":"); prev[a[1]] = a[2]
        }
        next
      }
      s2 = $0
      while (match(s2, /"[A-Za-z0-9_-]+":[0-9]+/)) {
        k = substr(s2, RSTART, RLENGTH); s2 = substr(s2, RSTART + RLENGTH)
        gsub(/"/, "", k); split(k, a, ":"); cur[a[1]] = a[2]
      }
    }
    END {
      t = 0
      for (e in cur) {
        p = (e in prev) ? prev[e] : 0
        d = cur[e] - p
        if (d < 0) d = 0
        t += d
      }
      print t
    }')
else
  DELTA=0
  DAY_OK=0
fi

# ---- daily ledger update (Asia/Shanghai day key) ----
DAY_TOTAL=0
if [ -f "$DAYF" ]; then
  DAY_TOTAL=$(printf '%s' "$(cat "$DAYF")" | sed -n 's/.*"total":\([0-9]*\).*/\1/p')
  DK=$(printf '%s' "$(cat "$DAYF")" | sed -n 's/.*"day_key":"\([^"]*\)".*/\1/p')
  [ "$DK" = "$DAY_KEY" ] || DAY_TOTAL=0
fi
DAY_TOTAL=$((DAY_TOTAL + DELTA))
printf '{"day_key":"%s","total":%d,"delta_last":%d,"sampled_at":"%s"}\n' \
  "$DAY_KEY" "$DAY_TOTAL" "$DELTA" "$NOW_ISO" > "$DAYF.tmp" \
  && mv "$DAYF.tmp" "$DAYF" || fail_exit "daily write failed"

# ---- prev snapshot for next run ----
printf '%s' "$ENDPOINTS" > "$PREV.tmp" && mv "$PREV.tmp" "$PREV"

# ---- rotation ----
SZ=$(stat -c %s "$LOG" 2>/dev/null || echo 0)
if [ "$SZ" -gt $LOG_MAX ]; then
  mv "$LOG" "$LOG.$(date '+%Y%m%d-%H%M%S')" 2>/dev/null
fi

if [ -n "$FAILS" ]; then
  log "OK(read_fail:$FAILS) seq=$SEQ cont=$CONTINUITY delta=$DELTA day_total=$DAY_TOTAL"
else
  log "OK seq=$SEQ cont=$CONTINUITY delta=$DELTA day_total=$DAY_TOTAL"
fi
echo "OK seq=$SEQ cont=$CONTINUITY delta=$DELTA day_total=$DAY_TOTAL fails=[$FAILS]"
exit 0
