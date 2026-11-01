#!/usr/bin/env python3
# 雅典娜（JDCloud RE-CS-02 AX6600）管理面迁移 DMIT：录入 + enroll 一次性脚本（2026-10-10）
# 复用 flash-snr.py 的录入路径：devices 创建 → enroll_code → enroll 引导包
# 引导包落 backups/.athena-enroll.json（600，gitignored），部署后由设备侧 agent.conf 消费
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..") if False else None
sys.path.insert(0, r"F:\vps\scripts")
import mgmt_api_test as M
import requests, urllib3
urllib3.disable_warnings()

M.ensure_tunnel()
H = {"Authorization": "Bearer " + M.token()}

r = requests.post(M.BASE + "/api/v1/devices", headers=H,
                  json={"name": "雅典娜-AX6600", "hw_model": "JDCloud-RE-CS-02-AX6600-LibWrt"},
                  verify=False, timeout=15)
r.raise_for_status()
dev = r.json()
print("device_id:", dev["id"], "name:", dev["name"])

r = requests.post(M.BASE + "/api/v1/devices/enroll",
                  json={"enroll_code": dev["enroll_code"]}, verify=False, timeout=15)
r.raise_for_status()
pkg = r.json()
pkg["mgmt_url"] = "https://127.0.0.1:18443"
out = r"F:\vps\backups\.athena-enroll.json"
open(out, "w").write(json.dumps(pkg, ensure_ascii=False, indent=1))
os.chmod(out, 0o600)
print("enroll ok ->", out)
print("keys:", sorted(pkg.keys()))
