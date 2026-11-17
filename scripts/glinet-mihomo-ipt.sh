#!/bin/sh
# GL-MT3600BE 透明代理 via iptables（SN-R 系 mihomo-ipt 的 GL 适配版 2026-11-12）
# 差异：LAN=192.168.8.0/24；NODE_IPS=当前活节点清单（新增/换节点必须同步）；
#       管理端点广州+GIA 双 SSH 口直连（隧道保命）；mihomo down 自动摘除防黑洞。
LAN=192.168.8.0/24
# 节点/管理直连清单（换节点/换 IP 必须同步更新此清单）：
#   GIA 新(154.36.139.155)=VLESS主+HY2+CF源站隧道 | TX(43.134.46.44) BWG(74.82.203.175)=备用端点
#   CF 边缘 104.18.32.7 / 162.159.135.42 | 广州管理台 134.175.151.213
NODE_IPS="154.36.139.155 43.134.46.44 74.82.203.175 104.18.32.7 162.159.135.42 134.175.151.213"

mihomo_up() {
    curl -s -m 2 -o /dev/null http://127.0.0.1:9090/version 2>/dev/null
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
    /etc/init.d/dnsmasq restart 2>/dev/null
    exit 0
    ;;
esac

if ! mihomo_up; then
    clear_rules
    logger -t mihomo-ipt "core down, redirects removed"
    exit 0
fi

# 幂等检测：核心 up 且规则齐全（PREROUTING 挂 MIHOMO + MIHOMO_DNS 双链 + dnsmasq 指 1053）则直接退出，不做任何改动
# 避免 cron 每分钟重跑引发 dnsmasq restart（DNS 每分钟抖动）
if mihomo_up && \
   iptables -t nat -L PREROUTING -n 2>/dev/null | grep -q ' MIHOMO$' && \
   iptables -t nat -L PREROUTING -n 2>/dev/null | grep -q ' MIHOMO_DNS$' && \
   grep -q "127.0.0.1#1053" /etc/config/dhcp 2>/dev/null; then
    exit 0
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

# QUIC/HTTP3(UDP443) 直连会被运营商干扰，丢弃强制回落 TCP（舰队标准）
iptables -I FORWARD 1 -i br-lan -p udp --dport 443 -m comment --comment mihomo-quic-drop -j DROP
iptables -t nat -A PREROUTING -i br-lan -j MIHOMO

# DNS 劫持到 mihomo 1053（fake-ip 模式必需，防 DNS 泄漏）
iptables -t nat -N MIHOMO_DNS
iptables -t nat -A MIHOMO_DNS -d ${LAN} -j RETURN
iptables -t nat -A MIHOMO_DNS -p udp --dport 53 -j REDIRECT --to-ports 1053
iptables -t nat -A MIHOMO_DNS -p tcp --dport 53 -j REDIRECT --to-ports 1053
iptables -t nat -A PREROUTING -i br-lan -j MIHOMO_DNS

uci set dhcp.@dnsmasq[0].noresolv='1'
uci -q del_list dhcp.@dnsmasq[0].server='127.0.0.1#1053'
uci add_list dhcp.@dnsmasq[0].server='127.0.0.1#1053'
uci commit dhcp
/etc/init.d/dnsmasq restart 2>/dev/null
logger -t mihomo-ipt "redirects installed (GL-MT3600BE)"
exit 0
