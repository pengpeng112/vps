#!/bin/sh
# wan-watchdog.sh — WAN 接口自愈看门狗（2026-10-24 进标准）
# 动机：DHCP 上级（家里/办公网关）偶发不给地址或 renew 失败，设备 WAN 假死无人处理（R2 实战）。
# 调用：mgmt-port-discovery.sh 每分钟带起。
# 模式（/etc/routermgmt/wan-watchdog.mode，默认 log）：
#   log  = 只记日志不动作（新刷默认；观察期确认无误判后改 heal）
#   heal = WAN 非 up 时 ifup wan 重试（前 10 次，之后只记录）
LOGTAG=wan-watchdog
MODE=$(cat /etc/routermgmt/wan-watchdog.mode 2>/dev/null || echo log)
CNTF=/tmp/wan-watchdog.n
if ifstatus wan 2>/dev/null | grep -q '"up": true'; then
    if [ -f "$CNTF" ]; then
        logger -t $LOGTAG "WAN recovered after $(cat $CNTF) obs"
        rm -f "$CNTF"
    fi
    exit 0
fi
N=$(cat "$CNTF" 2>/dev/null || echo 0)
N=$((N + 1))
echo "$N" > "$CNTF"
[ "$N" -le 3 ] && logger -t $LOGTAG "WAN not up (mode=$N obs #$N)"
if [ "$MODE" = "heal" ] && [ "$N" -le 10 ]; then
    logger -t $LOGTAG "ifup wan retry #$N"
    ifup wan
fi
exit 0
