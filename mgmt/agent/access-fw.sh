#!/bin/sh
# 访问控制：按源 IP/MAC 决定是否走透明代理。
# 由 Agent 写入 /etc/routermgmt/state/access.{mode,list} 后调用；
# mihomo-fw.sh 装完拦截规则也会再调一次（避免 fw4 reload 冲掉）。
#   all        — 全部设备走当前运行模式（默认）
#   whitelist  — 仅名单走代理，其余 DNS 改 223.5.5.5 并直连
#   blacklist  — 名单直连，其余走代理
STATE="${RM_STATE_DIR:-/etc/routermgmt/state}"
MODE=$(cat "$STATE/access.mode" 2>/dev/null || echo all)
LIST=$(cat "$STATE/access.list" 2>/dev/null | tr -d '\r')

# 清掉本脚本上次插入的规则
if nft -a list chain inet mihomo prerouting >/dev/null 2>&1; then
    nft -a list chain inet mihomo prerouting 2>/dev/null | awk '/rmgmt-access/{print $NF}' | while read h; do
        case "$h" in
            ''|*[!0-9]*) ;;
            *) nft delete rule inet mihomo prerouting handle "$h" 2>/dev/null ;;
        esac
    done
fi

case "$MODE" in
    whitelist|blacklist) ;;
    *) exit 0 ;;
esac
[ -n "$LIST" ] || exit 0
nft list chain inet mihomo prerouting >/dev/null 2>&1 || exit 0

IPS=""
MACS=""
oldifs=$IFS
IFS=,
for it in $LIST; do
    it=$(echo "$it" | tr -d ' ')
    [ -n "$it" ] || continue
    case "$it" in
        *:*) MACS="$MACS $it" ;;
        *)   IPS="$IPS $it" ;;
    esac
done
IFS=$oldifs

setbuild() { # $1=ips or macs → "{ a, b }"
    _s=""
    for x in $1; do
        [ -n "$_s" ] && _s="$_s, $x" || _s="$x"
    done
    [ -n "$_s" ] && echo "{ $_s }"
}

IPSET=$(setbuild "$IPS")
MACSET=$(setbuild "$MACS")

ins() { # 其余参数直接交给 nft insert
    nft insert rule inet mihomo prerouting iifname "br-lan" "$@" comment "rmgmt-access" 2>/dev/null
}

if [ "$MODE" = "blacklist" ]; then
    # 命中即直连（OR）
    if [ -n "$IPSET" ]; then
        ins ip saddr $IPSET udp dport 53 dnat to 223.5.5.5
        ins ip saddr $IPSET tcp dport 53 dnat to 223.5.5.5
        ins ip saddr $IPSET return
    fi
    if [ -n "$MACSET" ]; then
        ins ether saddr $MACSET udp dport 53 dnat to 223.5.5.5
        ins ether saddr $MACSET tcp dport 53 dnat to 223.5.5.5
        ins ether saddr $MACSET return
    fi
else
    # whitelist：IP 与 MAC 都不在名单才直连（AND）
    MATCH=""
    [ -n "$IPSET" ] && MATCH="$MATCH ip saddr != $IPSET"
    [ -n "$MACSET" ] && MATCH="$MATCH ether saddr != $MACSET"
    # shellcheck disable=SC2086
    ins $MATCH udp dport 53 dnat to 223.5.5.5
    ins $MATCH tcp dport 53 dnat to 223.5.5.5
    ins $MATCH return
fi
logger -t rmgmt-access "applied mode=$MODE list=$LIST"
exit 0
