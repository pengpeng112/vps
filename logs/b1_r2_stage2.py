"""B1 stage-2 driver: pause R2 -> apply(revision) -> poll task -> device verify -> PROXY AUTO -> bbolt -> heartbeats -> unpause."""
import hashlib
import json
import os
import time

import paramiko

PW = os.environ["DEVPW"]
DEV_ID = "rtr-a9bcba"
DEV_PORT = 13223

gz = paramiko.SSHClient()
gz.set_missing_host_key_policy(paramiko.AutoAddPolicy())
gz.connect("134.175.151.213", port=28722, username="ubuntu", password=PW,
           timeout=20, look_for_keys=False, allow_agent=False)

# upload GZ-side apply script + input file
led = open(r"F:\vps\登录信息与配置总表-20260917.md", encoding="utf-8").read()
au = led.split("| 用户名 | `")[1].split("`")[0]
apw = led.split("| 密码 | `")[1].split("`")[0]
totp = led.split("TOTP 密钥（重绑验证器用） | `")[1].split("`")[0]

gz_apply = r'''
import json, urllib.request, base64, hashlib, hmac, struct, time, sys
API = "http://127.0.0.1:8080"
inp = json.loads(open("/tmp/b1-input.json").read())
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
r = post("/api/v1/auth/login", {"username": inp["admin_user"], "password": inp["admin_pw"]})
r = post("/api/v1/auth/totp", {"ticket": r["ticket"], "code": totp_now(inp["totp_secret"])})
tok = r["token"]
idem = "b1-r2-narrow-" + REV[:16]
r = post("/api/v1/devices/rtr-a9bcba/configs/apply", {"revision": REV}, token=tok, idem=idem)
print(json.dumps({"task_id": r.get("task_id") or r.get("task", ""), "version": r.get("version"),
                  "raw_keys": sorted(r.keys())}))
'''
sftp = gz.open_sftp()
sftp.put(r"F:\vps\logs\b1_r2_stage1.py", "/tmp/b1_r2_stage1.py")  # keep in place
f = sftp.open("/tmp/b1_r2_apply.py", "w")
f.write(gz_apply)
f.close()
f = sftp.open("/tmp/b1-input.json", "w")
f.write(json.dumps({"admin_user": au, "admin_pw": apw, "totp_secret": totp,
                    "revision": "aec8d590b32f504bff013d7f3666b27c7bbd8d1e66755eda43d9f29e24fdcb9c"}))
f.close()
try:
    sftp.chmod("/tmp/b1_r2_apply.py", 0o600)
    sftp.chmod("/tmp/b1-input.json", 0o600)
except Exception:
    pass
sftp.close()

# 1) pause on R2 (check none first)
chan = gz.get_transport().open_channel("direct-tcpip", ("127.0.0.1", DEV_PORT), ("127.0.0.1", 0))
r2 = paramiko.SSHClient()
r2.set_missing_host_key_policy(paramiko.AutoAddPolicy())
r2.connect("127.0.0.1", port=DEV_PORT, username="root", password=PW, sock=chan,
           timeout=20, look_for_keys=False, allow_agent=False)
s = """[ -e /tmp/selfheal.pause ] && echo PAUSE_EXISTS || { touch /tmp/selfheal.pause && echo PAUSED; }
ls /etc/routermgmt/state/txn/*/uncommitted 2>/dev/null | wc -l"""
_, o, _ = r2.exec_command(s, timeout=20)
pause_state = o.read().decode().strip()
print("@@pause:", pause_state.replace("\n", " | "))
if "PAUSED" not in pause_state:
    print("STOP: pause exists or txn dirty")
    r2.close(); gz.close(); raise SystemExit(1)

# 2) apply
_, o, e = gz.exec_command("python3 /tmp/b1_r2_apply.py", timeout=90)
apply_out = o.read().decode("utf-8", "replace").strip()
err = e.read().decode("utf-8", "replace").strip()
print("@@apply:", apply_out[:300])
if err and "post-quantum" not in err:
    print("apply-err:", err[:150])
aj = json.loads(apply_out)
task_id = aj.get("task_id")
version = aj.get("version")
print("task:", task_id, "version:", version)
if not task_id:
    r2.close(); gz.close(); raise SystemExit("no task_id — STOP")

