#!/bin/sh
# mihomo 透明代理 nft 规则 + 自愈（busybox sh，OpenWrt/libwrt）
#
# 设计原则（本轮事故教训）：
#   核心死亡时本脚本【只拆不装】——任何情况下 LAN 都必须能直连上网。
#   规则只在核心确认存活后才安装。
#
# 调用点（两个）：
#   1) /etc/init.d/mihomo start_service —— 后台等核心就绪后调用（装规则）
#   2) firewall include（fw4 reload / 开机早期）—— 此时核心多半未起，
#      走【拆除/回退】分支，无害（这是自愈的本意）
#
# 注意：路由器上现存的 /etc/mihomo-fw.sh 是 dump 版，部署时必须先整份
#       用本文件覆盖（用 qf_client.py writefile，写完 readfile 读回校验）。

API="http://127.0.0.1:9090/version"
# 节点 VPS 公网 IP 永不重定向（防回环）；新增/更换节点必须同步此清单
# 2026-09-27 M03：补腾讯 43.134.46.44——串联拓扑下 SN-R1 的腾讯节点 TCP 曾被
# 本表宽 TCP redirect 二次接入（经规则 DIRECT 放行，多一层核心处理）
NODE_IPS="159.223.36.119 43.134.46.44"
PRIVSET="{ 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 127.0.0.0/8, 100.64.0.0/10, 169.254.0.0/16, 224.0.0.0/4, 255.255.255.255 }"

alive() { wget -T 3 -q -O /dev/null "$API" 2>/dev/null; }

dns_to_direct() {
    # 核心死亡 → dnsmasq 回退运营商 DNS（否则 LAN 解析全挂 =  perceived 断网）
    if grep -q "127.0.0.1#1053" /etc/config/dhcp 2>/dev/null; then
        sed -i "s/127\.0\.0\.1#1053/223.5.5.5/" /etc/config/dhcp
        sed -i "/option noresolv '1'/d" /etc/config/dhcp
        /etc/init.d/dnsmasq restart >/dev/null 2>&1
        logger -t mihomo-fw "dnsmasq fallback -> 223.5.5.5"
    fi
}

dns_to_core() {
    # 核心存活 → dnsmasq 严格指向核心 DNS（fake-ip 的前提，绝不能留双上游竞速）
    if ! grep -q "127.0.0.1#1053" /etc/config/dhcp 2>/dev/null; then
        sed -i "s/list server '223\.5\.5\.5'/list server '127.0.0.1#1053'/" /etc/config/dhcp
        if ! grep -q "option noresolv" /etc/config/dhcp 2>/dev/null; then
            sed -i "s/option domainneeded '1'/option domainneeded '1'\n\toption noresolv '1'/" /etc/config/dhcp
        fi
        /etc/init.d/dnsmasq restart >/dev/null 2>&1
        logger -t mihomo-fw "dnsmasq -> 127.0.0.1#1053"
    fi
}

# ---------- 核心死亡：拆除 + 回退 ----------
if ! alive; then
    nft delete table inet mihomo 2>/dev/null
    dns_to_direct
    logger -t mihomo-fw "core NOT alive: rules removed, dns fallback (self-heal)"
    exit 0
fi

# ---------- 核心存活：装规则 ----------
dns_to_core

nft list table inet mihomo >/dev/null 2>&1 || nft add table inet mihomo
nft list chain inet mihomo prerouting >/dev/null 2>&1 || \
    nft add chain inet mihomo prerouting '{ type nat hook prerouting priority -100; policy accept; }'
nft flush chain inet mihomo prerouting

# 节点 VPS 自身永不重定向（防回环；也放行下游串联路由器到节点的连接）
for _nip in $NODE_IPS; do
    nft add rule inet mihomo prerouting ip daddr $_nip counter return
done

# LAN 客户端硬编码境外 DNS → 核心 DNS（fake-ip 防污染）
nft add rule inet mihomo prerouting iifname "br-lan" udp dport 53 ip daddr != 192.168.2.0/24 counter redirect to :1053
nft add rule inet mihomo prerouting iifname "br-lan" tcp dport 53 ip daddr != 192.168.2.0/24 counter redirect to :1053

# QUIC：redir-port 只处理 TCP，UDP443 直连出境会被 GFW 掐/污染 → drop 逼浏览器回退 TCP
nft add rule inet mihomo prerouting iifname "br-lan" udp dport 443 ip daddr != $PRIVSET counter drop

# LAN TCP → 透明代理（198.18.0.0/16 fake-ip 属于非私网，自然被包含）
nft add rule inet mihomo prerouting iifname "br-lan" tcp dport 1-65535 ip daddr != $PRIVSET counter redirect to :7892

logger -t mihomo-fw "rules installed $(date)"
# 访问控制规则必须在 flush 之后重插，否则白名单/黑名单会丢
if [ -x /etc/routermgmt/access-fw.sh ]; then
    /etc/routermgmt/access-fw.sh
fi
exit 0
