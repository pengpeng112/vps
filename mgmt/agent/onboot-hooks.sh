#!/bin/sh
# onboot-hooks — SN-R 舰队开机幂等钩子（2026-10-08 定稿，R1/R2/R4 事故沉淀）
# 职责：厂商开机重写 crontab 后自动恢复四条关键任务 + 强制 QUIC 拦截规则顺序。
# 背景：
#   - 厂商固件每次开机重写 crontab（netprobe/discovery 曾整批丢失，R4 实测）
#   - mihomo 的 QUIC DROP 若排在自管 REJECT 之前，浏览器 QUIC 被静默吞包
#     导致"视频打不开"（2026-10-04 R2 事故：DROP 166 包、REJECT 0）
# 部署：/etc/routermgmt/onboot-hooks.sh + rc.local 在 exit 0 前调用本脚本。
( sleep 20
  CT=/etc/crontabs/root
  mkdir -p /etc/crontabs; touch "$CT"
  for E in \
    '*/5 * * * * /etc/routermgmt/netprobe.sh #routermgmt-netprobe' \
    '* * * * * /etc/routermgmt/mgmt-port-discovery.sh #mgmt-port' \
    '*/5 * * * * /etc/routermgmt/connlog.sh #routermgmt-connlog' \
    '0 0 * * * /sbin/reboot #routermgmt-reboot'; do
    TAG=$(echo "$E" | sed 's/.*#\([^ ]*\).*$/#\1/')
    grep -q "$TAG" "$CT" 2>/dev/null || echo "$E" >> "$CT"
  done
  /etc/init.d/cron restart
  # QUIC 拦截：REJECT 必须先于任何 DROP（浏览器被拒后立即回落 TCP，不傻等）
  iptables -D FORWARD -i br-lan -p udp -m udp --dport 443 -j REJECT --reject-with icmp-port-unreachable 2>/dev/null
  iptables -I FORWARD 1 -i br-lan -p udp -m udp --dport 443 -j REJECT --reject-with icmp-port-unreachable
) > /dev/null 2>&1 &
exit 0
