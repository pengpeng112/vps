#!/bin/bash
# R2 WAN 自愈部署循环 v2：90 分钟窗口，每 45s 一轮
# 每轮：先试部署（成功即退出）；失败则强清 13223 僵尸持有者（>150s 的会话，无论内核视角 ESTAB——
# 死路径上 ESTAB 是假象，强杀后设备 respawn 数秒内重绑，是解除 respawn 死锁的唯一办法）
WW=$(sed -n 1p /f/vps/backups/.r2-deploy-b64.txt)
HP=$(sed -n 2p /f/vps/backups/.r2-deploy-b64.txt)
WD=$(sed -n 3p /f/vps/backups/.r2-deploy-b64.txt)
PT=$(sed -n 4p /f/vps/backups/.r2-deploy-b64.txt)
DM="ssh -p 28722 -i C:/Users/Administrator/.ssh/id_rsa_dmit -o IdentitiesOnly=yes -o ConnectTimeout=12 root@179.255.137.64"

for i in $(seq 1 120); do
  OUT=$($DM "sshpass -p '[REDACTED]' ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 -p 13223 root@127.0.0.1 '
echo $WW | base64 -d > /usr/sbin/wan-watch.sh && chmod 755 /usr/sbin/wan-watch.sh
echo $HP | base64 -d > /etc/hotplug.d/iface/wan-watch && chmod 644 /etc/hotplug.d/iface/wan-watch
echo $WD | base64 -d > /etc/routermgmt/wan-watchdog.sh && chmod 755 /etc/routermgmt/wan-watchdog.sh
grep -q wan-watchdog /etc/routermgmt/mgmt-port-discovery.sh || echo $PT | base64 -d >> /etc/routermgmt/mgmt-port-discovery.sh
sh -n /usr/sbin/wan-watch.sh && sh -n /etc/routermgmt/wan-watchdog.sh && sh -n /etc/routermgmt/mgmt-port-discovery.sh && echo SYNTAX_OK
md5sum /usr/sbin/wan-watch.sh /etc/routermgmt/wan-watchdog.sh
tail -3 /etc/routermgmt/mgmt-port-discovery.sh
/etc/routermgmt/wan-watchdog.sh && echo WD_RUN_OK
/usr/sbin/wan-watch.sh probe >/dev/null 2>&1 && echo WW_RUN_OK && tail -1 /etc/routermgmt/logs/wan-watch.log
'" 2>&1)
  echo "== try$i $(date +%H:%M:%S) =="
  echo "$OUT" | grep -E "SYNTAX_OK|WD_RUN_OK|WW_RUN_OK|wan-watch|refused|reset|timed|denied" | head -8
  if echo "$OUT" | grep -q "WD_RUN_OK"; then echo "DEPLOY_SUCCESS try$i"; break; fi
  # 失败→强清僵尸持有者（>150s）
  $DM '
for p in $(ss -tnp | grep "127.0.0.1:13223" | grep -oE "pid=[0-9]+" | sort -u | cut -d= -f2); do
  ST=$(ps -o etimes= -p $p 2>/dev/null | tr -d " ")
  if [ -n "$ST" ] && [ "$ST" -gt 150 ]; then kill $p 2>/dev/null && echo "killed stale pid=$p age=${ST}s"; fi
done' 2>/dev/null
  sleep 45
done
echo "LOOP_END $(date +%H:%M:%S)"
