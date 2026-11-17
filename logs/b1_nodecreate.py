#!/usr/bin/env python3
# B1/E3 node-create executor (runs ON GZ). Secrets via stdin JSON, memory only.
import json, subprocess, sys, urllib.request

API = "http://127.0.0.1:8080"
DEVICE_ID = "rtr-a9bcba"

inp = json.loads(sys.stdin.read())
NP = inp["node_params"]
led = inp  # admin creds + node_params in one payload

def post(path, payload, token=None):
    req = urllib.request.Request(API + path, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    req.data = json.dumps(payload).encode()
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, {"_error": e.read().decode(errors="replace")[:200]}

def totp_now(secret):
    import base64, hashlib, hmac, struct, time
    key = base64.b32decode(secret + "=" * ((8 - len(secret) % 8) % 8), casefold=True)
    counter = struct.pack(">Q", int(time.time()) // 30)
    d = hmac.new(key, counter, hashlib.sha1).digest()
    off = d[-1] & 0x0F
    return "%06d" % ((struct.unpack(">I", d[off:off+4])[0] & 0x7FFFFFFF) % 1000000)

out = {"steps": []}
st, r = post("/api/v1/auth/login", {"username": led["admin_user"], "password": led["admin_pw"]})
out["steps"].append({"step": "login", "http": st})
if st != 200 or not r.get("ticket"):
    out["ok"] = False; print(json.dumps(out)); sys.exit(1)
st, r = post("/api/v1/auth/totp", {"ticket": r["ticket"], "code": totp_now(led["totp_secret"])})
out["steps"].append({"step": "totp", "http": st, "has_token": bool(r.get("token"))})
if st != 200 or not r.get("token"):
    out["ok"] = False; print(json.dumps(out)); sys.exit(1)
JWT = r["token"]

# create BULK node (vless-reality :2054, name contains LAX -> auto US-MEDIA member)
payload = {
    "name": "BULK-LAX-GIA-01",
    "host": "154.36.139.155",
    "port": 2054,
    "proto": "vless-reality",
    "params": {
        "uuid": NP["uuid"], "pbk": NP["pbk"], "sni": NP["sni"],
        "sid": NP["sid"], "flow": NP["flow"], "fp": NP["fp"],
    },
    "enabled": True,
}
st, r = post("/api/v1/nodes", payload, token=JWT)
out["steps"].append({"step": "node_create", "http": st, "resp": str(r)[:400]})
new_id = None
if isinstance(r, dict):
    new_id = r.get("id") or r.get("node", {}).get("id") if isinstance(r.get("node"), dict) else r.get("id")
if st not in (200, 201) or not new_id:
    out["ok"] = False; out["fail"] = "node_create"; print(json.dumps(out)); sys.exit(1)

# bind to R2 (non-primary)
st, r = post("/api/v1/assignments/batch",
             {"device_ids": [DEVICE_ID], "node_id": int(new_id), "is_primary": False, "apply": False}, token=JWT)
items = r.get("items") or []
ok_flags = [x.get("ok") for x in items]
out["steps"].append({"step": "bind", "http": st, "ok_flags": ok_flags, "new_node_id": new_id})
if st != 200 or not ok_flags or not all(ok_flags):
    out["ok"] = False; out["fail"] = "bind"; print(json.dumps(out)); sys.exit(1)

out["ok"] = True
out["new_node_id"] = int(new_id)
print(json.dumps(out, ensure_ascii=False))
