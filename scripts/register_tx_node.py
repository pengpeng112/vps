# -*- coding: utf-8 -*-
"""注册腾讯节点 VPS-TX-SG-02 并绑定到两台设备（secondary，apply=true 触发发布）。"""
import json, sys, uuid
sys.path.insert(0, r"F:\vps\scripts")
import mgmt_api_test as M
import requests

H = {"Authorization": "Bearer " + M.token()}

# 0) 设备清单（拿全两台 device_id）
r = requests.get(M.BASE + "/api/v1/devices", headers=H, verify=False, timeout=15).json()
devs = [d["id"] for d in (r.get("items") or r.get("data", {}).get("items") or [])]
print("devices:", devs)
if len(devs) < 2:
    raise SystemExit("设备数不对，先人工确认")

# 1) 节点是否已存在
r = requests.get(M.BASE + "/api/v1/nodes", headers=H, verify=False, timeout=15).json()
nodes = r.get("items") or []
exist = [n for n in nodes if n["name"] == "VPS-TX-SG-02"]
if exist:
    node_id = exist[0]["id"]
    print("node exists:", exist[0])
else:
    body = {
        "name": "VPS-TX-SG-02",
        "host": "43.134.46.44",
        "port": 443,
        "proto": "vless-reality",
        "params": {
            "uuid": "[REDACTED]",
            "pbk": "Y5v_pI1qCOPhiLj-X_x5D4aRKfnXnK-LSe9vybWxhnE",
            "sid": "0a1e1b8a77988e8c",
            "sni": "www.microsoft.com",
            "flow": "xtls-rprx-vision",
            "fp": "chrome",
        },
        "priority": 50,
        "enabled": True,
    }
    r = requests.post(M.BASE + "/api/v1/nodes", headers=H, json=body, verify=False, timeout=15)
    print("create node:", r.status_code, r.text[:300])
    node_id = r.json()["id"]

# 2) 绑定两台设备：secondary + apply（发布双节点配置并生成 switch_node 任务）
body = {"device_ids": devs, "node_id": node_id, "is_primary": False, "apply": True}
r = requests.post(M.BASE + "/api/v1/assignments/batch", headers=H, json=body, verify=False, timeout=30)
print("bind+apply:", r.status_code, json.dumps(r.json(), ensure_ascii=False)[:600])
