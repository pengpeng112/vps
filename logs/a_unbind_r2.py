#!/usr/bin/env python3
# A2-A4 executor (runs ON GZ): unbind node14 from R2, guarded txn, preview assertions.
# Secrets via stdin JSON, memory only. Manual: Codex 执行单 A.3/A.4.
import json, subprocess, sys, urllib.request

API = "http://127.0.0.1:8080"
DEVICE_ID = "rtr-a9bcba"
KEEP_PRIMARY = 8
KEEP_BACKUP = 4
REMOVE_NODE = 14

inp = json.loads(sys.stdin.read())
led = inp

def post(path, payload, token=None):
    req = urllib.request.Request(API + path, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    req.data = json.dumps(payload).encode()
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, {"_error": e.read().decode(errors="replace")[:250]}

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
    out["ok"] = False; out["fail"] = "login"; print(json.dumps(out)); sys.exit(1)
st, r = post("/api/v1/auth/totp", {"ticket": r["ticket"], "code": totp_now(led["totp_secret"])})
out["steps"].append({"step": "totp", "http": st, "has_token": bool(r.get("token"))})
if st != 200 or not r.get("token"):
    out["ok"] = False; out["fail"] = "totp"; print(json.dumps(out)); sys.exit(1)
JWT = r["token"]

# guarded txn: remove node14 binding, keep exactly 8(primary)+4(backup)
txn = f"""BEGIN;
SELECT id FROM devices WHERE id = '{DEVICE_ID}' FOR UPDATE;
DELETE FROM node_assignments
WHERE device_id = '{DEVICE_ID}' AND node_id = {REMOVE_NODE};
SELECT 1 / CASE WHEN count(*) = 2
  AND count(*) FILTER (WHERE node_id = {KEEP_PRIMARY} AND is_primary) = 1
  AND count(*) FILTER (WHERE node_id = {KEEP_BACKUP} AND NOT is_primary) = 1
  THEN 1 ELSE 0 END AS binding_guard
FROM node_assignments WHERE device_id = '{DEVICE_ID}';
COMMIT;"""
open("/tmp/a-unbind.sql", "w").write(txn)
p = subprocess.run(["sudo", "-u", "postgres", "psql", "routermgmt", "-v",
                    "ON_ERROR_STOP=1", "-f", "/tmp/a-unbind.sql"],
                   capture_output=True, text=True, timeout=30)
out["steps"].append({"step": "sql_unbind", "rc": p.returncode,
                     "guard": "binding_guard" in p.stdout})
if p.returncode != 0 or "binding_guard" not in p.stdout:
    out["ok"] = False; out["fail"] = "sql_unbind"; print(json.dumps(out)); sys.exit(1)

# preview with strict assertions
st, r = post(f"/api/v1/devices/{DEVICE_ID}/configs/preview", {}, token=JWT)
my = r.get("masked_yaml", "")
import re
checks = {
    "http": st,
    "has_revision": bool(r.get("revision")),
    "proxies_exact_2": len(re.findall(r'^  - name: "', my.split('proxy-groups:')[0], re.M)) == 2,
    "names": re.findall(r'^  - name: "([^"]+)"', my, re.M),
    "no_bulk": "BULK" not in my,
    "no_gia": ("154.36.139.155" not in my and "US666" not in my and "GIA-HY2" not in my),
    "no_cf": ("104.18.32.7" not in my and "162.159.135.42" not in my and "2054" not in my and "2053" not in my),
    "no_crlf": "\r" not in my,
    "mgmt_kept": "134.175.151.213" not in my or True,  # v32 base has no GZ-IP rule; keep info only
    "airport_kept": "airport-sub" in my,
    "p0_gamedns_n_a": True,  # dnsmasq side, not in mihomo yaml
}
np_m = re.search(r'- name: "NODE-PICK"\n    type: \w+\n(?:    [^\n]*\n)*?    proxies:\n((?:      - [^\n]+\n)+)', my)
if np_m:
    members = [l.strip("- ").strip() for l in np_m.group(1).splitlines() if l.strip()]
    checks["nodepick_members"] = members
    checks["nodepick_ok"] = members == ["BWG-HY2-JPTY-01", "BWG-VLESS-JPTY-01"]
us_m = re.search(r'- name: "US-MEDIA"\n    type: \w+\n(?:    [^\n]*\n)*?    proxies:\n((?:      - [^\n]+\n)+)', my)
if us_m:
    umembers = [l.strip("- ").strip() for l in us_m.group(1).splitlines() if l.strip()]
    checks["usmedia_members"] = umembers
    checks["usmedia_can_pick_nodepick"] = "NODE-PICK" in umembers
out["steps"].append({"step": "preview", **checks})
passed = (checks["http"] == 200 and checks["has_revision"] and checks["proxies_exact_2"]
          and checks["no_bulk"] and checks["no_gia"] and checks["no_cf"] and checks["no_crlf"]
          and checks["airport_kept"] and checks.get("nodepick_ok")
          and checks.get("usmedia_can_pick_nodepick"))
out["ok"] = bool(passed)
out["revision"] = r.get("revision")
out["next_version"] = r.get("next_version")
out["current_version"] = r.get("current_version")
print(json.dumps(out, ensure_ascii=False))
