import json
import time
import urllib.request
import base64
import hashlib
import hmac
import struct

API = "http://127.0.0.1:8080"
inp = json.loads(open("/tmp/b2-pre.json").read())
REV = json.loads(open("/tmp/b1-rev.json").read())["revision"]

def post(path, payload, token=None, idem=None):
    req = urllib.request.Request(API + path, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    if idem:
        req.add_header("Idempotency-Key", idem)
    req.data = json.dumps(payload).encode()
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode())

def totp_at(sec, step):
    key = base64.b32decode(sec + "=" * ((8 - len(sec) % 8) % 8), casefold=True)
    d = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    off = d[-1] & 0x0F
    return "%06d" % ((struct.unpack(">I", d[off:off+4])[0] & 0x7FFFFFFF) % 1000000)

# login
r = post("/api/v1/auth/login", {"username": inp["admin_user"], "password": inp["admin_pw"]})
ticket = r["ticket"]
# TOTP with window-wait retry (avoids same-step replay rejection)
tok = None
for attempt in range(4):
    step = int(time.time()) // 30
    code = totp_at(inp["totp_secret"], step)
    try:
        r = post("/api/v1/auth/totp", {"ticket": ticket, "code": code})
        tok = r.get("token")
        print("totp attempt", attempt + 1, "ok")
        break
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:100]
        print("totp attempt", attempt + 1, "http", e.code, body[:60])
        if attempt < 3:
            time.sleep(32)
if not tok:
    raise SystemExit("TOTP failed after retries")

idem = "b1-r2-bulk-v34"
r = post("/api/v1/devices/rtr-a9bcba/configs/apply", {"revision": REV}, token=tok, idem=idem)
print("APPLY_TASK:", r.get("task_id"), r.get("version"))
