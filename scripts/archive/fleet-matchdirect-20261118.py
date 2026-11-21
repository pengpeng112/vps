# -*- coding: utf-8 -*-
"""全舰队 MATCH,DIRECT 兜底反转 + 新 GeoSite 库批量部署（2026-11-18）
范围：R1/R2/R3/R5(补fallback)/R6/R8/R9/GL（R4 已完成；R7/R10/R11 离线；雅典娜异构另行）
幂等：sed 匹配不到 MATCH,PROXY 则跳过该步；库 md5 已对则跳过下载。
每台验证：mihomo 进程/hb 200/google 走代理/antdu 直连/百度直连。
"""
import paramiko

GZ = '134.175.151.213'
PW_GZ = 'SnR@2026!Mgmt'
DEV_PW = 'SnR@2026!Mgmt'
EXPECT_MD5 = '62a14c7a'

DEVICES = [
    ('R1', 13222), ('R2', 13223), ('R3', 13227), ('R5', 13225),
    ('R6', 13226), ('R8', 13230), ('R9', 13231), ('GL', 13234),
]

gz = paramiko.SSHClient(); gz.set_missing_host_key_policy(paramiko.AutoAddPolicy())
gz.connect(GZ, port=22, username='ubuntu', password=PW_GZ, timeout=15)

def sh(port, script, timeout=180):
    cmd = ("sshpass -p '%s' ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
           "-o ConnectTimeout=8 root@127.0.0.1 -p %d \"%s\" 2>/dev/null" % (DEV_PW, port, script.replace('"', '\\"')))
    _, o, e = gz.exec_command(cmd, timeout=timeout)
    return o.read().decode() + e.read().decode()[:100]

results = []
for name, port in DEVICES:
    print('==== %s (%d) ====' % (name, port))
    try:
        out = sh(port, r'''
R=/etc/mihomo
cp $R/GeoSite.dat $R/GeoSite.dat.bak-20261118 2>/dev/null
cp $R/config.yaml $R/config.yaml.bak-matchdir-20261118
# 1) 库
MD5=$(md5sum $R/GeoSite.dat | cut -c1-8)
if [ "$MD5" != "EXPECT" ]; then
  curl -sk -m 300 https://127.0.0.1:18443/tmp-geosite.dat -o $R/GeoSite.dat.new
  N=$(md5sum $R/GeoSite.dat.new | cut -c1-8)
  [ "$N" = "EXPECT" ] && mv $R/GeoSite.dat.new $R/GeoSite.dat || { echo LIB_FAIL; }
else
  echo LIB_ALREADY
fi
ls -la $R/GeoSite.dat | awk '{print "geo_size="$5}'
# 2) MATCH 反转
if grep -q '^  - MATCH,PROXY$' $R/config.yaml; then
  sed -i 's|^  - MATCH,PROXY$|  - GEOSITE,geolocation-!cn,PROXY\n  - MATCH,DIRECT|' $R/config.yaml
  echo MATCH_PATCHED
else
  grep -q '^  - MATCH,DIRECT$' $R/config.yaml && echo MATCH_ALREADY || echo MATCH_UNKNOWN
fi
# 3) DNS fallback 国产化
sed -i 's|    - https://dns.google/dns-query|    - 223.5.5.5|; s|    - https://cloudflare-dns.com/dns-query|    - 119.29.29.29|' $R/config.yaml
grep -c 'dns.google\|cloudflare-dns' $R/config.yaml | xargs echo foreign_dns_left=
# 4) 校验+重启
/usr/bin/mihomo -d $R -f $R/config.yaml -t 2>&1 | grep -q successful && echo SYNTAX_OK || echo SYNTAX_FAIL
/etc/init.d/mihomo restart
sleep 15
echo MIHOMO=$(ps w | grep -c '[m]ihomo')
echo HB=$(curl -sk -m 5 -o /dev/null -w '%{http_code}' https://127.0.0.1:18443/api/v1/health)
echo GOOGLE=$(curl -s -o /dev/null -w '%{http_code}' -x http://127.0.0.1:7890 -m 15 https://www.google.com 2>/dev/null)
echo ANTDU=$(curl -sk -o /dev/null -w '%{http_code}' --noproxy '*' -m 8 https://sdswfsclxgajwadd.antdu.net 2>/dev/null)
echo BAIDU=$(curl -s -o /dev/null -w '%{http_code}' --noproxy '*' -m 6 https://www.baidu.com 2>/dev/null)
echo DONE
'''.replace('EXPECT', EXPECT_MD5), timeout=240)
        tail = [l for l in out.split('\n') if any(k in l for k in
                ('LIB_', 'MATCH_', 'geo_size', 'foreign_dns_left', 'SYNTAX', 'MIHOMO=', 'HB=', 'GOOGLE=', 'ANTDU=', 'BAIDU=', 'DONE', 'FAIL'))]
        for l in tail: print('  ', l.strip())
        results.append((name, out))
    except Exception as ex:
        print('  ERROR:', str(ex)[:120])
        results.append((name, 'ERROR ' + str(ex)[:100]))

print('\n==== 汇总 ====')
for name, out in results:
    ok = ('DONE' in out and 'SYNTAX_OK' in out and 'HB=200' in out
          and ('GOOGLE=200' in out) and ('ANTDU=302' in out or 'ANTDU=200' in out)
          and ('BAIDU=200' in out))
    print(name, 'PASS' if ok else 'CHECK: ' + ' | '.join(
        l.strip() for l in out.split('\n') if 'FAIL' in l or 'HB=' in l)[:120])
gz.close()
