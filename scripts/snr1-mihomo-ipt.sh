#!/bin/sh
# SN-R1 transparent proxy via iptables. Only touches br-lan.
# If mihomo is down, remove redirects so LAN DNS/TCP are not black-holed.
LAN=192.168.132.0/24
# 节点 VPS 公网 IP 永不重定向；新增/更换节点必须同步此清单
# 2026-09-27 M03：补腾讯 43.134.46.44（与雅典娜 mihomo-fw.sh 清单保持一致）
NODE_IPS="159.223.36.119 43.134.46.44"

mihomo_up() {
    wget -T 2 -q -O /dev/null http://127.0.0.1:9090/version 2>/dev/null
}

clear_rules() {
    iptables -t nat -D PREROUTING -i br-lan -j MIHOMO_DNS 2>/dev/null
    iptables -D FORWARD -i br-lan -p udp --dport 443 -m comment --comment mihomo-quic-drop -j DROP 2>/dev/null
    iptables -t nat -D PREROUTING -i br-lan -j MIHOMO 2>/dev/null
    iptables -t nat -F MIHOMO_DNS 2>/dev/null
    iptables -t nat -X MIHOMO_DNS 2>/dev/null
    iptables -t nat -F MIHOMO 2>/dev/null
    iptables -t nat -X MIHOMO 2>/dev/null
}

case "$1" in
stop)
    clear_rules
    uci -q delete dhcp.@dnsmasq[0].noresolv
    uci -q del_list dhcp.@dnsmasq[0].server='127.0.0.1#1053'
    uci commit dhcp
    /etc/init.d/dnsmasq restart
    exit 0
    ;;
esac

if ! mihomo_up; then
    clear_rules
    logger -t mihomo-ipt "core down, redirects removed"
    exit 0
fi

if [ -w /sys/kernel/debug/hnat/hook_toggle ]; then
    echo 0 > /sys/kernel/debug/hnat/hook_toggle
fi

clear_rules
iptables -t nat -N MIHOMO
iptables -t nat -A MIHOMO -d 0.0.0.0/8 -j RETURN
iptables -t nat -A MIHOMO -d 10.0.0.0/8 -j RETURN
iptables -t nat -A MIHOMO -d 127.0.0.0/8 -j RETURN
iptables -t nat -A MIHOMO -d 100.64.0.0/10 -j RETURN
iptables -t nat -A MIHOMO -d 169.254.0.0/16 -j RETURN
iptables -t nat -A MIHOMO -d 172.16.0.0/12 -j RETURN
iptables -t nat -A MIHOMO -d 192.168.0.0/16 -j RETURN
iptables -t nat -A MIHOMO -d 224.0.0.0/4 -j RETURN
for _nip in $NODE_IPS; do iptables -t nat -A MIHOMO -d ${_nip}/32 -j RETURN; done
iptables -t nat -A MIHOMO -p tcp -j REDIRECT --to-ports 7892

# QUIC/HTTP3(UDP443) 不走代理会直连出公司网被安全设备干扰，丢弃强制回落 TCP（2026-09-23）
iptables -I FORWARD 1 -i br-lan -p udp --dport 443 -m comment --comment mihomo-quic-drop -j DROP
iptables -t nat -A PREROUTING -i br-lan -j MIHOMO

iptables -t nat -N MIHOMO_DNS
iptables -t nat -A MIHOMO_DNS -d ${LAN} -j RETURN
iptables -t nat -A MIHOMO_DNS -p udp --dport 53 -j REDIRECT --to-ports 1053
iptables -t nat -A MIHOMO_DNS -p tcp --dport 53 -j REDIRECT --to-ports 1053
iptables -t nat -A PREROUTING -i br-lan -j MIHOMO_DNS

uci set dhcp.@dnsmasq[0].noresolv='1'
uci -q del_list dhcp.@dnsmasq[0].server='127.0.0.1#1053'
uci add_list dhcp.@dnsmasq[0].server='127.0.0.1#1053'
uci commit dhcp
/etc/init.d/dnsmasq restart
logger -t mihomo-ipt "redirects installed"
[ -x /etc/routermgmt/access-fw.sh ] && /etc/routermgmt/access-fw.sh
exit 0
