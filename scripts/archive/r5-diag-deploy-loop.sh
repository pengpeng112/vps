#!/bin/bash
# R5 诊断+部署循环：窗口一到先抓 lan2/Web 诊断证据，再装 WAN 自愈看门狗
WW=$(sed -n 1p /f/vps/backups/.r2-deploy-b64.txt)
HP=$(sed -n 2p /f/vps/backups/.r2-deploy-b64.txt)
WD=$(sed -n 3p /f/vps/backups/.r2-deploy-b64.txt)
PT=$(sed -n 4p /f/vps/backups/.r2-deploy-b64.txt)
DM="ssh -p 28722 -i C:/Users/Administrator/.ssh/id_rsa_dmit -o IdentitiesOnly=yes -o ConnectTimeout=12 root@179.255.137.64"

for i in $(seq 1 120); do
  OUT=$($DM "sshpass -p '[REDACTED]' ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 -p 13225 root@127.0.0.1 '
echo ===DIAG===
echo ID=\$(grep DEVICE_ID /etc/routermgmt/agent.conf 2>/dev/null); uptime | cut -d, -f1
for p in lan1 lan2 lan3 lan4; do
  C=\$(cat /sys/class/net/\$p/carrier 2>/dev/null)
  B=\$(basename \$(readlink /sys/class/net/\$p/brport/bridge 2>/dev/null) 2>/dev/null)
  echo \$p carrier=\$C bridge=\$B
done
netstat -tln 2>/dev/null | grep -E \":(80|443) \" | head -3
echo uhttpd=\$(ps w | grep -c [u]httpd)
curl -sk -m 5 -o /dev/null -w self_http=%{http_code} http://192.168.132.1/ ; echo
iptables -S zone_lan_input 2>/dev/null | grep -cE \"REJECT|DROP\" ; iptables -S | grep -c 132
cat /tmp/dhcp.leases 2>/dev/null | head -5
cat /proc/net/arp | grep -E \"br-lan\" | head -5
echo ===DEPLOY===
echo $WW | base64 -d > /usr/sbin/wan-watch.sh && chmod 755 /usr/sbin/wan-watch.sh
echo $HP | base64 -d > /etc/hotplug.d/iface/wan-watch && chmod 644 /etc/hotplug.d/iface/wan-watch
echo $WD | base64 -d > /etc/routermgmt/wan-watchdog.sh && chmod 755 /etc/routermgmt/wan-watchdog.sh
grep -q wan-watchdog /etc/routermgmt/mgmt-port-discovery.sh || echo $PT | base64 -d >> /etc/routermgmt/mgmt-port-discovery.sh
sh -n /usr/sbin/wan-watch.sh && sh -n /etc/routermgmt/wan-watchdog.sh && sh -n /etc/routermgmt/mgmt-port-discovery.sh && echo SYNTAX_OK
/etc/routermgmt/wan-watchdog.sh && echo WD_RUN_OK
/usr/sbin/wan-watch.sh probe >/dev/null 2>&1 && echo WW_RUN_OK
'" 2>&1)
  echo "== try$i $(date +%H:%M:%S) =="
  echo "$OUT" | grep -E "ID=|up |carrier|self_http|uhttpd=|SYNTAX_OK|WD_RUN_OK|WW_RUN_OK|refused|reset|timed" | head -10
  if echo "$OUT" | grep -q "WD_RUN_OK"; then echo "R5_DEPLOY_SUCCESS try$i"; break; fi
  $DM '
for p in $(ss -tnp | grep "127.0.0.1:13225" | grep -oE "pid=[0-9]+" | sort -u | cut -d= -f2); do
  ST=$(ps -o etimes= -p $p 2>/dev/null | tr -d " ")
  if [ -n "$ST" ] && [ "$ST" -gt 150 ]; then kill $p 2>/dev/null && echo "killed stale pid=$p age=${ST}s"; fi
done' 2>/dev/null
  sleep 45
done
echo "LOOP_END $(date +%H:%M:%S)"
