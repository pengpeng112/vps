#!/bin/sh /etc/rc.common
# Direct mihomo runtime (bypasses broken OpenClash wrapper on this build)
# Config: /etc/mihomo/run.yaml  Core: /usr/bin/mihomo -> /etc/openclash/core/clash_meta
# Geo DBs are read from -d /etc/openclash (GeoSite.dat / Country.mmdb)
#
# 为什么用软链启动：管理系统 Agent 用 `pidof mihomo` 探测代理进程存活
# （routermgmt-agent.sh），直接跑 clash_meta 进程名对不上，心跳会永远报代理已死。
# 软链必须先行创建（ttyd 一条命令）：
#   ln -sf /etc/openclash/core/clash_meta /usr/bin/mihomo

USE_PROCD=1
START=99
STOP=15

start_service() {
    # 软链自建：管理系统 Agent 用 `pidof mihomo` 探测，进程名必须对上
    [ -e /usr/bin/mihomo ] || ln -sf /etc/openclash/core/clash_meta /usr/bin/mihomo
    procd_open_instance
    procd_set_param command /usr/bin/mihomo -d /etc/openclash -f /etc/mihomo/run.yaml
    procd_set_param respawn 3600 5 0
    procd_set_param stdout 1
    procd_set_param stderr 1
    procd_set_param pidfile /var/run/mihomo.pid
    procd_close_instance
    # 核心就绪后才装 nft 规则（mihomo-fw.sh 内部有存活检查，双保险；
    # firewall include 只负责"核心死亡时拆除"的自愈分支）
    (
        i=0
        while [ "$i" -lt 60 ]; do
            wget -T 2 -q -O /dev/null http://127.0.0.1:9090/version 2>/dev/null && break
            sleep 1; i=$((i + 1))
        done
        /etc/mihomo-fw.sh
    ) &
}

reload_service() {
    stop
    start
}
