#!/bin/sh
# 设备端执行：MATCH,DIRECT 反转 + 新 GeoSite 库 + DNS fallback 国产化（幂等）
R=/etc/mihomo
cp $R/GeoSite.dat $R/GeoSite.dat.bak-20261118 2>/dev/null
cp $R/config.yaml $R/config.yaml.bak-matchdir-20261118

MD5=$(md5sum $R/GeoSite.dat | cut -c1-8)
if [ "$MD5" != "62a14c7a" ]; then
  curl -sk -m 300 https://127.0.0.1:18443/tmp-geosite.dat -o $R/GeoSite.dat.new
  N=$(md5sum $R/GeoSite.dat.new | cut -c1-8)
  if [ "$N" = "62a14c7a" ]; then
    mv $R/GeoSite.dat.new $R/GeoSite.dat
    echo LIB_UPDATED
  else
    echo LIB_FAIL
  fi
else
  echo LIB_ALREADY
fi
ls -la $R/GeoSite.dat | awk '{print "geo_size="$5}'

if grep -q '^  - MATCH,PROXY$' $R/config.yaml; then
  sed -i 's|^  - MATCH,PROXY$|  - GEOSITE,geolocation-!cn,PROXY\n  - MATCH,DIRECT|' $R/config.yaml
  echo MATCH_PATCHED
else
  grep -q '^  - MATCH,DIRECT$' $R/config.yaml && echo MATCH_ALREADY || echo MATCH_UNKNOWN
fi

sed -i 's|    - https://dns.google/dns-query|    - 223.5.5.5|; s|    - https://cloudflare-dns.com/dns-query|    - 119.29.29.29|' $R/config.yaml
echo foreign_dns_left=$(grep -c 'dns.google\|cloudflare-dns' $R/config.yaml)

if /usr/bin/mihomo -d $R -f $R/config.yaml -t 2>&1 | grep -q successful; then
  echo SYNTAX_OK
else
  echo SYNTAX_FAIL
  exit 1
fi
/etc/init.d/mihomo restart
sleep 15
echo MIHOMO=$(ps w | grep -c '[m]ihomo')
echo HB=$(curl -sk -m 5 -o /dev/null -w '%{http_code}' https://127.0.0.1:18443/api/v1/health)
echo GOOGLE=$(curl -s -o /dev/null -w '%{http_code}' -x http://127.0.0.1:7890 -m 15 https://www.google.com 2>/dev/null)
echo ANTDU=$(curl -sk -o /dev/null -w '%{http_code}' --noproxy '*' -m 8 https://sdswfsclxgajwadd.antdu.net 2>/dev/null)
echo BAIDU=$(curl -s -o /dev/null -w '%{http_code}' --noproxy '*' -m 6 https://www.baidu.com 2>/dev/null)
echo DONE
