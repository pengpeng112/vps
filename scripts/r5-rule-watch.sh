#!/bin/bash
# R5 回线哨兵：一检测到 13225 监听即重发布配置（含 istarshine 直连规则）并验证
DM="ssh -p 28722 -i C:/Users/Administrator/.ssh/id_rsa_dmit -o IdentitiesOnly=yes -o ConnectTimeout=12 root@179.255.137.64"
for i in $(seq 1 240); do
  L=$($DM 'ss -tln | grep -c 13225' 2>/dev/null | tail -1)
  if [ "$L" = "1" ]; then
    echo "== R5 回线 try$i $(date +%H:%M:%S) =="
    python - <<'PYEOF'
import sys, time, subprocess, requests, urllib3
sys.path.insert(0, r"F:\vps\scripts")
import mgmt_api_test as M
urllib3.disable_warnings(); M.ensure_tunnel(); H = M.H()
DM = ['ssh','-p','28722','-i','C:/Users/Administrator/.ssh/id_rsa_dmit','-o','IdentitiesOnly=yes','root@179.255.137.64']
r = requests.post(M.BASE + "/api/v1/assignments/batch", headers=H,
                  json={"device_ids": ["rtr-c46a25"], "node_id": 4, "is_primary": True, "apply": True}, verify=False, timeout=30)
tid = r.json()["items"][0]["task_id"]
t0 = time.time(); st = "?"
while time.time() - t0 < 180:
    tl = requests.get(M.BASE + "/api/v1/tasks?page_size=100", headers=H, verify=False, timeout=15).json()
    t = next((x for x in (tl.get("items") or []) if x["id"] == tid), None)
    if t and t["status"] in ("ok","failed","rolled_back","expired","timeout"): st = t["status"]; break
    time.sleep(8)
print("重发布:", st)
time.sleep(6)
out = subprocess.run(DM + ["sshpass -p '[REDACTED]' ssh -o StrictHostKeyChecking=no -o ConnectTimeout=12 -p 13225 root@127.0.0.1 'grep -c istarshine /etc/mihomo/config.yaml; curl -s -m 10 -o /dev/null -w %{http_code} https://yqms.istarshine.com/v4/login; echo; curl -s -m 8 -x http://127.0.0.1:7890 https://api.ipify.org 2>/dev/null'"],
                     capture_output=True, text=True, timeout=90).stdout.strip()
print("R5 规则/站点/出口:", out.replace(chr(10), " / "))
sql = ("UPDATE node_assignments SET failover_priority=0 WHERE device_id='rtr-c46a25' AND node_id=4; "
       "UPDATE node_assignments SET failover_priority=1 WHERE device_id='rtr-c46a25' AND node_id=7; "
       "UPDATE node_assignments SET failover_priority=2 WHERE device_id='rtr-c46a25' AND node_id=3; "
       "UPDATE node_assignments SET failover_priority=3 WHERE device_id='rtr-c46a25' AND node_id=2;")
subprocess.run(DM + ['docker exec routermgmt-db-1 psql -U postgres -d routermgmt -c "' + sql + '"'], capture_output=True, timeout=60)
print("优先级已复原")
PYEOF
    break
  fi
  sleep 30
done
echo "WATCH_END $(date +%H:%M:%S)"
