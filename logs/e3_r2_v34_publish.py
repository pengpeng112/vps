"""R2 v34 publish: pause -> login -> preview(select assertions) -> apply -> poll -> device verify."""
import hashlib
import json
import os
import time

import paramiko

PW = os.environ["DEVPW"]
DEV_ID = "rtr-a9bcba"
DEV_PORT = 13223

led = open(r"F:\vps\登录信息与配置总表-20260917.md", encoding="utf-8").read()
creds = {"admin_user": led.split("| 用户名 | `")[1].split("`")[0],
         "admin_pw": led.split("| 密码 | `")[1].split("`")[0],
         "totp_secret": led.split("TOTP 密钥（重绑验证器用） | `")[1].split("`")[0]}

gz = paramiko.SSHClient()
gz.set_missing_host_key_policy(paramiko.AutoAddPolicy())
gz.connect("134.175.151.213", port=28722, username="ubuntu", password=PW,
           timeout=20, look_for_keys=False, allow_agent=False)

gz_pre = r'''
import json, urllib.request, base64, hashlib, hmac, struct, time, re
API = "http://127.0.0.1:8080"
inp = json.loads(open("/tmp/b1-input.json").read())
def post(path, payload, token=None, idem=None):
    req = urllib.request.Request(API + path, method="POST")
    req.add_header("Content-Type", "application/json")
    if token: req.add_header("Authorization", "Bearer " + token)
    if idem: req.add_header("Idempotency-Key", idem)
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
us_block = re.search(r'- name: "US-MEDIA"\n    type: (\w+)(?:\n(?:    .*\n|  - name|$)*)', my)
us_type = re.search(r'- name: "US-MEDIA"\n    type: (\w+)', my).group(1)
us_m = re.search(r'- name: "US-MEDIA"\n    type: \w+\n(?:    [^\n]*\n)*?    proxies:\n((?:      - [^\n]+\n)+)', my)
members = [l.strip("- ").strip() for l in us_m.group(1).splitlines() if l.strip()] if us_m else []
proxies_names = re.findall(r'  - name: "([^"]+)"', my)
out = {
    "revision": r.get("revision"), "next_version": r.get("next_version"),
    "current_version": r.get("current_version"), "us_type": us_type,
    "us_members": members, "proxy_count": len(proxies_names),
    "proxies_names": proxies_names[:6],
    "no_gia": ("154.36.139.155/32" not in my and 'name: "US666' not in my),
    "no_crlf": ("\r" not in my),
}
print(json.dumps(out, ensure_ascii=False))
open("/tmp/b1-rev.json", "w").write(json.dumps({"revision": r.get("revision")}))
'''
sftp = gz.open_sftp()
sftp.put(r"F:\vps\logs\b1_r2_stage1.py", "/tmp/b1_r2_stage1.py")
f = sftp.open("/tmp/b2-pre.json", "w")
f.write(json.dumps(creds))
f.close()
f = sftp.open("/tmp/b2-preview.py", "w")
f.write(gz_pre)
f.close()
try:
    sftp.chmod("/tmp/b2-pre.json", 0o600)
    sftp.chmod("/tmp/b2-preview.py", 0o600)
except Exception:
    pass
sftp.close()

_, o, _ = gz.exec_command("python3 /tmp/b2-preview.py", timeout=90)
pre = o.read().decode("utf-8", "replace").strip()
print("@@preview:", pre[:700])
pj = json.loads(pre)
if pj["us_type"] != "select" or "BULK-LAX-GIA-01" not in pj["us_members"]:
    print("PREVIEW_ASSERT_FAIL — STOP")
    gz.close(); raise SystemExit(1)
REV = pj["revision"]

# pause on R2
chan = gz.get_transport().open_channel("direct-tcpip", ("127.0.0.1", DEV_PORT), ("127.0.0.1", 0))
r2 = paramiko.SSHClient()
r2.set_missing_host_key_policy(paramiko.AutoAddPolicy())
r2.connect("127.0.0.1", port=DEV_PORT, username="root", password=PW, sock=chan,
           timeout=20, look_for_keys=False, allow_agent=False)
_, o, _ = r2.exec_command(
    "[ -e /tmp/selfheal.pause ] && echo PAUSE_EXISTS || { touch /tmp/selfheal.pause && echo PAUSED; }", timeout=15)
ps = o.read().decode().strip()
print("@@pause:", ps)
if "PAUSED" not in ps:
    print("STOP"); r2.close(); gz.close(); raise SystemExit(1)

