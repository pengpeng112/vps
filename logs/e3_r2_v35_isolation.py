"""E3/R2 v35 publish: preview isolation asserts -> pause -> apply -> poll -> verify -> bbolt -> hb x2."""
import json
import os
import time

import paramiko

PW = os.environ["DEVPW"]
DEV_ID = "rtr-a9bcba"
DEV_PORT = 13223

led = open(r"F:\vps\登录信息与配置总表-20260917.md", encoding="utf-8").read()
creds = {
    "admin_user": led.split("| 用户名 | `")[1].split("`")[0],
    "admin_pw": led.split("| 密码 | `")[1].split("`")[0],
    "totp_secret": led.split("TOTP 密钥（重绑验证器用） | `")[1].split("`")[0],
}

gz = paramiko.SSHClient()
gz.set_missing_host_key_policy(paramiko.AutoAddPolicy())
gz.connect("134.175.151.213", port=28722, username="ubuntu", password=PW,
           timeout=20, look_for_keys=False, allow_agent=False)

preview_py = r'''
import json, urllib.request, base64, hashlib, hmac, struct, time, re
API = "http://127.0.0.1:8080"
inp = json.loads(open("/tmp/b2-pre.json").read())
def post(path, payload, token=None):
    req = urllib.request.Request(API + path, method="POST")
    req.add_header("Content-Type", "application/json")
    if token: req.add_header("Authorization", "Bearer " + token)
    req.data = json.dumps(payload).encode()
    with urllib.request.urlopen(req, timeout=25) as r: return json.loads(r.read().decode())
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
def grp(name):
    m = re.search(r'- name: "' + name + r'"\n    type: \w+\n(?:    [^\n]*\n)*?    proxies:\n((?:      - [^\n]+\n)+)', my)
    if not m: return []
    return [l.strip("- ").strip() for l in m.group(1).splitlines() if l.strip()]
np, us, pr = grp("NODE-PICK"), grp("US-MEDIA"), grp("PROXY")
out = {"revision": r.get("revision"), "next_version": r.get("next_version"),
       "nodepick": np, "usmedia": us, "proxy": pr, "crlf": ("\r" in my)}
print(json.dumps(out, ensure_ascii=False))
open("/tmp/a-rev.json", "w").write(json.dumps({"revision": r.get("revision")}))
'''

apply_py = r'''
import json, urllib.request, base64, hashlib, hmac, struct, time
API = "http://127.0.0.1:8080"
inp = json.loads(open("/tmp/b2-pre.json").read())
REV = json.loads(open("/tmp/a-rev.json").read())["revision"]
def post(path, payload, token=None, idem=None):
    req = urllib.request.Request(API + path, method="POST")
    req.add_header("Content-Type", "application/json")
    if token: req.add_header("Authorization", "Bearer " + token)
    if idem: req.add_header("Idempotency-Key", idem)
    req.data = json.dumps(payload).encode()
    with urllib.request.urlopen(req, timeout=25) as r: return json.loads(r.read().decode())
def totp_now(sec):
    key = base64.b32decode(sec + "=" * ((8 - len(sec) % 8) % 8), casefold=True)
    d = hmac.new(key, struct.pack(">Q", int(time.time()) // 30), hashlib.sha1).digest()
    off = d[-1] & 0x0F
    return "%06d" % ((struct.unpack(">I", d[off:off+4])[0] & 0x7FFFFFFF) % 1000000)
r = post("/api/v1/auth/login", {"username": inp["admin_user"], "password": inp["admin_pw"]})
ticket = r["ticket"]
tok = None
for attempt in range(4):
    step = int(time.time()) // 30
    try:
        r = post("/api/v1/auth/totp", {"ticket": ticket, "code": totp_now(inp["totp_secret"])})
        tok = r.get("token"); print("totp attempt", attempt+1, "ok"); break
    except urllib.error.HTTPError as e:
        print("totp attempt", attempt+1, "http", e.code)
        if attempt < 3: time.sleep(32)
if not tok: raise SystemExit("totp failed")
idem = "e3-r2-isolation-v35"
r = post("/api/v1/devices/rtr-a9bcba/configs/apply", {"revision": REV}, token=tok, idem=idem)
print("APPLY_TASK:", r.get("task_id"), r.get("version"))
'''

sftp = gz.open_sftp()
f = sftp.open("/tmp/b2-pre.json", "w"); f.write(json.dumps(creds)); f.close()
f = sftp.open("/tmp/a35-preview.py", "w"); f.write(preview_py); f.close()
f = sftp.open("/tmp/a35-apply.py", "w"); f.write(apply_py); f.close()
for fp in ("/tmp/a35-preview.py", "/tmp/a35-apply.py", "/tmp/b2-pre.json"):
    try: sftp.chmod(fp, 0o600)
    except Exception: pass
sftp.close()

_, o, _ = gz.exec_command("sleep 10; python3 /tmp/a35-preview.py 2>&1 | tail -3", timeout=120)
pre = o.read().decode("utf-8", "replace").strip()
print("@@preview:", pre[:600])
pj = json.loads(pre)
ok = ("BULK" not in " ".join(pj["nodepick"]) and "BULK" not in " ".join(pj["proxy"])
      and "BULK-LAX-GIA-01" in " ".join(pj["usmedia"]) and "NODE-PICK" in pj["usmedia"]
      and not pj["crlf"])
