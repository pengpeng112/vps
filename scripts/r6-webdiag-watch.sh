#!/bin/bash
# R6 回线监视：DMIT 路由恢复（13226 重绑）即跑 Web 登录诊断 + 隧道健康快照
DM="ssh -p 28722 -i C:/Users/Administrator/.ssh/id_rsa_dmit -o IdentitiesOnly=yes -o ConnectTimeout=12 root@179.255.137.64"
for i in $(seq 1 90); do
  OUT=$($DM "sshpass -p '[REDACTED]' ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 -p 13226 root@127.0.0.1 '
echo \"=== \$(date +%H:%M:%S) uptime=\$(cut -d. -f1 /proc/uptime)s ===\"
echo hb=\$(logread | grep -i heartbeat | tail -1 | cut -c7-30)
echo uhttpd=\$(ps w | grep -c \"[u]httpd\") rpcd=\$(ps w | grep -c \"[r]pcd\")
curl -s -o /dev/null -w \"root=%{http_code} \" http://127.0.0.1/ 2>/dev/null
curl -s -o /dev/null -w \"luci=%{http_code} \" http://127.0.0.1/cgi-bin/luci 2>/dev/null
curl -s -m 8 -X POST http://127.0.0.1/cgi-bin/luci/rpc/auth -H \"Content-Type: application/json\" -d \"{\\\"jsonrpc\\\":\\\"2.0\\\",\\\"id\\\":1,\\\"method\\\":\\\"login\\\",\\\"params\\\":[\\\"root\\\",\\\"[REDACTED]\\\"]}\" | head -c 130; echo
logread | grep -iE \"uhttpd|rpcd\" | tail -3
'" 2>&1)
  if echo "$OUT" | grep -q "uptime="; then
    echo "== R6 BACK at try$i =="; echo "$OUT"
    break
  fi
  sleep 60
done
echo "WATCH_END $(date +%H:%M:%S)"
