#!/bin/sh
# WAN 分层探活 + 持久日志（overlay，重启不丢）
# 用途：下次再断网时能分清是 PHY 掉线 / DHCP 丢地址 / 公司网关不通 / 公网 ICMP 不通 / 代理挂 / WG 回包丢。
#
# 日志：
#   /etc/routermgmt/logs/wan-watch.log     本脚本（轮转 .1 .2）
#   /etc/routermgmt/logs/syslog            logd 持久系统日志（netifd/udhcpc/内核）
# 一行一个快照，字段全是 k=v，grep event=change 即可看状态翻转。

LOGDIR=/etc/routermgmt/logs
LOG=$LOGDIR/wan-watch.log
LAST=$LOGDIR/wan-watch.last
VPS=159.223.36.119
WG_HUB=10.10.0.1
MIHOMO=http://127.0.0.1:9090/version

mkdir -p "$LOGDIR"

rotate() {
    [ -f "$LOG" ] || return 0
    sz=$(wc -c < "$LOG" 2>/dev/null || echo 0)
    [ "$sz" -gt 400000 ] || return 0
    mv -f "$LOG.1" "$LOG.2" 2>/dev/null
    mv -f "$LOG" "$LOG.1" 2>/dev/null
}

# busybox wget -T 对 DNS 卡住无效，必须自己杀
run_to() {
    secs=$1; shift
    "$@" >/dev/null 2>&1 &
    pid=$!
    n=0
    while [ "$n" -lt "$secs" ]; do
        kill -0 "$pid" 2>/dev/null || { wait "$pid"; return $?; }
        sleep 1
        n=$((n + 1))
    done
    kill "$pid" 2>/dev/null
    wait "$pid" 2>/dev/null
    return 124
}

ping1() {
    run_to 3 ping -c 1 -W 2 "$1" && echo ok || echo fail
}

curl_ok() {
    # 只问「TCP 有没有连上」：0=HTTP 成功；52 空回复；60/51/35 SSL 问题都说明 L4 通
    # 7=连不上 28=超时 6=DNS 失败 → fail
    curl -sS -m 3 --connect-timeout 3 -k -o /dev/null "$1" >/dev/null 2>&1
    rc=$?
    case "$rc" in
        0|52|56|60|35|51|77) echo ok ;;
        *) echo fail ;;
    esac
}

tcp443() {
    curl_ok "https://$1/"
}

probe() {
    ts=$(date '+%Y-%m-%d %H:%M:%S')
    uptime_s=$(cut -d. -f1 /proc/uptime)
    event=${1:-probe}

    wan_oper=$(cat /sys/class/net/wan/operstate 2>/dev/null || echo missing)
    wan_carrier=$(cat /sys/class/net/wan/carrier 2>/dev/null || echo n)
    wan_speed=$(cat /sys/class/net/wan/speed 2>/dev/null || echo n)

    wan_ips=$(ip -4 -o addr show dev wan 2>/dev/null | awk '{print $4}' | tr '\n' ',' | sed 's/,$//')
    [ -n "$wan_ips" ] || wan_ips=none
    extra_ip=no
    echo "$wan_ips" | grep -q '10.1.2.18' && extra_ip=yes

    gw=$(ip -4 route show default 2>/dev/null | awk '/dev wan/{print $3; exit}')
    [ -n "$gw" ] || gw=none

    # 只在有默认路由时探 L3，避免无谓超时
    if [ "$gw" != none ]; then
        gw_ping=$(ping1 "$gw")
        inet_ping=$(ping1 223.5.5.5)
        vps_ping=$(ping1 "$VPS")
        vps443=$(tcp443 "$VPS")
    else
        gw_ping=skip
        inet_ping=skip
        vps_ping=skip
        vps443=skip
    fi

    wg_hs=none
    wg_rx=0
    if command -v wg >/dev/null 2>&1; then
        hs=$(wg show wg0 latest-handshakes 2>/dev/null | awk '{print $2; exit}')
        now=$(date +%s)
        if [ -n "$hs" ] && [ "$hs" -gt 0 ] 2>/dev/null; then
            age=$((now - hs))
            wg_hs=${age}s
        fi
        wg_rx=$(wg show wg0 transfer 2>/dev/null | awk '{print $2; exit}')
        [ -n "$wg_rx" ] || wg_rx=0
    fi

    mihomo=dead
    curl -sS -m 2 --connect-timeout 2 -o /dev/null "$MIHOMO" 2>/dev/null && mihomo=ok

    # 走 IP 的 HTTP，不解析域名（避免 fake-ip/DNS 把探测卡死）
    http=$(curl_ok http://1.1.1.1/)

    line="ts=$ts event=$event uptime=${uptime_s}s wan_oper=$wan_oper wan_carrier=$wan_carrier wan_speed=$wan_speed wan_ip=$wan_ips extra_ip=$extra_ip gw=$gw gw_ping=$gw_ping inet_ping=$inet_ping vps_ping=$vps_ping vps443=$vps443 wg_hs=$wg_hs wg_rx=$wg_rx mihomo=$mihomo http=$http"

    # 状态签名：变了就打 event=change，方便事后 grep
    sig="oper=$wan_oper carrier=$wan_carrier ip=$wan_ips extra=$extra_ip gw=$gw gw_ping=$gw_ping inet=$inet_ping vps=$vps_ping v443=$vps443 mihomo=$mihomo http=$http"
    prev=""
    [ -f "$LAST" ] && prev=$(cat "$LAST")
    if [ "$event" = probe ] && [ -n "$prev" ] && [ "$sig" != "$prev" ]; then
        event=change
        line="ts=$ts event=$event uptime=${uptime_s}s wan_oper=$wan_oper wan_carrier=$wan_carrier wan_speed=$wan_speed wan_ip=$wan_ips extra_ip=$extra_ip gw=$gw gw_ping=$gw_ping inet_ping=$inet_ping vps_ping=$vps_ping vps443=$vps443 wg_hs=$wg_hs wg_rx=$wg_rx mihomo=$mihomo http=$http"
        logger -t wan-watch "$line"
    fi
    echo "$sig" > "$LAST"

    rotate
    echo "$line" >> "$LOG"
    # 开机第一次 / 热插拔事件也进 syslog，重启后 logread 没了但 syslog 文件还在
    if [ "$event" != probe ]; then
        logger -t wan-watch "$line"
    fi
}

loop() {
    # 开机立刻打一条 boot，再 60s 一轮
    probe boot
    while true; do
        sleep 60
        probe probe
    done
}

case "${1:-probe}" in
    loop) loop ;;
    probe) probe probe ;;
    boot) probe boot ;;
    event)
        shift
        probe "event:${1:-hotplug}"
        ;;
    *) probe probe ;;
esac
