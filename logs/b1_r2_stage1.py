#!/usr/bin/env python3
# B1 stage-1 executor (runs ON GZ as ubuntu). Secrets arrive via stdin JSON, memory only.
# Manual: docs/plans/全舰队国外流量分级限速与解封执行手册-v2-20261116.md §5.4 steps 3-7.
import json, subprocess, sys, urllib.request

API = "http://127.0.0.1:8080"
DEVICE_ID = "rtr-a9bcba"
NODE_PRIMARY = 8
NODE_BACKUP = 4

inp = json.loads(sys.stdin.read())
ADMIN_PW = inp["admin_pw"]
TOTP_SECRET = inp["totp_secret"]
ADMIN_USER = inp["admin_user"]

def post(path, payload, token=None, idem=None):
    req = urllib.request.Request(API + path, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    if idem:
        req.add_header("Idempotency-Key", idem)
    req.data = json.dumps(payload).encode()
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:200]
        return e.code, {"_error": body}

def totp_now(secret):
    import base64, hashlib, hmac, struct, time
    key = base64.b32decode(secret + "=" * ((8 - len(secret) % 8) % 8), casefold=True)
    counter = struct.pack(">Q", int(time.time()) // 30)
    d = hmac.new(key, counter, hashlib.sha1).digest()
    off = d[-1] & 0x0F
    return "%06d" % ((struct.unpack(">I", d[off:off+4])[0] & 0x7FFFFFFF) % 1000000)

out = {"steps": []}

# 1) login -> ticket
st, r = post("/api/v1/auth/login", {"username": ADMIN_USER, "password": ADMIN_PW})
out["steps"].append({"step": "login", "http": st, "need_totp": r.get("need_totp")})
if st != 200 or not r.get("ticket"):
    out["ok"] = False; out["fail"] = "login"; print(json.dumps(out)); sys.exit(1)
# 2) totp -> JWT
st, r = post("/api/v1/auth/totp", {"ticket": r["ticket"], "code": totp_now(TOTP_SECRET)})
out["steps"].append({"step": "totp", "http": st, "has_token": bool(r.get("token"))})
if st != 200 or not r.get("token"):
    out["ok"] = False; out["fail"] = "totp"; print(json.dumps(out)); sys.exit(1)
JWT = r["token"]

# 3) assignments/batch node8 primary, apply:false
st, r = post("/api/v1/assignments/batch",
             {"device_ids": [DEVICE_ID], "node_id": NODE_PRIMARY,
              "is_primary": True, "apply": False}, token=JWT)
results = r.get("items") or []
ok_flags = [x.get("ok") for x in results]
out["steps"].append({"step": "batch", "http": st, "ok_flags": ok_flags})
if st != 200 or not ok_flags or not all(ok_flags):
    out["ok"] = False; out["fail"] = "batch"; print(json.dumps(out)); sys.exit(1)

# 4) guarded SQL txn: keep only nodes 8(primary)+4(backup)
txn = f"""BEGIN;
SELECT id FROM devices WHERE id = '{DEVICE_ID}' FOR UPDATE;
INSERT INTO node_assignments(device_id, node_id, is_primary, failover_priority)
VALUES ('{DEVICE_ID}', {NODE_BACKUP}, false, 0)
ON CONFLICT (device_id, node_id) DO NOTHING;
DELETE FROM node_assignments
WHERE device_id = '{DEVICE_ID}' AND node_id NOT IN ({NODE_PRIMARY}, {NODE_BACKUP});
SELECT 1 / CASE WHEN count(*) = 2
  AND count(*) FILTER (WHERE node_id = {NODE_PRIMARY} AND is_primary) = 1
  AND count(*) FILTER (WHERE node_id = {NODE_BACKUP} AND NOT is_primary) = 1
  THEN 1 ELSE 0 END AS binding_guard
FROM node_assignments WHERE device_id = '{DEVICE_ID}';
COMMIT;"""
open("/tmp/b1-txn.sql", "w").write(txn)
p = subprocess.run(["sudo", "-u", "postgres", "psql", "routermgmt", "-v",
                    "ON_ERROR_STOP=1", "-f", "/tmp/b1-txn.sql"],
                   capture_output=True, text=True, timeout=30)
guard_ok = "binding_guard" in p.stdout and "| 1" in p.stdout.replace(" ", "").replace("\n", "") or " 1" in p.stdout.split("binding_guard")[-1] if "binding_guard" in p.stdout else False
out["steps"].append({"step": "sql_txn", "rc": p.returncode, "guard_ok": bool("binding_guard" in p.stdout and p.returncode == 0)})
if p.returncode != 0 or "binding_guard" not in p.stdout:
    out["ok"] = False; out["fail"] = "sql_txn"; print(json.dumps(out)); sys.exit(1)

# 5) preview + in-memory assertions
st, r = post(f"/api/v1/devices/{DEVICE_ID}/configs/preview", {}, token=JWT)
my = r.get("masked_yaml", "")
checks = {
    "http": st,
    "has_revision": bool(r.get("revision")),
    "next_version": r.get("next_version"),
    "proxies_count_2": my.count("  - name:") == 2 if my else False,
    "has_bwg_hy2": "BWG-HY2-JPTY-01" in my,
    "has_bwg_vless": "BWG-VLESS-JPTY-01" in my,
    "no_gia": ("154.36.139.155" not in my and "US666" not in my and "GIA-HY2" not in my),
    "no_cf": ("104.18.32.7" not in my and "162.159.135.42" not in my),
    "no_crlf": ("\r" not in my),
    "mgmt_direct_kept": "134.175.151.213" in my,
    "airport_provider_kept": "airport-sub" in my,
    "game_dns_n_a": True,
}
# NODE-PICK order must be BWG-HY2 first then BWG-VLESS
import re as _re
np = _re.search(r'- name: "NODE-PICK"\n(?:.*\n)*?    proxies:\n((?:      - .*\n)+)', my)
if np:
    members = [l.strip("- ").strip() for l in np.group(1).splitlines() if l.strip()]
    checks["nodepick_order"] = members
    checks["nodepick_8_first"] = members[:2] == ["BWG-HY2-JPTY-01", "BWG-VLESS-JPTY-01"]
out["steps"].append({"step": "preview", **checks})
passed = (checks["http"] == 200 and checks["has_revision"] and checks["proxies_count_2"]
          and checks["has_bwg_hy2"] and checks["has_bwg_vless"] and checks["no_gia"]
          and checks["no_cf"] and checks["no_crlf"] and checks["mgmt_direct_kept"]
          and checks["airport_provider_kept"] and checks.get("nodepick_8_first"))
out["ok"] = bool(passed)
out["revision"] = r.get("revision")
out["next_version"] = r.get("next_version")
out["current_version"] = r.get("current_version")
print(json.dumps(out, ensure_ascii=False))
