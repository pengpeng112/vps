# R2 rate-limit v2 preflight - READ-ONLY
set -u
echo '@@identity'
awk -F= '$1=="DEVICE_ID"{print $2}' /etc/routermgmt/agent.conf
date '+%F %T %z'
echo "uptime_s=$(cut -d' ' -f1 /proc/uptime)"
echo '@@management'
curl --noproxy '*' -sk -m 5 -o /dev/null -w 'health=%{http_code}\n' https://127.0.0.1:18443/api/v1/health
echo '@@qdisc'
command -v tc || true
tc -V 2>/dev/null || true
ip -4 route show default
WAN=$(ip -4 route show default | awk 'NR==1{print $5}')
echo "wan=$WAN"
tc qdisc show dev "$WAN" 2>/dev/null || true
echo '@@ifb_capability'
[ -d /sys/module/ifb ] && echo IFB_MODULE_PRESENT || echo IFB_MODULE_NOT_LOADED
ip -details link show 2>/dev/null | grep -i ifb || echo NO_IFB_LINK
echo '@@existing_v2_objects'
find /etc/routermgmt -maxdepth 3 -type f \( -iname '*ratelimit*' -o -iname '*rate_limit*' -o -iname '*meter*' -o -iname '*qos*' \) -print 2>/dev/null || true
echo '@@proxy_state'
for g in PROXY AUTO NODE-PICK US-MEDIA; do
  j=$(curl --noproxy '*' -fsS -m 5 "http://127.0.0.1:9090/proxies/$g" 2>/dev/null)
  echo "$g now=$(jsonfilter -s "$j" -e '@.now' 2>/dev/null)"
done
echo '@@config_hashes'
sha256sum /etc/config/dhcp /etc/config/wireless /etc/mihomo/config.yaml /etc/routermgmt/tunnel.conf /etc/routermgmt/self-heal.sh 2>/dev/null
echo R2_RATE_PRECHECK_DONE
