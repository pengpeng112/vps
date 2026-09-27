"""M02 发布客户端：经 PC 本地 18444 通道调管理台 API 发布配置到指定设备。
凭据内存使用，不打印 token/密码。用法: python publish.py <device_id> [dry]
"""
import base64, hashlib, hmac, json, struct, sys, time, uuid
import requests, urllib3
urllib3.disable_warnings()

BASE = "https://127.0.0.1:18444"
ADMIN_PW = None
TOTP_SECRET = None


def load_creds():
    global ADMIN_PW, TOTP_SECRET
    src = open('F:/vps/登录信息与配置总表-20260917.md', encoding='utf-8').read()
    ADMIN_PW = src.split('| 密码 | `')[1].split('`')[0]
    TOTP_SECRET = src.split('TOTP 密钥（重绑验证器用） | `')[1].split('`')[0]


def totp():
    key = base64.b32decode(TOTP_SECRET + "=" * ((8 - len(TOTP_SECRET) % 8) % 8), casefold=True)
    counter = struct.pack(">Q", int(time.time()) // 30)
    d = hmac.new(key, counter, hashlib.sha1).digest()
    off = d[-1] & 0x0F
    return f"{(struct.unpack('>I', d[off:off+4])[0] & 0x7FFFFFFF) % 1000000:06d}"


def token():
    for _ in range(2):
        r = requests.post(BASE + "/api/v1/auth/login", json={"username": "admin", "password": ADMIN_PW},
                          verify=False, timeout=10).json()
        ticket = r.get("ticket") or r.get("data", {}).get("ticket")
        r2 = requests.post(BASE + "/api/v1/auth/totp", json={"ticket": ticket, "code": totp()},
                           verify=False, timeout=10).json()
        tok = r2.get("token") or r2.get("data", {}).get("token")
        if tok:
            return tok
        time.sleep(1)
    raise SystemExit("login failed")


def H(t):
    return {"Authorization": "Bearer " + t, "Idempotency-Key": "m02-" + uuid.uuid4().hex[:12]}


def wait_task(t, tid, timeout=240):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = requests.get(BASE + "/api/v1/tasks?limit=20&device_id=" + DEV, headers={"Authorization": "Bearer " + t},
                         verify=False, timeout=10).json()
        items = r.get("tasks") or r.get("data") or r.get("items") or []
        for it in items:
            if it.get("id") == tid:
                st = it.get("status")
                if st in ("success", "failed", "expired", "rolled_back", "rollback_failed"):
                    return st, it.get("result")
        time.sleep(4)
    return "timeout", None


if __name__ == '__main__':
    DEV = sys.argv[1]
    dry = len(sys.argv) > 2 and sys.argv[2] == 'dry'
    load_creds()
    t = token()
    print("login ok")
    pv = requests.post(BASE + f"/api/v1/devices/{DEV}/configs/preview", headers=H(t), verify=False, timeout=15).json()
    d = pv.get("data") or pv
    print("next_version:", d.get("next_version"), "sha256:", str(d.get("sha256"))[:16])
    for line in (d.get("diff") or []):
        print("  diff:", line.get("op"), line.get("line"))
    rev = d.get("revision")
    if dry or not rev:
        print("dry stop"); sys.exit(0)
    ap = requests.post(BASE + f"/api/v1/devices/{DEV}/configs/apply", json={"revision": rev},
                       headers=H(t), verify=False, timeout=20).json()
    print("apply:", json.dumps({k: ap.get(k) for k in ('ok', 'task_id', 'version', 'error', 'message')}, ensure_ascii=False))
    tid = ap.get("task_id") or (ap.get("data") or {}).get("task_id")
    if not tid:
        sys.exit(1)
    st, res = wait_task(t, tid)
    print("task:", tid, "status:", st)
    print("result:", json.dumps(res, ensure_ascii=False)[:500] if res else None)
