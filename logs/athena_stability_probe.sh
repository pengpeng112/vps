# Athena foreign-link stability probe - read-only
echo '@@time'
date '+%F %T %z'
echo '@@netprobe_status_counts_today'
grep '2026-11-13' /etc/routermgmt/logs/netprobe.log 2>/dev/null | grep 'http_proxy' | grep -o 'status=[a-z]*' | sort | uniq -c
grep '2026-11-13' /etc/routermgmt/logs/netprobe.log 2>/dev/null | grep 'http_gstatic' | grep -o 'status=[a-z]*' | sort | uniq -c
echo '@@recent_proxy_probes'
grep '2026-11-13' /etc/routermgmt/logs/netprobe.log 2>/dev/null | grep -E 'http_proxy|http_gstatic' | tail -12
echo '@@error_by_hour_today'
logread 2>/dev/null | grep 'mihomo' | grep 'Nov 13' | grep -i 'error' | awk '{print substr($4,1,2)}' | sort | uniq -c
echo '@@direct_endpoint_trials'
for target in 154.36.139.155:443 74.82.193.113:443 104.18.32.7:2053 162.159.135.42:2053; do
  ip=${target%:*}
  pt=${target#*:}
  ok=0
  fail=0
  i=1
  while [ $i -le 5 ]; do
    code=$(curl -k -sS -m 5 -o /dev/null -w '%{http_code}' "https://$ip:$pt/" 2>/dev/null)
    case "$code" in
      400|401|403|404) ok=$((ok+1)) ;;
      *) fail=$((fail+1)) ;;
    esac
    i=$((i+1))
    sleep 1
  done
  echo "$target ok=$ok fail=$fail"
done
echo '@@group_now'
for g in PROXY AUTO NODE-PICK; do
  j=$(curl --noproxy '*' -fsS -m 5 "http://127.0.0.1:9090/proxies/$g" 2>/dev/null)
  echo "$g now=$(jsonfilter -s "$j" -e '@.now' 2>/dev/null) alive=$(jsonfilter -s "$j" -e '@.alive' 2>/dev/null)"
done
echo '@@exit_5x'
i=1
while [ $i -le 5 ]; do
  out=$(curl -4 -sS -m 12 -x http://127.0.0.1:7890 https://api.ipify.org 2>/dev/null)
  echo "try$i=${out:-FAIL}"
  i=$((i+1))
  sleep 1
done
echo ATH_STABILITY_DONE
