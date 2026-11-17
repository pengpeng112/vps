import json
import urllib.request
import base64
import hashlib
import hmac
import struct
import time
import re

API = "http://127.0.0.1:8080"
inp = json.loads(open("/tmp/b2-pre.json").read())

def post(path, payload, token=None):
    req = urllib.request.Request(API + path, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    req.data = json.dumps(payload).encode()
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode())

def totp_now(sec):
    key = base64.b32decode(sec + "=" * ((8 - len(sec) % 8) % 8), casefold=True)
    d = hmac.new(key, struct.pack(">Q", int(time.time()) // 30), hashlib.sha1).digest()
    off = d[-1] & 0x0F
    return "%06d" % ((struct.unpack(">I", d[off:off+4])[0] & 0x7FFFFFFF) % 1000000)

r = post("/api/v1/auth/login", {"username": inp["admin_user"], "password": inp["admin_pw"]})
r = post("/api/v1/auth/totp", {"ticket": r["ticket"], "code": totp_now(inp["totp_secret"])})
tok = r["token"]
r = post("/api/v1/devices/rtr-a9bcba/configs/preview", {}, token=tok)
my = r.get("masked_yaml", "")
lines = my.splitlines()
found = False
for i, l in enumerate(lines):
    if "US-MEDIA" in l:
        print("--- context ---")
        print("\n".join(lines[max(0, i - 1):i + 10]))
        found = True
        break
print("--- regex types:", re.findall(r'- name: "US-MEDIA"\s*\n\s*type: (\w+)', my))
print("--- select count:", my.count("type: select"), "| fallback count:", my.count("type: fallback"))
print("--- has_bulk:", "BULK-LAX-GIA-01" in my)
print("--- revision:", r.get("revision"), "next:", r.get("next_version"), "cur:", r.get("current_version"))
open("/tmp/b1-rev.json", "w").write(json.dumps({"revision": r.get("revision")}))
