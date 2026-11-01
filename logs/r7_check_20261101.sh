# R7 upgrade completion check - READ-ONLY - 2026-11-01
# Sent via stdin (sh -s) to avoid nested-SSH quoting corruption
echo "@@id"
cat /etc/routermgmt/identity 2>/dev/null || ls /etc/routermgmt/ 2>/dev/null | head -8
hostname
echo "@@procs"
ps w | grep '[m]ihomo' | head -2
ps w | grep '[r]outermgmt' | head -3
echo "@@T1_tunnelf"
grep '^VPS_HOST' /etc/routermgmt/tunnel.conf 2>/dev/null
grep '^VPS_PORT' /etc/routermgmt/tunnel.conf 2>/dev/null
echo "@@T2_gia"
grep -c '154.36.139.155' /etc/routermgmt/tunnel.conf 2>/dev/null
grep -c '154.36.137.54' /etc/routermgmt/tunnel.conf 2>/dev/null
grep -c '154.36.137.54' /etc/mihomo/config.yaml 2>/dev/null
echo "@@T3_do"
grep -c '^#VPS4' /etc/init.d/mgmt-tunnel 2>/dev/null
grep -c '^VPS4_HOST' /etc/init.d/mgmt-tunnel 2>/dev/null
echo "@@T4_respawn"
grep -h respawn /etc/init.d/routermgmt /etc/init.d/mgmt-tunnel 2>/dev/null
echo "@@T5_selfheal"
md5sum /etc/routermgmt/self-heal.sh 2>/dev/null
grep -c 'self-heal' /etc/crontabs/root 2>/dev/null
ls /tmp/selfheal.pause 2>/dev/null || echo no-pause
echo "@@T6_mgmturl"
grep 'MGMT_URL' /etc/routermgmt/agent.conf 2>/dev/null
echo "@@T7_fw3"
uci -q show firewall 2>/dev/null | grep -i mihomo | head -4
echo "@@T8_clock"
date -u '+%s %Y-%m-%d'
echo "@@T11_egress"
curl -s -m 12 -x http://127.0.0.1:7890 https://api.ipify.org 2>/dev/null
echo
curl -s -m 10 -o /dev/null -w 'gstatic=%{http_code}\n' -x http://127.0.0.1:7890 https://www.gstatic.com/generate_204 2>/dev/null
curl -sk -m 6 -o /dev/null -w 'mgmt18443=%{http_code}\n' https://127.0.0.1:18443/api/v1/health 2>/dev/null
echo "@@cfg"
CFG=/etc/mihomo/config.yaml
md5sum "$CFG" 2>/dev/null
grep -c '154.36.139.155' "$CFG" 2>/dev/null
echo lazy_lines:
grep -n 'lazy' "$CFG" 2>/dev/null | head -4
echo providers:
grep -c 'proxy-providers' "$CFG" 2>/dev/null
grep -c 'airport' "$CFG" 2>/dev/null
echo cf_nodes:
grep -c 'k3x9m2' "$CFG" 2>/dev/null
echo istarshine:
grep -c 'istarshine' "$CFG" 2>/dev/null
echo hy2:
grep -Ec 'hy2|hysteria' "$CFG" 2>/dev/null
echo "@@api"
curl -s -m 5 http://127.0.0.1:9090/version 2>/dev/null
echo
curl -s -m 5 http://127.0.0.1:9090/proxies/PROXY 2>/dev/null | head -c 220
echo
curl -s -m 5 http://127.0.0.1:9090/proxies/AUTO 2>/dev/null | head -c 340
echo
echo provider_names:
curl -s -m 5 http://127.0.0.1:9090/providers/proxies 2>/dev/null | grep -o '"name":"[^"]*"' | head -8
echo
echo "@@mac"
uci -q show network 2>/dev/null | grep -i macaddr | head -4
echo "@@webpw"
head -1 /etc/shadow 2>/dev/null | awk -F: '{print substr($2,1,1)" hash_len="length($2)}'
echo "@@done"
