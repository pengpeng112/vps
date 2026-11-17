# Athena active per-node stability probe - read-only
for n in US666-CN2GIA-01 BWG-HY2-JPTY-01 GIA-HY2-US-01 BWG-VLESS-JPTY-01 CF2-CDN-GIA-01; do
  i=1
  while [ $i -le 3 ]; do
    r=$(curl --noproxy '*' -fsS -m 10 -G "http://127.0.0.1:9090/proxies/$n/delay" --data-urlencode 'url=https://www.gstatic.com/generate_204' --data-urlencode 'timeout=6000' 2>/dev/null | grep -o '"delay":[0-9]*' | cut -d: -f2)
    echo "$n try$i=${r:-FAIL}ms"
    i=$((i+1))
    sleep 1
  done
done
echo '@@recent_error_types'
logread 2>/dev/null | grep 'mihomo' | grep -i 'error' | tail -400 | grep -oE 'context deadline exceeded|unexpected eof while reading|connection reset by peer|i/o timeout|dns resolve failed|connection refused' | sort | uniq -c | sort -rn
echo NODE_PROBE_DONE
