"""DMIT-HY2 third-leg enablement (user-authorized 2026-11-18): UFW done separately.

Flow: read hysteria2 password from DMIT (memory only) -> GZ API login (TOTP)
-> create node DMIT-HY2-US-01 (hysteria2, 69.63.219.156:34567, purpose=ordinary)
-> bind to R2 (non-primary, no apply) -> preview -> apply (Idempotency-Key)
-> poll task -> device verify (node alive/delay via mihomo API, groups unchanged).
R2 self-heal paused during publish per prior-session practice. No secrets printed.
"""
import json
import os
import re
import sys
import time
from pathlib import Path

import paramiko

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(".").resolve()
BT = chr(96)
NODE_NAME = "DMIT-HY2-US-01"


def controlled_row(path, name):
    for line in (ROOT / path).read_text(encoding="utf-8-sig").splitlines():
        cells = [x.strip() for x in line.strip().strip("|").split("|")]
        if line.startswith("|") and cells[0] == name:
            return "|".join(cells[1:])
    raise ValueError("missing field %s" % name)


def quoted(v):
    return re.findall(BT + "([^" + BT + "]+)" + BT, v)


PW = quoted(controlled_row("backups/SN-R系列凭证总览.md", "**root/SSH 口令**"))[0]
led = (ROOT / "登录信息与配置总表-20260917.md").read_text(encoding="utf-8")
creds = {
    "admin_user": led.split("| 用户名 | `")[1].split("`")[0],
    "admin_pw": led.split("| 密码 | `")[1].split("`")[0],
    "totp_secret": led.split("TOTP 密钥（重绑验证器用） | `")[1].split("`")[0],
}


def ssh_pw(host, port, user, pw, jump=None, tmo=15):
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    sock = jump.get_transport().open_channel("direct-tcpip", (host, port), ("127.0.0.1", 0), timeout=tmo) if jump else None
    c.connect(host, port=port, username=user, password=pw, sock=sock,
              timeout=tmo, banner_timeout=tmo, auth_timeout=tmo, look_for_keys=False, allow_agent=False)
    return c


# ---- 1) DMIT hysteria2 password (memory only) ----
pkey = None
for cls in (paramiko.RSAKey, paramiko.Ed25519Key, paramiko.ECDSAKey):
    try:
        pkey = cls.from_private_key_file("VPS远程工具包/id_rsa_dmit")
        break
    except Exception:
        continue
hypass = None
for attempt in range(4):
    try:
        dm = paramiko.SSHClient()
        dm.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        dm.connect("69.63.219.156", 22, username="root", pkey=pkey, timeout=15, banner_timeout=15, look_for_keys=False, allow_agent=False)
        i, o, e = dm.exec_command("python3 -c \"import yaml;print(yaml.safe_load(open('/etc/hysteria/config.yaml'))['auth']['password'])\"", timeout=15)
        hypass = o.read().decode().strip()
        dm.close()
        break
    except Exception as ex:
        print("dmit retry", attempt, type(ex).__name__)
        time.sleep(4)
assert hypass and re.fullmatch(r"[A-Za-z0-9_-]{16,64}", hypass), "hysteria password read/charset failed"
print("dmit hysteria2 password: read ok (len=%d, charset ok)" % len(hypass))

gz = ssh_pw("134.175.151.213", 28722, "ubuntu", PW)

GZ_SCRIPT = r'''
import json, urllib.request, urllib.error, base64, hashlib, hmac, struct, time
API = "http://127.0.0.1:8080"
inp = json.loads(open("/tmp/dm2-input.json").read())
def post(path, payload, token=None, idem=None, method="POST"):
    req = urllib.request.Request(API + path, method=method)
    req.add_header("Content-Type", "application/json")
    if token: req.add_header("Authorization", "Bearer " + token)
    if idem: req.add_header("Idempotency-Key", idem)
    req.data = json.dumps(payload).encode()
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try: return {"_http": e.code, "_body": e.read().decode()[:300]}
        except Exception: return {"_http": e.code}
def totp_now(sec):
    key = base64.b32decode(sec + "=" * ((8 - len(sec) % 8) % 8), casefold=True)
    d = hmac.new(key, struct.pack(">Q", int(time.time()) // 30), hashlib.sha1).digest()
    off = d[-1] & 0x0F
    return "%06d" % ((struct.unpack(">I", d[off:off+4])[0] & 0x7FFFFFFF) % 1000000)

tok = None
r = post("/api/v1/auth/login", {"username": inp["admin_user"], "password": inp["admin_pw"]})
ticket = r.get("ticket")
for attempt in range(4):
    try:
        r = post("/api/v1/auth/totp", {"ticket": ticket, "code": totp_now(inp["totp_secret"])})
        tok = r.get("token")
        if tok: break
    except Exception as e:
        pass
    time.sleep(32)
if not tok: raise SystemExit("totp failed")
print("LOGIN_OK")

# existing nodes / duplicate check
nodes = post("/api/v1/nodes?page=1&size=100", {}, token=tok, method="GET") if False else None
import urllib.request as u
req = u.Request(API + "/api/v1/nodes?page=1&size=200")
req.add_header("Authorization", "Bearer " + tok)
with u.urlopen(req, timeout=20) as r2:
    listing = json.loads(r2.read().decode())
items = listing.get("items", [])
dup = [n for n in items if n.get("name") == "DMIT-HY2-US-01"]
if dup:
    print("NODE_EXISTS id=%s" % dup[0]["id"])
    nid = dup[0]["id"]
else:
    r = post("/api/v1/nodes", {"name": "DMIT-HY2-US-01", "host": "69.63.219.156", "port": 34567,
                               "proto": "hysteria2", "params": {"password": inp["hypass"]},
                               "priority": 0, "enabled": True}, token=tok)
    if r.get("_http"):
        raise SystemExit("NODE_CREATE_FAIL %s %s" % (r["_http"], r.get("_body")))
    nid = r["id"]
    print("NODE_CREATED id=%s" % nid)

# bind to R2 (no apply)
r = post("/api/v1/assignments/batch", {"device_ids": ["rtr-a9bcba"], "node_id": nid,
                                       "is_primary": False, "apply": False}, token=tok)
print("BIND:", json.dumps(r.get("items", r))[:220])

# preview (POST)
req = u.Request(API + "/api/v1/devices/rtr-a9bcba/configs/preview", method="POST")
req.add_header("Content-Type", "application/json")
req.add_header("Authorization", "Bearer " + tok)
req.data = b"{}"
with u.urlopen(req, timeout=30) as r3:
    pv = json.loads(r3.read().decode())
REV = pv.get("revision")
print("PREVIEW_OK rev=%s" % str(REV)[:16])

# apply
r = post("/api/v1/devices/rtr-a9bcba/configs/apply", {"revision": REV}, token=tok,
         idem="dmit-hy2-r2-" + str(REV)[:16])
print("APPLY_TASK: %s %s" % (r.get("task_id"), r.get("version")))
'''
sftp = gz.open_sftp()
with sftp.open("/tmp/dm2-input.json", "w") as f:
    f.write(json.dumps({**creds, "hypass": hypass}))