print("ISOLATION_ASSERT:", "PASS" if ok else "FAIL")
assert ok, "isolation assert failed"
REV = pj["revision"]

# pause R2
chan = gz.get_transport().open_channel("direct-tcpip", ("127.0.0.1", DEV_PORT), ("127.0.0.1", 0))
r2 = paramiko.SSHClient()
r2.set_missing_host_key_policy(paramiko.AutoAddPolicy())
r2.connect("127.0.0.1", port=DEV_PORT, username="root", password=PW, sock=chan,
           timeout=20, look_for_keys=False, allow_agent=False)
_, o, _ = r2.exec_command(
    "[ -e /tmp/selfheal.pause ] && echo PAUSE_EXISTS || { touch /tmp/selfheal.pause && echo PAUSED; }",
    timeout=15)
print("@@pause:", o.read().decode().strip())
r2.close()

_, o, e = gz.exec_command("python3 /tmp/a35-apply.py 2>&1 | tail -3", timeout=180)
ao = o.read().decode("utf-8", "replace").strip()
print("@@apply:", ao[:200])
import re
m = re.search(r"APPLY_TASK: (\S+) (\d+)", ao)
assert m, "no task"
task_id, version = m.group(1), m.group(2)

for i in range(30):
    time.sleep(4)
    _, o, _ = gz.exec_command(
        f"sudo -u postgres psql routermgmt -t -A -c \"SELECT status FROM tasks WHERE id='{task_id}';\"",
        timeout=20)
    st = o.read().decode().strip()
    print(f"poll{i}: {st}")
    if st in ("ok", "failed", "rolled_back", "expired"):
        break
assert st == "ok", "TASK_NOT_OK"

# device verify + ensure selections + pause clear
chan = gz.get_transport().open_channel("direct-tcpip", ("127.0.0.1", DEV_PORT), ("127.0.0.1", 0))
r2 = paramiko.SSHClient()
r2.set_missing_host_key_policy(paramiko.AutoAddPolicy())
r2.connect("127.0.0.1", port=DEV_PORT, username="root", password=PW, sock=chan,
           timeout=20, look_for_keys=False, allow_agent=False)
V = r"""
echo "crlf=$(grep -c $'\r' /etc/mihomo/config.yaml)"
echo "nodes_total=$(grep -c '  - name:' /etc/mihomo/config.yaml)"
grep -E '^  - name:' /etc/mihomo/config.yaml
echo "no_bulk_gia=$(grep -cE 'BULK|154\.36\.139\.155' /etc/mihomo/config.yaml)"
for g in PROXY NODE-PICK US-MEDIA AUTO DOWNLOAD; do
  echo "$g now=$(curl --noproxy '*' -fsS -m 5 http://127.0.0.1:9090/proxies/$g 2>/dev/null | jsonfilter -e '@.now')"
done
echo "gamedns=$(uci -q get dhcp.@dnsmasq[0].server | grep -c 'lol.qq.com')"
curl -sk -m 6 -o /dev/null -w 'health=%{http_code}\n' https://127.0.0.1:18443/api/v1/health
echo "bulk_conns=$(curl --noproxy '*' -fsS -m 5 http://127.0.0.1:9090/connections 2>/dev/null | grep -c 'BULK')"
rm -f /tmp/selfheal.pause && echo PAUSE_CLEARED
"""
_, o, _ = r2.exec_command(V, timeout=60)
print("@@device-verify")
print(o.read().decode("utf-8", "replace").strip())
r2.close()

_, o, _ = gz.exec_command(
    f"SSHPASS='{PW}' sshpass -e scp -O -P {DEV_PORT} -o StrictHostKeyChecking=yes "
    f"root@127.0.0.1:/etc/mihomo/cache.db /tmp/r2-cache-v35 && "
    f"md5sum /tmp/r2-cache-v35 | cut -c1-12", timeout=45)
gz_pull = o.read().decode("utf-8", "replace").strip()
print("@@cache_pull:", gz_pull[:80])
sftp = gz.open_sftp()
sftp.get("/tmp/r2-cache-v35", r"F:\vps\backups\game-dns-20261116\r2-cache-v35.db")
sftp.close()
gz.close()

import subprocess
bb = os.path.expanduser("~/go/bin/bbolt.exe")
for grp in ("PROXY", "US-MEDIA", "NODE-PICK"):
    sel = subprocess.run([bb, "get", r"F:\vps\backups\game-dns-20261116\r2-cache-v35.db",
                          "selected", grp], capture_output=True, text=True, timeout=30)
    print(f"bbolt selected/{grp} =", sel.stdout.decode(errors="replace").strip())

g2 = paramiko.SSHClient()
g2.set_missing_host_key_policy(paramiko.AutoAddPolicy())
g2.connect("134.175.151.213", port=28722, username="ubuntu", password=PW,
           timeout=20, look_for_keys=False, allow_agent=False)
for i in range(2):
    _, o, _ = g2.exec_command(
        "sudo -u postgres psql routermgmt -t -A -c \"SELECT round(extract(epoch FROM now()-last_heartbeat_at)) "
        f"FROM devices WHERE id='{DEV_ID}';\"", timeout=20)
    print(f"hb{i+1}_age:", o.read().decode().strip(), "s")
    if i == 0:
        time.sleep(40)
g2.close()
print("V35_ISOLATION_COMPLETE")