# apply
gz_apply = r'''
import json, urllib.request, base64, hashlib, hmac, struct, time
API = "http://127.0.0.1:8080"
inp = json.loads(open("/tmp/b1-input.json").read())
REV = json.loads(open("/tmp/b1-rev.json").read())["revision"]
def post(path, payload, token=None, idem=None):
    req = urllib.request.Request(API + path, method="POST")
    req.add_header("Content-Type", "application/json")
    if token: req.add_header("Authorization", "Bearer " + token)
    if idem: req.add_header("Idempotency-Key", idem)
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
idem = "b1-r2-bulk-" + REV[:16]
r = post("/api/v1/devices/rtr-a9bcba/configs/apply", {"revision": REV}, token=tok, idem=idem)
print(json.dumps({"task_id": r.get("task_id"), "version": r.get("version")}))
'''
f = sftp.open("/tmp/b2-apply.py", "w")
f.write(gz_apply)
f.close()
sftp.close()

_, o, e = gz.exec_command("python3 /tmp/b2-apply.py", timeout=60)
ao = o.read().decode("utf-8", "replace").strip()
print("@@apply:", ao[:200])
aj = json.loads(ao)
task_id = aj["task_id"]; version = aj["version"]

# poll
for i in range(30):
    time.sleep(4)
    _, o, _ = gz.exec_command(
        f"sudo -u postgres psql routermgmt -t -A -c \"SELECT status FROM tasks WHERE id='{task_id}';\"",
        timeout=20)
    st = o.read().decode().strip()
    print(f"poll{i}: {st}")
    if st in ("ok", "failed", "rolled_back", "expired"):
        break
if st != "ok":
    print("TASK_NOT_OK — STOP")
    r2.close(); gz.close(); raise SystemExit(1)

# device verify
V = r"""
echo "nodes=$(grep -c '  - name:' /etc/mihomo/config.yaml)"
grep -E '^  - name:' /etc/mihomo/config.yaml | head -10
echo "us_type_check:"
grep -A1 'name: "US-MEDIA"' /etc/mihomo/config.yaml | head -2
for g in PROXY NODE-PICK US-MEDIA AUTO; do
  echo "$g now=$(curl --noproxy '*' -fsS -m 5 http://127.0.0.1:9090/proxies/$g 2>/dev/null | jsonfilter -e '@.now')"
done
curl -sk -m 6 -o /dev/null -w 'health=%{http_code}\n' https://127.0.0.1:18443/api/v1/health
"""
_, o, _ = r2.exec_command(V, timeout=60)
print("@@device-verify")
print(o.read().decode("utf-8", "replace").strip())

# US-MEDIA -> NODE-PICK (select default may be first member BULK; set explicit)
_, o, _ = r2.exec_command(
    "curl --noproxy '*' -sS -m 10 -X PUT -H 'Content-Type: application/json' "
    "--data '{\"name\":\"NODE-PICK\"}' -o /dev/null -w '%{http_code}' http://127.0.0.1:9090/proxies/US-MEDIA",
    timeout=30)
print("@@usmedia_put_nodepick:", o.read().decode().strip())
_, o, _ = r2.exec_command(
    "curl --noproxy '*' -fsS -m 5 http://127.0.0.1:9090/proxies/US-MEDIA 2>/dev/null | jsonfilter -e '@.now'",
    timeout=20)
print("@@usmedia_now:", o.read().decode().strip())

# game DNS intact
_, o, _ = r2.exec_command(
    "uci -q get dhcp.@dnsmasq[0].server | grep -c 'lol.qq.com'", timeout=15)
print("@@gamedns_intact:", o.read().decode().strip())

# PROXY -> AUTO
_, o, _ = r2.exec_command(
    "curl --noproxy '*' -sS -m 10 -X PUT -H 'Content-Type: application/json' "
    "--data '{\"name\":\"AUTO\"}' -o /dev/null -w '%{http_code}' http://127.0.0.1:9090/proxies/PROXY",
    timeout=30)
print("@@proxy_auto:", o.read().decode().strip())

r2.close()

# cache verify
_, o, _ = gz.exec_command(
    f"SSHPASS='{PW}' sshpass -e scp -O -P {DEV_PORT} -o StrictHostKeyChecking=yes "
    f"root@127.0.0.1:/etc/mihomo/cache.db /tmp/r2-cache-v34", timeout=45)
time.sleep(1)
sftp = gz.open_sftp()
sftp.get("/tmp/r2-cache-v34", r"F:\vps\backups\game-dns-20261116\r2-cache-v34.db")
sftp.close()
gz.close()

import subprocess
bb = os.path.expanduser("~/go/bin/bbolt.exe")
for grp in ("PROXY", "US-MEDIA"):
    sel = subprocess.run([bb, "get", r"F:\vps\backups\game-dns-20261116\r2-cache-v34.db",
                          "selected", grp], capture_output=True, text=True, timeout=30)
    print(f"bbolt selected/{grp} =", sel.stdout.decode(errors="replace").strip())

# two heartbeats
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
print("E3_R2_V34_COMPLETE")