# 3) poll task terminal (ok)
for i in range(30):
    time.sleep(4)
    _, o, _ = gz.exec_command(
        f"sudo -u postgres psql routermgmt -t -A -F'|' -c \"SELECT status FROM tasks "
        f"WHERE id='{task_id}';\" 2>&1", timeout=20)
    st = o.read().decode().strip()
    print(f"poll{i}: {st}")
    if st in ("ok", "failed", "rolled_back", "expired"):
        break
if st != "ok":
    print("TASK_NOT_OK — STOP, leave pause for investigation")
    r2.close(); gz.close(); raise SystemExit(1)

# 4) device verify: config nodes + chains + game DNS + 9090 groups
V = r"""
echo "@@device_config"
grep -c '  - name:' /etc/mihomo/config.yaml
grep -E '^  - name:' /etc/mihomo/config.yaml | head -12
echo "cidr_gia=$(grep -c '154.36.139.155' /etc/mihomo/config.yaml)"
echo "cidr_bwg=$(grep -c '74.82.193.113' /etc/mihomo/config.yaml)"
echo "@@dnsmasq_game_intact"
uci -q get dhcp.@dnsmasq[0].server | grep -c 'lol.qq.com\|gamesafe\|anticheat'
echo "@@groups_9090"
for g in PROXY NODE-PICK US-MEDIA AUTO; do
  echo "$g now=$(curl --noproxy '*' -fsS -m 5 http://127.0.0.1:9090/proxies/$g 2>/dev/null | jsonfilter -e '@.now')"
done
echo "@@pause_clear_now"
rm -f /tmp/selfheal.pause && echo PAUSE_CLEARED
"""
_, o, _ = r2.exec_command(V, timeout=60)
print("@@device-verify")
print(o.read().decode("utf-8", "replace").strip())

# 5) PROXY -> AUTO (persistent)
_, o, _ = r2.exec_command(
    "curl --noproxy '*' -sS -m 10 -X PUT -H 'Content-Type: application/json' "
    "--data '{\"name\":\"AUTO\"}' -o /dev/null -w '%{http_code}' http://127.0.0.1:9090/proxies/PROXY",
    timeout=30)
code = o.read().decode().strip()
print("@@put_auto:", code)

# 6) pull cache.db -> bbolt verify
_, o, _ = gz.exec_command(
    f"SSHPASS='{PW}' sshpass -e scp -O -P {DEV_PORT} -o StrictHostKeyChecking=yes "
    f"root@127.0.0.1:/etc/mihomo/cache.db /tmp/r2-cache-after-b1", timeout=45)
o.channel.sendall(PW.encode()); o.channel.shutdown_write()
o.read()
sftp = gz.open_sftp()
sftp.get("/tmp/r2-cache-after-b1", r"F:\vps\backups\game-dns-20261116\r2-cache-after-b1.db")
sftp.close()
print("@@cache pulled")

r2.close()
gz.close()

import subprocess
bb = os.path.expanduser("~/go/bin/bbolt.exe")
sel = subprocess.run([bb, "get", r"F:\vps\backups\game-dns-20261116\r2-cache-after-b1.db",
                      "selected", "PROXY"], capture_output=True, text=True, timeout=30)
print("@@bbolt selected/PROXY =", sel.stdout.decode(errors="replace").strip())

# 7) two heartbeats
g2 = paramiko.SSHClient()
g2.set_missing_host_key_policy(paramiko.AutoAddPolicy())
g2.connect("134.175.151.213", port=28722, username="ubuntu", password=PW,
           timeout=20, look_for_keys=False, allow_agent=False)
for i in range(2):
    _, o, _ = g2.exec_command(
        "sudo -u postgres psql routermgmt -t -A -c \"SELECT round(extract(epoch FROM now()-last_heartbeat_at)) "
        f"FROM devices WHERE id='{DEV_ID}';\"", timeout=20)
    print(f"hb{i+1}_age:", o.read().decode().strip(), "s")
    time.sleep(40)
g2.close()
print("B1_R2_COMPLETE")
