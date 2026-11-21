#!/bin/bash
# R5 切 GIA 循环补投：任务过期前设备没捡到就重投，成功（66云出现昌乐代理流量）即停
DM="ssh -p 28722 -i C:/Users/Administrator/.ssh/id_rsa_dmit -o IdentitiesOnly=yes -o ConnectTimeout=12 root@179.255.137.64"
for i in $(seq 1 30); do
  # 成功检测：66云 443 上出现昌乐 IP
  C=$(ssh -i /root/.ssh/id_ed25519_666 -o BatchMode=yes root@154.36.137.54 "ss -tn state established '( sport = :443 )' | grep -c 39.90.84.192" 2>/dev/null)
  if [ "${C:-0}" -ge 3 ]; then echo "SUCCESS try$i: 昌乐代理流量已上66云 ($C 条) $(date +%H:%M:%S)"; break; fi
  python - <<'PYEOF'
import sys, requests, urllib3
sys.path.insert(0, r"F:\vps\scripts")
import mgmt_api_test as M
urllib3.disable_warnings(); M.ensure_tunnel()
r = requests.post(M.BASE + "/api/v1/assignments/batch", headers=M.H(),
                  json={"device_ids": ["rtr-c46a25"], "node_id": 7, "is_primary": True, "apply": True}, verify=False, timeout=30)
print("submitted", r.json().get("items",[{}])[0].get("task_id","?"))
PYEOF
  sleep 140
done
echo "LOOP_END $(date +%H:%M:%S)"
