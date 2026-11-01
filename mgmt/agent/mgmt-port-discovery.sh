#!/bin/sh
# mgmt-port-discovery.sh — agent 多端口自动选路补丁（2026-10-01 三方冗余）
#
# 用途：mgmt-tunnel v2 部署后，主/备隧道分别在 18443/18444/18445 监听。
#       此脚本探测哪个端口活着，写入 /tmp/mgmt_active_port 供 agent 读取。
#       agent 脚本在 API 调用前 source 此文件即可自动选路。
#
# 部署：/etc/routermgmt/mgmt-port-discovery.sh，由 agent 启动时和 cron 每分钟调用。
# 输出：设置 MGMT_ACTIVE_PORT 和 MGMT_URL 环境变量（source 用法），
#       或写 /tmp/mgmt_active_port 文件（agent 读取用法）。

# 尝试顺序：18443 → 18444 → 18445 → 18446 → 18447（v3.1 五端点，2026-10-26 加 66clouds GIA）
# 超时 2 秒/端口（本地回环，不需要长超时）
_discover() {
    for _port in 18443 18444 18445 18446 18447; do
        if curl -fsk -m 2 -o /dev/null \
           "https://127.0.0.1:${_port}/api/v1/health" 2>/dev/null; then
            echo "$_port"
            return 0
        fi
    done
    return 1
}

# Start tunnel recovery even when discovery finds no healthy endpoint.
# The watchdog owns its lock, sampling interval and cooldown.
[ -x /etc/routermgmt/tunnel-watchdog.sh ] && /etc/routermgmt/tunnel-watchdog.sh >/dev/null 2>&1 &

# 主入口
ACTIVE=$(_discover)
if [ -n "$ACTIVE" ]; then
    # 写文件供 agent 读取
    echo "$ACTIVE" > /tmp/mgmt_active_port
    # 直接输出（source 用法）
    MGMT_ACTIVE_PORT="$ACTIVE"
    MGMT_URL="https://127.0.0.1:${ACTIVE}"
    export MGMT_ACTIVE_PORT MGMT_URL
else
    # 全部不通：清除缓存，agent 会报心跳失败
    rm -f /tmp/mgmt_active_port 2>/dev/null
    logger -t mgmt-port "WARN: all management ports unreachable (18443-18447)"
    return 1 2>/dev/null || exit 1
fi

# 2026-10-24 标准挂靠（随 #mgmt-port 每分钟运行，厂商重写 crontab 后由 rc.local 守护块补回）：
# 隧道看门狗：心跳通道连续 ~2 分钟死→重启 mgmt-tunnel（防半开挂死，R5 42h 实证）
# WAN 看门狗：默认 log 模式；站点确认无误判后置 /etc/routermgmt/wan-watchdog.mode=heal 启用自愈
[ -x /etc/routermgmt/wan-watchdog.sh ] && /etc/routermgmt/wan-watchdog.sh >/dev/null 2>&1 &

# 2026-10-28 透明代理入口自愈（R1 实证：开机 fw3 重载冲掉 PREROUTING 的 MIHOMO 跳转，
# 链在入口丢，客户端国内通国外断）。mihomo 活着而入口缺失时重跑 mihomo-ipt 重建。
if curl -fsk -m 3 -o /dev/null http://127.0.0.1:9090/version 2>/dev/null; then
    iptables -t nat -S PREROUTING 2>/dev/null | grep -q "\-i br-lan -j MIHOMO" ||         { logger -t mihomo-guard "PREROUTING MIHOMO jump missing, rebuilding"; sh /etc/mihomo-ipt.sh >/dev/null 2>&1; }
fi
