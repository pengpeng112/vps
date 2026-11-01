#!/bin/bash
# R1/R3 循环补投：线路给窗口即自动切66云（含 istarshine 直连规则）
for i in $(seq 1 60); do
  python - <<'PYEOF' 2>/dev/null
import sys, requests, urllib3
sys.path.insert(0, r"F:\vps\scripts")
import mgmt_api_test as M
urllib3.disable_warnings(); M.ensure_tunnel()
for devid in ["rtr-d3847d", "rtr-38aa54"]:
    try:
        requests.post(M.BASE + "/api/v1/assignments/batch", headers=M.H(),
                      json={"device_ids": [devid], "node_id": 7, "is_primary": True, "apply": True}, verify=False, timeout=20)
        print("submitted", devid)
    except Exception as e:
        print("err", devid, str(e)[:40])
PYEOF
  sleep 150
done
