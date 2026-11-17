#!/bin/sh
# gia-meter-sample: reads gia_usage_meter counters, appends JSON line (no reset).
# Every minute via cron. Log rotation at 20MB. Disk-safe: tmp+atomic append.
set -u
DIR=/var/lib/gia-meter
LOG=$DIR/samples.jsonl
mkdir -p "$DIR"
TS=$(date '+%F %T %z')
BOOT=$(cut -d' ' -f3 /proc/uptime >/dev/null 2>&1; cat /proc/sys/kernel/random/boot_id | cut -c1-8)
OUT=$(nft -j list table inet gia_usage_meter 2>/dev/null) || { echo "{\"ts\":\"$TS\",\"error\":\"nft_fail\"}" >> "$LOG"; exit 0; }
LINE=$(printf '%s' "$OUT" | tr -d '\n' | sed 's/[[:space:]]\+/ /g')
printf '{"ts":"%s","boot":"%s","counters":%s}\n' "$TS" "$BOOT" "$LINE" >> "$LOG.tmp"
cat "$LOG.tmp" >> "$LOG" 2>/dev/null || cat "$LOG.tmp" > "$LOG"
rm -f "$LOG.tmp"
SZ=$(stat -c %s "$LOG" 2>/dev/null || echo 0)
if [ "$SZ" -gt 20971520 ]; then
  mv "$LOG" "$LOG.$(date '+%Y%m%d-%H%M%S')" 2>/dev/null
fi
exit 0
