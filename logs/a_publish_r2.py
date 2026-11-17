"""A5 publish driver: pause -> apply(rev) -> poll -> device verify(LF/nodes/groups) -> AUTO + US-MEDIA -> bbolt -> hb x2 -> unpause."""
import json
import os
import subprocess
import time

import paramiko

PW = os.environ["DEVPW"]
DEV_ID = "rtr-a9bcba"
DEV_PORT = 13223
REV = "aec8d590b32f504bff013d7f3666b27c7bbd8d1e66755eda43d9f29e24fdcb9c"

led = open(r"F:\vps\登录信息与配置总表-20260917.md", encoding="utf-8").read()
creds_obj = {
    "admin_user": led.split("| 用户名 | `")[1].split("`")[0],
    "admin_pw": led.split("| 密码 | `")[1].split("`")[0],
    "totp_secret": led.split("TOTP 密钥（重绑验证器用） | `")[1].split("`")[0],
}

gz = paramiko.SSHClient()
gz.set_missing_host_key_policy(paramiko.AutoAddPolicy())
gz.connect("134.175.151.213", port=28722, username="ubuntu", password=PW,
           timeout=20, look_for_keys=False, allow_agent=False)

gz_apply = r'''
import json, urllib.request, base64, hashlib, hmac, struct, time
API = "http://127.0.0.1:8080"
inp = json.loads(open("/tmp/a-input.json").read())
REV = inp["revision"]
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
tok = None
r = post("/api/v1/auth/login", {"username": inp["admin_user"], "password": inp["admin_pw"]})
ticket = r["ticket"]
for attempt in range(4):
    step = int(time.time()) // 30
    try:
        r = post("/api/v1/auth/totp", {"ticket": ticket, "code": totp_now(inp["totp_secret"])})
        tok = r.get("token")
        print("totp attempt", attempt + 1, "ok")
        break
    except urllib.error.HTTPError as e:
        print("totp attempt", attempt + 1, "http", e.code)
        if attempt < 3: time.sleep(32)
if not tok: raise SystemExit("totp failed")
idem = "a-r2-unbind14-" + REV[:16]
r = post("/api/v1/devices/rtr-a9bcba/configs/apply", {"revision": REV}, token=tok, idem=idem)
print("APPLY_TASK:", r.get("task_id"), r.get("version"))
'''
sftp = gz.open_sftp()
f = sftp.open("/tmp/a-input.json", "w")
f.write(json.dumps({**creds_obj, "revision": REV}))
f.close()
f = sftp.open("/tmp/a-apply.py", "w")
f.write(gz_apply)
f.close()
try:
    sftp.chmod("/tmp/a-input.json", 0o600)
    sftp.chmod("/tmp/a-apply.py", 0o600)
except Exception:
    pass
sftp.close()

# pause on R2
chan = gz.get_transport().open_channel("direct-tcpip", ("127.0.0.1", DEV_PORT), ("127.0.0.1", 0))
r2 = paramiko.SSHClient()
r2.set_missing_host_key_policy(paramiko.AutoAddPolicy())
r2.connect("127.0.0.1", port=DEV_PORT, username="root", password=PW, sock=chan,
           timeout=20, look_for_keys=False, allow_agent=False)
_, o, _ = r2.exec_command(
    "[ -e /tmp/selfheal.pause ] && echo PAUSE_EXISTS || { touch /tmp/selfheal.pause && echo PAUSED; }",
    timeout=15)
ps = o.read().decode().strip()
print("@@pause:", ps)
if "PAUSED" not in ps:
    print("STOP"); r2.close(); gz.close(); raise SystemExit(1)
r2.close()

# apply
_, o, e = gz.exec_command("python3 /tmp/a-apply.py 2>&1 | tail -4", timeout=180)
ao = o.read().decode("utf-8", "replace").strip()
print("@@apply:", ao[:300])
import re
m = re.search(r"APPLY_TASK: (\S+) (\d+)", ao)
if not m:
    gz.close(); raise SystemExit("no task — STOP")
task_id, version = m.group(1), m.group(2)

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
    gz.close(); raise SystemExit("TASK_NOT_OK — STOP")

# device verify (LF + nodes + groups + P0 + AUTO + US-MEDIA + pause clear)
chan = gz.get_transport().open_channel("direct-tcpip", ("127.0.0.1", DEV_PORT), ("127.0.0.1", 0))
r2 = paramiko.SSHClient()
r2.set_missing_host_key_policy(paramiko.AutoAddPolicy())
r2.connect("127.0.0.1", port=DEV_PORT, username="root", password=PW, sock=chan,
           timeout=20, look_for_keys=False, allow_agent=False)
V = r"""
echo "crlf=$(grep -c $'\r' /etc/mihomo/config.yaml)"
echo "nodes_total=$(grep -c '  - name:' /etc/mihomo/config.yaml)"
grep -E '^  - name:' /etc/mihomo/config.yaml
echo "no_bulk_gia_cf=$(grep -cE 'BULK|154\.36\.139\.155|104\.18\.32\.7|162\.159\.135\.42' /etc/mihomo/config.yaml)"
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

# ensure selections persisted: PROXY->AUTO, US-MEDIA->NODE-PICK
_, o, _ = r2.exec_command(
    "curl --noproxy '*' -sS -m 10 -X PUT -H 'Content-Type: application/json' "
    "--data '{\"name\":\"AUTO\"}' -o /dev/null -w '%{http_code}' http://127.0.0.1:9090/proxies/PROXY",
    timeout=30)
print("@@proxy_auto:", o.read().decode().strip())
_, o, _ = r2.exec_command(
    "curl --noproxy '*' -sS -m 10 -X PUT -H 'Content-Type: application/json' "
    "--data '{\"name\":\"NODE-PICK\"}' -o /dev/null -w '%{http_code}' http://127.0.0.1:9090/proxies/US-MEDIA",
    timeout=30)
print("@@usmedia_nodepick:", o.read().decode().strip())
r2.close()

# cache -> bbolt
_, o, _ = gz.exec_command(
    f"SSHPASS='{PW}' sshpass -e scp -O -P {DEV_PORT} -o StrictHostKeyChecking=yes "
    f"root@127.0.0.1:/etc/mihomo/cache.db /tmp/r2-cache-a35", timeout=45)
sftp = gz.open_sftp()
sftp.get("/tmp/r2-cache-a35", r"F:\vps\backups\game-dns-20261116\r2-cache-a35.db")
sftp.close()
gz.close()

bb = os.path.expanduser("~/go/bin/bbolt.exe")
chk = subprocess.run([bb, "check", r"F:\vps\backups\game-dns-20261116\r2-cache-a35.db"],
                     capture_output=True, text=True, timeout=30)
print("bbolt check =", chk.stdout.decode(errors="replace").strip())
for grp in ("PROXY", "US-MEDIA", "NODE-PICK"):
    sel = subprocess.run([bb, "get", r"F:\vps\backups\game-dns-20261116\r2-cache-a35.db",
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
print("A_STAGE_COMPLETE")
