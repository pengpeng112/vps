#!/bin/bash
# p0-lossguard.sh — 远程失联防线 P0（在管理机 159.223.36.119 上以 root 运行）
#
# 组成：
#   Part A：sshd 服务器端保活（ClientAliveInterval 30 / CountMax 3）
#           → 死掉的 -R 会话 90 秒内被 sshd 回收，1322x 端口释放给设备重连
#           → 解决"僵尸监听占端口 + dbclient 无 ExitOnForwardFailure 卡死"循环的 VPS 侧
#   Part B：mgmt-rssh-probe 探测覆盖扩展到舰队端口（纯配置追加，脚本本身不动）
#
# 执行纪律（整改文档 P0）：
#   - Part A 用 reload 不用 restart（不断现有连接）；sshd -t 校验失败自动放弃
#   - 执行后 30 分钟内观察全部隧道（雅典娜/R3/工位跳板）心跳与 banner
#   - 与设备侧隧道脚本改动（P0-3）永不同天执行
# 回滚：
#   Part A: rm /etc/ssh/sshd_config.d/98-clientalive.conf && systemctl reload ssh
#   Part B: cp /etc/mgmt-rssh-probe.conf.bak-<ts> /etc/mgmt-rssh-probe.conf && systemctl restart mgmt-rssh-probe.timer
set -u

echo "== Part A 前置: 整文件备份 + 现状快照 =="
TS0=$(date +%Y%m%d-%H%M%S)
cp -a /etc/ssh/sshd_config /root/sshd_config.bak-$TS0
cp -a /etc/ssh/sshd_config.d /root/sshd_config.d.bak-$TS0
md5sum /etc/ssh/sshd_config /etc/ssh/sshd_config.d/*.conf > /root/sshd-pre-$TS0.md5
grep -nE '^\s*(Match|PermitOpen|PermitListen|AllowTcpForwarding)' /etc/ssh/sshd_config /etc/ssh/sshd_config.d/*.conf > /root/sshd-match-pre-$TS0.txt || true
echo "备份: /root/sshd_config{,.d}.bak-$TS0；改后若 diff 超出预期（Match/Permit* 行变化）立即还原"

echo "== Part A: sshd ClientAlive =="
if grep -rE '^\s*ClientAliveInterval' /etc/ssh/sshd_config /etc/ssh/sshd_config.d/ 2>/dev/null | grep -v '^#'; then
  echo "已存在 ClientAliveInterval 配置，跳过（避免双重定义）"
else
  cat > /etc/ssh/sshd_config.d/98-clientalive.conf <<'EOF'
# 2026-09-30 P0-1: 服务器端保活，90s 回收死会话（防 -R 僵尸占口）。回滚=删除本文件后 reload。
ClientAliveInterval 30
ClientAliveCountMax 3
EOF
  if sshd -t 2>/dev/null; then
    systemctl reload ssh
    echo "sshd 配置已生效（reload，现有连接不受影响）"
  else
    echo "sshd -t 校验失败！回滚删除配置文件"; rm -f /etc/ssh/sshd_config.d/98-clientalive.conf; exit 1
  fi
fi

echo "== Part B: 探测覆盖舰队端口 =="
TS=$(date +%Y%m%d-%H%M%S)
cp /etc/mgmt-rssh-probe.conf /etc/mgmt-rssh-probe.conf.bak-$TS 2>/dev/null || echo "conf 不存在，将新建"
# 13224 现为 R3 占用（R3/R4 共用口，改 13227 后更新此行）
add() { grep -qE "^$1 " /etc/mgmt-rssh-probe.conf 2>/dev/null || echo "$*" >> /etc/mgmt-rssh-probe.conf; }
add 13223 rtr-a9bcba      # SN-R2
add 13224 rtr-38aa54      # SN-R3（当前部署位）
add 13225 rtr-c46a25      # SN-R5
add 13226 rtr-673568      # SN-R6
echo "--- 当前探测清单 ---"; cat /etc/mgmt-rssh-probe.conf
systemctl restart mgmt-rssh-probe.timer 2>/dev/null || systemctl restart mgmt-rssh-probe 2>/dev/null || true
sleep 35
echo "--- 探测日志尾部（应有新端口记录）---"
tail -n 8 /var/log/mgmt-rssh-probe.log 2>/dev/null

echo "== Part A 后核：Match/Permit* 完整性 =="
grep -nE '^\s*(Match|PermitOpen|PermitListen|AllowTcpForwarding)' /etc/ssh/sshd_config /etc/ssh/sshd_config.d/*.conf > /tmp/sshd-match-post.txt || true
if ! diff -q /root/sshd-match-pre-$TS0.txt /tmp/sshd-match-post.txt >/dev/null; then
  echo "!! Match/Permit* 配置出现差异（本脚本不应触碰这些行）——请人工核对："
  diff /root/sshd-match-pre-$TS0.txt /tmp/sshd-match-post.txt
  echo "如需回滚: cp -a /root/sshd_config.bak-$TS0 /etc/ssh/sshd_config && rm -rf /etc/ssh/sshd_config.d && cp -a /root/sshd_config.d.bak-$TS0 /etc/ssh/sshd_config.d && systemctl reload ssh"
fi

echo "== Part C: 通道活性即时验证（执行后必看，30 分钟内复查一轮）=="
ss -tn state established '( sport = :28722 )' | tail -n +2 | wc -l | xargs echo "28722 现存隧道数(应≥3 不减):"
for p in 18222 13224; do echo -n "  127.0.0.1:$p banner: "; timeout 4 nc 127.0.0.1 $p </dev/null 2>/dev/null | head -c 12; echo; done

echo "== 完成。验收：管理台各设备 remote_ssh_alive 应在 1-2 分钟内变为 true/false（不再是 null）=="
