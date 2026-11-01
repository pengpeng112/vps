# -*- coding: utf-8 -*-
"""腾讯节点 IP 迁移：43.134.5.101 -> 43.134.46.44
1) PATCH 节点 host  2) 重新发布双节点配置(assignments/batch apply)
3) 等 switch_node 完成  4) set_proxy_state enabled=true 把 PROXY 切回 AUTO（双线自动选择）
"""
import json, sys, time, uuid
sys.path.insert(0, r"F:\vps\scripts")
import mgmt_api_test as M
import requests

H = {"Authorization": "Bearer " + M.token()}
NEW_IP = "43.134.46.44"

# 1) PATCH host
r = requests.patch(M.BASE + "/api/v1/nodes/2", headers=H, json={"host": NEW_IP}, verify=False, timeout=15)
print("patch node:", r.status_code, r.text[:200])
assert r.status_code == 200 and r.json()["host"] == NEW_IP, "PATCH 失败"

# 2) 重新发布（内容变化→新版本+switch_node 任务）
r = requests.get(M.BASE + "/api/v1/devices", headers=H, verify=False, timeout=15).json()
devs = [d["id"] for d in (r.get("items") or [])]
r = requests.post(M.BASE + "/api/v1/assignments/batch", headers=H,
                  json={"device_ids": devs, "node_id": 2, "is_primary": False, "apply": True},
                  verify=False, timeout=30).json()
print("republish:", json.dumps(r, ensure_ascii=False))
tasks = {i["device_id"]: i["task_id"] for i in r["items"]}

# 3) 等 switch_node 完成（只认终态；running/claimed 不是终态）
def wait_task(tid, timeout=200):
    FINAL = ("ok", "failed", "rolled_back", "expired", "timeout")
    t0 = time.time()
    while time.time() - t0 < timeout:
        rr = requests.get(M.BASE + "/api/v1/tasks", headers=H, verify=False, timeout=15).json()
        t = next((x for x in (rr.get("items") or []) if x["id"] == tid), None)
        if t and t["status"] in FINAL:
            return t
        time.sleep(8)
    return None

for dev, tid in tasks.items():
    t = wait_task(tid)
    print(f"switch_node {dev}: {t['status'] if t else 'TIMEOUT'}", json.dumps(t.get("result") if t else {}, ensure_ascii=False)[:200])

# 4) set_proxy_state enabled=true → PROXY→AUTO
for dev in devs:
    r = requests.post(M.BASE + f"/api/v1/devices/{dev}/ops", headers={**H, "Idempotency-Key": str(uuid.uuid4())},
                      json={"type": "set_proxy_state", "params": {"enabled": True}}, verify=False, timeout=15).json()
    tid = r.get("task_id")
    t = wait_task(tid, timeout=120)
    print(f"set_proxy_state {dev}: {t['status'] if t else 'TIMEOUT'}", json.dumps(t.get("result") if t else {}, ensure_ascii=False)[:200])
