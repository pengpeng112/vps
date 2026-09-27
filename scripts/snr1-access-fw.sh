#!/bin/sh
# SN-R1 访问控制（iptables + ipset）。模式文件与雅典娜相同。
STATE="${RM_STATE_DIR:-/etc/routermgmt/state}"
MODE=$(cat "$STATE/access.mode" 2>/dev/null || echo all)
LIST=$(cat "$STATE/access.list" 2>/dev/null | tr -d '\r' | tr -d ' ')

iptables -t nat -F MIHOMO_ACC 2>/dev/null
iptables -t nat -F MIHOMO_ACC_DNS 2>/dev/null
ipset destroy rmgmt_acc 2>/dev/null

iptables -t nat -N MIHOMO_ACC 2>/dev/null
iptables -t nat -N MIHOMO_ACC_DNS 2>/dev/null
iptables -t nat -C MIHOMO -j MIHOMO_ACC 2>/dev/null || iptables -t nat -I MIHOMO 1 -j MIHOMO_ACC
iptables -t nat -C MIHOMO_DNS -j MIHOMO_ACC_DNS 2>/dev/null || iptables -t nat -I MIHOMO_DNS 1 -j MIHOMO_ACC_DNS

case "$MODE" in
    whitelist|blacklist) ;;
    *) exit 0 ;;
esac
[ -n "$LIST" ] || exit 0

ipset create rmgmt_acc hash:ip family inet -exist
ipset flush rmgmt_acc
oldifs=$IFS
IFS=,
for it in $LIST; do
    case "$it" in
        *:*) ;;
        *.*.*.*) ipset add rmgmt_acc "$it" 2>/dev/null ;;
    esac
done
IFS=$oldifs

if [ "$MODE" = "blacklist" ]; then
    iptables -t nat -A MIHOMO_ACC -m set --match-set rmgmt_acc src -j RETURN
    iptables -t nat -A MIHOMO_ACC_DNS -m set --match-set rmgmt_acc src -p udp --dport 53 -j DNAT --to-destination 223.5.5.5:53
    iptables -t nat -A MIHOMO_ACC_DNS -m set --match-set rmgmt_acc src -p tcp --dport 53 -j DNAT --to-destination 223.5.5.5:53
else
    iptables -t nat -A MIHOMO_ACC -m set ! --match-set rmgmt_acc src -j RETURN
    iptables -t nat -A MIHOMO_ACC_DNS -m set ! --match-set rmgmt_acc src -p udp --dport 53 -j DNAT --to-destination 223.5.5.5:53
    iptables -t nat -A MIHOMO_ACC_DNS -m set ! --match-set rmgmt_acc src -p tcp --dport 53 -j DNAT --to-destination 223.5.5.5:53
fi
logger -t rmgmt-access "iptables mode=$MODE list=$LIST"
exit 0