with sftp.open("/tmp/dm2-apply.py", "w") as f:
    f.write(GZ_SCRIPT)
try:
    sftp.chmod("/tmp/dm2-input.json", 0o600)
    sftp.chmod("/tmp/dm2-apply.py", 0o600)
except Exception:
    pass
sftp.close()

# pause R2 self-heal during publish (prior-session practice)
r2 = ssh_pw("127.0.0.1", 13223, "root", PW, jump=gz)
_, o, _ = r2.exec_command("[ -e /tmp/selfheal.pause ] && echo PAUSE_EXISTS || { touch /tmp/selfheal.pause && echo PAUSED; }", timeout=15)
ps = o.read().decode().strip()
print("@@pause:", ps)
if "PAUSED" not in ps and "PAUSE_EXISTS" not in ps:
    print("STOP: cannot pause self-heal")
    r2.close(); gz.close(); raise SystemExit(1)
r2.close()

_, o, e = gz.exec_command("python3 /tmp/dm2-apply.py 2>&1 | tail -8", timeout=240)
ao = o.read().decode("utf-8", "replace").strip()
print("@@apply:", ao[:500])
m = re.search(r"APPLY_TASK: (\S+) (\d+)", ao)
if not m:
    # unpause before bailing
    r2 = ssh_pw("127.0.0.1", 13223, "root", PW, jump=gz)
    r2.exec_command("rm -f /tmp/selfheal.pause")
    r2.close(); gz.close()
    raise SystemExit("no task id — aborted (self-heal unpaused)")

task_id, version = m.group(1), m.group(2)
for i in range(30):
    time.sleep(4)
    _, o, _ = gz.exec_command("sudo -u postgres psql routermgmt -t -A -c \"SELECT status FROM tasks WHERE id='%s';\"" % task_id, timeout=20)
    st = o.read().decode().strip()
    print("poll%d: %s" % (i, st))
    if st in ("ok", "failed", "rolled_back", "expired"):
        break

# cleanup creds on GZ
gz.exec_command("shred -u /tmp/dm2-input.json 2>/dev/null || rm -f /tmp/dm2-input.json; rm -f /tmp/dm2-apply.py")

# device verify
r2 = ssh_pw("127.0.0.1", 13223, "root", PW, jump=gz)
_, o, _ = r2.exec_command(r'''
sleep 3
echo "== groups =="
for g in PROXY NODE-PICK US-MEDIA AUTO; do printf '%s=' "$g"; curl --noproxy '*' -s -m 5 http://127.0.0.1:9090/proxies/$g | jsonfilter -e '@.now'; done
echo "== NODE-PICK members =="; curl --noproxy '*' -s -m 5 http://127.0.0.1:9090/proxies/NODE-PICK | jsonfilter -e '@.all[*]'
echo "== DMIT node state =="; curl --noproxy '*' -s -m 8 "http://127.0.0.1:9090/proxies/DMIT-HY2-US-01" | jsonfilter -e '@.alive' -e '@.history[-1].delay'
echo "== DMIT node delay test (real handshake) =="
curl --noproxy '*' -s -m 15 "http://127.0.0.1:9090/proxies/DMIT-HY2-US-01/delay?timeout=10000&url=http://www.gstatic.com/generate_204"; echo
echo "== config version/crlf =="; grep -c $'\r' /etc/mihomo/config.yaml || true; ls /etc/mihomo/config.yaml
''', timeout=90)
print("@@verify:\n" + o.read().decode("utf-8", "replace"))
_, o, _ = r2.exec_command("rm -f /tmp/selfheal.pause && echo unpause-ok", timeout=10)
print("@@unpause:", o.read().decode().strip())
r2.close()
gz.close()
print("DONE")
