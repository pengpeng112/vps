# R4 discriminating probe v2: switch PROXY(select) per node, full-request test, restore AUTO
i=1
while [ $i -le 5 ]; do
  g=$(curl --noproxy '*' -fsS -m 8 -G 'http://127.0.0.1:9090/proxies/US666-CN2GIA-01/delay' --data-urlencode 'url=https://www.gstatic.com/generate_204' --data-urlencode 'timeout=5000' 2>/dev/null | grep -o '"delay":[0-9]*' | cut -d: -f2)
  h=$(curl --noproxy '*' -fsS -m 8 -G 'http://127.0.0.1:9090/proxies/GIA-HY2-US-01/delay' --data-urlencode 'url=https://www.gstatic.com/generate_204' --data-urlencode 'timeout=5000' 2>/dev/null | grep -o '"delay":[0-9]*' | cut -d: -f2)
  b=$(curl --noproxy '*' -fsS -m 8 -G 'http://127.0.0.1:9090/proxies/BWG-VLESS-JPTY-01/delay' --data-urlencode 'url=https://www.gstatic.com/generate_204' --data-urlencode 'timeout=5000' 2>/dev/null | grep -o '"delay":[0-9]*' | cut -d: -f2)
  w=$(curl --noproxy '*' -fsS -m 8 -G 'http://127.0.0.1:9090/proxies/BWG-HY2-JPTY-01/delay' --data-urlencode 'url=https://www.gstatic.com/generate_204' --data-urlencode 'timeout=5000' 2>/dev/null | grep -o '"delay":[0-9]*' | cut -d: -f2)
  echo "round$i GIA-TCP=${g:-F} GIA-UDP=${h:-F} BWG-TCP=${b:-F} BWG-UDP=${w:-F}"
  i=$((i+1))
  sleep 2
done
echo '@@full_request_via_each'
for n in US666-CN2GIA-01 BWG-VLESS-JPTY-01 BWG-HY2-JPTY-01; do
  curl --noproxy '*' -fsS -m 5 -X PUT -H 'Content-Type: application/json' --data "{\"name\":\"$n\"}" http://127.0.0.1:9090/proxies/PROXY >/dev/null 2>&1
  sleep 1
  c1=$(curl -4 -sS -m 10 -x http://127.0.0.1:7890 -o /dev/null -w '%{http_code}/%{time_total}s' https://www.gstatic.com/generate_204 2>/dev/null)
  c2=$(curl -4 -sS -m 10 -x http://127.0.0.1:7890 -o /dev/null -w '%{http_code}/%{time_total}s' https://www.google.com/ 2>/dev/null)
  echo "PROXY=$n -> gstatic=$c1 google=$c2"
done
curl --noproxy '*' -fsS -m 5 -X PUT -H 'Content-Type: application/json' --data '{"name":"AUTO"}' http://127.0.0.1:9090/proxies/PROXY >/dev/null 2>&1
echo "restored PROXY -> $(curl --noproxy '*' -fsS -m 5 http://127.0.0.1:9090/proxies/PROXY 2>/dev/null | jsonfilter -e '@.now')"
echo DISCRIM2_DONE
