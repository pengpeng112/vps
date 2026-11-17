#!/bin/sh
# proxy-meter v1 (R2 pilot) - per-endpoint proxy traffic accounting on the router.
# Counts router OUTPUT (to endpoints) and INPUT (from endpoints) bytes for proxy
# endpoint tuples only. Domestic/SSH/WG never match. Idempotent rules; survives
# fw3 reload by re-ensuring each run. Log: deltas every run (cron */5). No resets.
set -u
STATE=/etc/routermgmt/state/proxy-meter.state
LOG=/etc/routermgmt/logs/proxy-meter.log
mkdir -p /etc/routermgmt/state /etc/routermgmt/logs

# endpoint rules: name|direction|ip|proto|port
RULES='
pm_bwg_vless_up|OUT|74.82.193.113|tcp|443
pm_bwg_vless_down|IN|74.82.193.113|tcp|443
pm_bwg_hy2_up|OUT|74.82.193.113|udp|34567
pm_bwg_hy2_down|IN|74.82.193.113|udp|34567
pm_gia_vless_up|OUT|154.36.139.155|tcp|443
pm_gia_vless_down|IN|154.36.139.155|tcp|443
pm_gia_hy2_up|OUT|154.36.139.155|udp|34568
pm_gia_hy2_down|IN|154.36.139.155|udp|34568
pm_cf1_up|OUT|104.18.32.7|tcp|2053
pm_cf1_down|IN|104.18.32.7|tcp|2053
pm_cf2_up|OUT|162.159.135.42|tcp|2053
pm_cf2_down|IN|162.159.135.42|tcp|2053
'

ensure_rules() {
  iptables -N PROXY_METER_OUT 2>/dev/null || true
  iptables -N PROXY_METER_IN 2>/dev/null || true
  iptables -C OUTPUT -j PROXY_METER_OUT 2>/dev/null || iptables -I OUTPUT 1 -j PROXY_METER_OUT
  iptables -C INPUT -j PROXY_METER_IN 2>/dev/null || iptables -I INPUT 1 -j PROXY_METER_IN
  printf '%s\n' "$RULES" | while IFS='|' read -r nm dir ip proto port; do
    [ -n "$nm" ] || continue
    if [ "$dir" = "OUT" ]; then
      iptables -C PROXY_METER_OUT -d "$ip" -p "$proto" --dport "$port" -m comment --comment "$nm" 2>/dev/null || \
      iptables -A PROXY_METER_OUT -d "$ip" -p "$proto" --dport "$port" -m comment --comment "$nm"
    else
      iptables -C PROXY_METER_IN -s "$ip" -p "$proto" --sport "$port" -m comment --comment "$nm" 2>/dev/null || \
      iptables -A PROXY_METER_IN -s "$ip" -p "$proto" --sport "$port" -m comment --comment "$nm"
    fi
  done
}

read_counters() {
  printf '%s\n' "$RULES" | while IFS='|' read -r nm dir ip proto port; do
    [ -n "$nm" ] || continue
    if [ "$dir" = "OUT" ]; then
      v=$(iptables -L PROXY_METER_OUT -v -x -n 2>/dev/null | awk -v c="$nm" '$0 ~ c {print $2; exit}')
    else
      v=$(iptables -L PROXY_METER_IN -v -x -n 2>/dev/null | awk -v c="$nm" '$0 ~ c {print $2; exit}')
    fi
    echo "$nm ${v:-0}"
  done
}

ensure_rules

NOW=$(date '+%s')
NOW_H=$(date '+%F %T')
NEW=$(read_counters | sort)
if [ -f "$STATE" ]; then
  OLD=$(sort "$STATE")
  printf '%s\n' "$NEW" | while IFS=' ' read -r nm cur; do
    prev=$(printf '%s\n' "$OLD" | awk -v n="$nm" '$1==n {print $2; exit}')
    [ -n "$prev" ] || prev=0
    d=$((cur - prev))
    [ "$d" -lt 0 ] && d=0
    if [ "$d" -gt 0 ]; then
      echo "$NOW_H $nm delta=${d}B total=${cur}B" >> "$LOG"
    fi
  done
  echo "run ts=$NOW" >> "$LOG"
else
  printf '%s\n' "$NEW" > "$STATE"
  echo "$NOW_H baseline created" >> "$LOG"
fi
printf '%s\n' "$NEW" > "$STATE.tmp" && mv "$STATE.tmp" "$STATE"

# log rotation 5MB
SZ=$(stat -c %s "$LOG" 2>/dev/null || echo 0)
if [ "$SZ" -gt 5242880 ]; then
  mv "$LOG" "$LOG.$(date '+%Y%m%d-%H%M%S')" 2>/dev/null
fi
exit 0
