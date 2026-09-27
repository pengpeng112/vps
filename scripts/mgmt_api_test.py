"""SN-R1 远程控制测试驱动 —— 经本机 SSH 隧道调管理台 API。
用法: python mgmt_api_test.py <子命令>
  login      打印 token（有效期内的复用 F:\\vps\\backups\\.mgmt-token）
  state      设备当前状态
  op <type> <json参数>   下发任务并等完成，打印任务结果
  tasks      最近任务列表
  patch <json>           PATCH 设备字段（改名/时间）
"""
import base64, hashlib, hmac, json, struct, subprocess, sys, time, uuid
import requests, urllib3
urllib3.disable_warnings()

BASE = "https://127.0.0.1:18444"
# 2026-09-27 M05：源码不再内嵌口令/TOTP——运行时从根目录台账读取；缺台账立即失败。
LEDGER = r"F:\vps\登录信息与配置总表-20260917.md"
def _ledger_pw():
    try:
        src = open(LEDGER, encoding="utf-8").read()
    except OSError as e:
        raise SystemExit(f"缺凭据台账，拒绝运行（M05 受控来源）: {e}")
    try:
        return (src.split("| 密码 | `")[1].split("`")[0],
                src.split("TOTP 密钥（重绑验证器用） | `")[1].split("`")[0])
    except IndexError:
        raise SystemExit("台账结构变化，无法定位管理台口令/TOTP 字段")
ADMIN_PW, TOTP_SECRET = _ledger_pw()
import os
DEV = os.environ.get("RM_DEV", "rtr-d3847d")
TOKEN_FILE = r"F:\vps\backups\.mgmt-token"

def totp():
    key = base64.b32decode(TOTP_SECRET + "=" * ((8 - len(TOTP_SECRET) % 8) % 8), casefold=True)
    counter = struct.pack(">Q", int(time.time()) // 30)
    d = hmac.new(key, counter, hashlib.sha1).digest()
    off = d[-1] & 0x0F
    return f"{(struct.unpack('>I', d[off:off+4])[0] & 0x7FFFFFFF) % 1000000:06d}"

def ensure_tunnel():
    # 本地 18444 -> VPS 10.10.0.1:8443（独立于用户的管理台通道）
    try:
        requests.get(BASE + "/api/v1/health", verify=False, timeout=4)
        return
    except Exception:
        pass
    subprocess.Popen([r"D:\Program Files\Git\usr\bin\ssh.exe", "-f", "-N", "-p", "28722",
                      "-i", "C:/Users/Administrator/.ssh/id_ed25519_digitalocean",
                      "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
                      "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=30",
                      "-L", "127.0.0.1:18444:10.10.0.1:8443", "root@159.223.36.119"],
                     creationflags=0x00000008)
    for _ in range(10):
        try:
            requests.get(BASE + "/api/v1/health", verify=False, timeout=3)
            return
        except Exception:
            time.sleep(1)
    raise SystemExit("隧道建不上")

def token():
    try:
        t, ts = json.load(open(TOKEN_FILE))
        if time.time() - ts < 3600:
            return t
    except Exception:
        pass
    r = requests.post(BASE + "/api/v1/auth/login",
                      json={"username": "admin", "password": ADMIN_PW}, verify=False, timeout=10).json()
    ticket = r.get("ticket") or r.get("data", {}).get("ticket")
    r2 = requests.post(BASE + "/api/v1/auth/totp", json={"ticket": ticket, "code": totp()},
                       verify=False, timeout=10).json()
    tok = r2.get("token") or r2.get("data", {}).get("token") or r2.get("access_token")
    if not tok:
        print("login resp:", r, r2); raise SystemExit("无 token")
    json.dump([tok, time.time()], open(TOKEN_FILE, "w"))
    return tok

def H():
    return {"Authorization": "Bearer " + token()}

def device():
    r = requests.get(BASE + f"/api/v1/devices/{DEV}", headers=H(), verify=False, timeout=10)
    d = r.json()
    return d.get("device") or d.get("data") or d

def wait_task(tid, timeout=180):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = requests.get(BASE + "/api/v1/tasks?limit=20", headers=H(), verify=False, timeout=10).json()
        items = r.get("tasks") or r.get("data") or r.get("items") or []
        for it in items:
            if it.get("id") == tid or it.get("task_id") == tid:
                if it.get("status") in ("succeeded", "ok", "done", "completed", "failed", "unknown", "cancelled", "timeout"):
                    return it
        time.sleep(3)
    return {"status": "timeout", "id": tid}

def op(ttype, params):
    r = requests.post(BASE + f"/api/v1/devices/{DEV}/ops",
                      headers={**H(), "Idempotency-Key": str(uuid.uuid4())},
                      json={"type": ttype, "params": params}, verify=False, timeout=10)
    print("下发:", ttype, params, "->", r.status_code, r.text[:150])
    if r.status_code != 200:
        return None
    tid = r.json().get("task_id")
    res = wait_task(tid)
    print("任务:", tid, "状态:", res.get("status"),
          "| 结果:", json.dumps(res.get("result") or res.get("result_summary") or {}, ensure_ascii=False)[:300])
    return res

if __name__ == "__main__":
    ensure_tunnel()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "state"
    if cmd == "login":
        print("token OK:", token()[:20] + "...")
    elif cmd == "state":
        d = device()
        keys = ["id", "name", "online", "last_heartbeat_at", "proxy_mode", "access_mode", "access_list",
                "vps_limit_kbps", "reboot_cron", "license_enabled", "license_expires_at",
                "current_node_name", "current_config_version", "desired_config_version"]
        print(json.dumps({k: d.get(k) for k in keys}, ensure_ascii=False, indent=1, default=str))
    elif cmd == "op":
        op(sys.argv[2], json.loads(sys.argv[3]) if len(sys.argv) > 3 else {})
    elif cmd == "tasks":
        r = requests.get(BASE + "/api/v1/tasks?limit=10", headers=H(), verify=False, timeout=10).json()
        items = r.get("tasks") or r.get("data") or []
        for it in items[:10]:
            print(it.get("created_at", ""), it.get("type"), it.get("status"),
                  json.dumps(it.get("result") or {}, ensure_ascii=False)[:120])
    elif cmd == "patch":
        body = json.loads(sys.argv[2])
        r = requests.patch(BASE + f"/api/v1/devices/{DEV}", headers=H(), json=body, verify=False, timeout=10)
        print(r.status_code, r.text[:200])
