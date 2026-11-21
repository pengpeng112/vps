"""Bind GIA-HY2 (node 9) to R2 and republish v39 (adds 5th live line)."""
from pathlib import Path
import json
import re
import sys
import time

import paramiko

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[2]
BT = chr(96)
DEV_ID = "rtr-a9bcba"
NODE_ID = 9  # GIA-HY2-US-01


def controlled_row(path, name):
    for line in (ROOT / path).read_text(encoding="utf-8-sig").splitlines():
        cells = [x.strip() for x in line.strip().strip("|").split("|")]
        if line.startswith("|") and cells[0] == name:
            return "|".join(cells[1:])
    raise ValueError(name)


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


gz = ssh_pw("134.175.151.213", 28722, "ubuntu", PW)
_, o, _ = gz.exec_command(
    "sudo -u postgres psql routermgmt -t -A -c \"SELECT count(*) FROM tasks WHERE device_id='rtr-a9bcba' AND status IN ('pending','running');\"", timeout=20)
cnt = o.read().decode().strip()
print("gate tasks=%s" % cnt)
assert cnt == "0", "R2 has in-flight tasks — abort"

r2 = ssh_pw("127.0.0.1", 13223, "root", PW, jump=gz)


def run_r2(script, t=60):
    i, o, e = r2.exec_command("sh -s", timeout=t)
    i.write(script)
    i.channel.shutdown_write()
    return o.read().decode("utf-8", "replace")


print("pause:", run_r2("[ -e /tmp/selfheal.pause ] && echo PAUSE_EXISTS || { touch /tmp/selfheal.pause && echo PAUSED; }").strip())

GZ_SCRIPT = r'''
import json, urllib.request, urllib.error, base64, hashlib, hmac, struct, time
API = "http://127.0.0.1:8080"
inp = json.loads(open("/tmp/g9-input.json").read())
def req(path, payload=None, token=None, idem=None):
    r = urllib.request.Request(API + path, method="POST" if payload is not None else "GET")
    r.add_header("Content-Type", "application/json")
    if token: r.add_header("Authorization", "Bearer " + token)
    if idem: r.add_header("Idempotency-Key", idem)
    if payload is not None: r.data = json.dumps(payload).encode()
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try: return {"_http": e.code, "_body": e.read().decode()[:300]}
        except Exception: return {"_http": e.code}
def totp_now(sec):
    key = base64.b32decode(sec + "=" * ((8 - len(sec) % 8) % 8), casefold=True)
    d = hmac.new(key, struct.pack(">Q", int(time.time()) // 30), hashlib.sha1).digest()
    off = d[-1] & 0x0F
    return "%06d" % ((struct.unpack(">I", d[off:off+4])[0] & 0x7FFFFFFF) % 1000000)
tok = None
t0 = req("/api/v1/auth/login", {"username": inp["admin_user"], "password": inp["admin_pw"]})
for attempt in range(4):
    try:
        r = req("/api/v1/auth/totp", {"ticket": t0.get("ticket"), "code": totp_now(inp["totp_secret"])})
        tok = r.get("token")
        if tok: break
    except Exception:
        pass
    time.sleep(32)
if not tok: raise SystemExit("totp failed")
print("LOGIN_OK")
r = req("/api/v1/assignments/batch", {"device_ids": [inp["dev"]], "node_id": inp["nid"],
                                       "is_primary": False, "apply": False}, token=tok)
print("BIND:", json.dumps(r.get("items", r))[:160])
pv = req("/api/v1/devices/%s/configs/preview" % inp["dev"], {}, token=tok)
if pv.get("_http"): raise SystemExit("preview fail %s" % pv)
print("PREVIEW_OK rev=%s" % str(pv.get("revision"))[:16])
ap = req("/api/v1/devices/%s/configs/apply" % inp["dev"], {"revision": pv["revision"]}, token=tok,
         idem="r2-giahy2-" + str(pv["revision"])[:16])
print("APPLY_TASK: %s %s" % (ap.get("task_id"), ap.get("version")))
'''
sftp = gz.open_sftp()
with sftp.open("/tmp/g9-input.json", "w") as f:
    f.write(json.dumps({**creds, "dev": DEV_ID, "nid": NODE_ID}))
with sftp.open("/tmp/g9-apply.py", "w") as f:
    f.write(GZ_SCRIPT)
try:
    sftp.chmod("/tmp/g9-input.json", 0o600); sftp.chmod("/tmp/g9-apply.py", 0o600)
except Exception:
    pass
sftp.close()

_, o, _ = gz.exec_command("python3 /tmp/g9-apply.py 2>&1 | tail -5", timeout=240)
ao = o.read().decode("utf-8", "replace").strip()
print("@@apply:", ao[:300])
m = re.search(r"APPLY_TASK: (\S+) (\d+)", ao)
if not m:
    run_r2("rm -f /tmp/selfheal.pause")
    r2.close(); gz.close()
    raise SystemExit("no task — aborted, unpaused")
task_id = m.group(1)
for i in range(30):
    time.sleep(4)
    _, o, _ = gz.exec_command(
        "sudo -u postgres psql routermgmt -t -A -c \"SELECT status FROM tasks WHERE id='%s';\"" % task_id, timeout=20)
    st = o.read().decode().strip()
    print("poll%d: %s" % (i, st))
    if st in ("ok", "failed", "rolled_back", "expired"):
        break
gz.exec_command("shred -u /tmp/g9-input.json 2>/dev/null; rm -f /tmp/g9-apply.py")

print(run_r2(r"""rm -f /tmp/selfheal.pause && echo unpause-ok
sleep 3
echo '== NODE-PICK members =='
curl --noproxy '*' -s -m 5 http://127.0.0.1:9090/proxies/NODE-PICK | jsonfilter -e '@.all[*]' | tr '\n' ' '; echo
echo '== groups =='
for g in PROXY NODE-PICK US-MEDIA; do printf '%s=' "$g"; curl --noproxy '*' -s -m 4 http://127.0.0.1:9090/proxies/$g | jsonfilter -e '@.now'; done
echo '== GIA-HY2 delay =='
curl --noproxy '*' -s -m 8 "http://127.0.0.1:9090/proxies/GIA-HY2-US-01/delay?timeout=6000&url=http://www.gstatic.com/generate_204"; echo
echo '== user-path =='
printf 'google  '; curl -x http://127.0.0.1:7890 -s -o /dev/null -w '%{http_code} %{time_total}s' -m 8 https://www.google.com/generate_204; echo
printf 'baidu   '; curl --noproxy '*' -s -o /dev/null -w '%{http_code} %{time_total}s' -m 8 https://www.baidu.com; echo
""", t=120))
r2.close(); gz.close()
print("DONE task=%s" % task_id)
