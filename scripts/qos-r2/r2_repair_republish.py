"""R2 repair via platform republish: bind CF1/CF2 (k3x9m2.top chain) + clean v38.

Republish regenerates /etc/mihomo/config.yaml from golden+DB nodes, wiping the
hand-edited damage (wrong BWG password, junk tcp/udp lines). Then restore
resolv.conf from the pre-damage backup. No manual config.yaml edits (§5.1).
"""
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
DEV_PORT = 13223
CF1_ID, CF2_ID = 10, 11


def controlled_row(path, name):
    for line in (ROOT / path).read_text(encoding="utf-8-sig").splitlines():
        cells = [x.strip() for x in line.strip().strip("|").split("|")]
        if line.startswith("|") and cells[0] == name:
            return "|".join(cells[1:])
    raise ValueError("missing %s" % name)


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

# ---- 并行会话守门：R2 无 pending/running 任务 ----
_, o, _ = gz.exec_command(
    "sudo -u postgres psql routermgmt -t -A -c \"SELECT count(*) FROM tasks WHERE device_id='rtr-a9bcba' AND status IN ('pending','running');\"", timeout=20)
cnt = o.read().decode().strip()
print("gate tasks(pending/running)=%s" % cnt)
assert cnt == "0", "R2 has in-flight tasks — abort"

r2 = ssh_pw("127.0.0.1", DEV_PORT, "root", PW, jump=gz)


def run_r2(script, t=60):
    i, o, e = r2.exec_command("sh -s", timeout=t)
    i.write(script)
    i.channel.shutdown_write()
    return o.read().decode("utf-8", "replace")


# ---- 修前取证（一次性） ----
print(run_r2("""echo '== pre-state =='
head -3 /etc/resolv.conf; ls /etc/resolv.conf.bak-20261118 2>/dev/null || echo NO_BACKUP
grep -c '^  tcp: true' /etc/mihomo/config.yaml 2>/dev/null
awk '/name: "BWG-HY2-JPTY-01"/{f=1} f&&/password/{print "pw_len=" length($2); exit}' /etc/mihomo/config.yaml
"""))

# ---- 发布期暂停 self-heal（先例做法） ----
print("pause:", run_r2("[ -e /tmp/selfheal.pause ] && echo PAUSE_EXISTS || { touch /tmp/selfheal.pause && echo PAUSED; }").strip())

# ---- 平台：绑定 CF1/CF2 → preview → apply ----
GZ_SCRIPT = r'''
import json, urllib.request, urllib.error, base64, hashlib, hmac, struct, time
API = "http://127.0.0.1:8080"
inp = json.loads(open("/tmp/r2fix-input.json").read())
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
ticket = t0.get("ticket")
for attempt in range(4):
    try:
        r = req("/api/v1/auth/totp", {"ticket": ticket, "code": totp_now(inp["totp_secret"])})
        tok = r.get("token")
        if tok: break
    except Exception:
        pass
    time.sleep(32)
if not tok: raise SystemExit("totp failed")
print("LOGIN_OK")

for nid in (10, 11):
    r = req("/api/v1/assignments/batch", {"device_ids": [inp["dev"]], "node_id": nid,
                                           "is_primary": False, "apply": False}, token=tok)
    b = r.get("items", r)
    print("BIND node%s:" % nid, json.dumps(b)[:160])
pv = req("/api/v1/devices/%s/configs/preview" % inp["dev"], {}, token=tok)
if pv.get("_http"): raise SystemExit("preview fail %s" % pv)
REV = pv.get("revision")
print("PREVIEW_OK rev=%s" % str(REV)[:16])
ap = req("/api/v1/devices/%s/configs/apply" % inp["dev"], {"revision": REV}, token=tok,
         idem="r2fix-cfbind-" + str(REV)[:16])
print("APPLY_TASK: %s %s" % (ap.get("task_id"), ap.get("version")))
'''
sftp = gz.open_sftp()
with sftp.open("/tmp/r2fix-input.json", "w") as f:
    f.write(json.dumps({**creds, "dev": DEV_ID}))
with sftp.open("/tmp/r2fix-apply.py", "w") as f:
    f.write(GZ_SCRIPT)
try:
    sftp.chmod("/tmp/r2fix-input.json", 0o600)
    sftp.chmod("/tmp/r2fix-apply.py", 0o600)
except Exception:
    pass
sftp.close()

_, o, _ = gz.exec_command("python3 /tmp/r2fix-apply.py 2>&1 | tail -6", timeout=240)
ao = o.read().decode("utf-8", "replace").strip()
print("@@apply:", ao[:400])
m = re.search(r"APPLY_TASK: (\S+) (\d+)", ao)
if not m:
    run_r2("rm -f /tmp/selfheal.pause")
    r2.close(); gz.close()
    raise SystemExit("no task — aborted, self-heal unpaused")
task_id, version = m.group(1), m.group(2)
for i in range(30):
    time.sleep(4)
    _, o, _ = gz.exec_command(
        "sudo -u postgres psql routermgmt -t -A -c \"SELECT status FROM tasks WHERE id='%s';\"" % task_id, timeout=20)
    st = o.read().decode().strip()
    print("poll%d: %s" % (i, st))
    if st in ("ok", "failed", "rolled_back", "expired"):
        break
gz.exec_command("shred -u /tmp/r2fix-input.json 2>/dev/null; rm -f /tmp/r2fix-apply.py")

# ---- resolv.conf 还原 + 解除暂停 ----
print(run_r2("""[ -f /etc/resolv.conf.bak-20261118 ] && cp /etc/resolv.conf.bak-20261118 /etc/resolv.conf && echo resolv-restored
cat /etc/resolv.conf
rm -f /tmp/selfheal.pause && echo unpause-ok
sleep 3
nslookup www.baidu.com 2>/dev/null | grep -m1 'Address' || true"""))

# ---- 验证 ----
print(run_r2(r"""sleep 2
echo '== config sanity =='
grep -c '^  tcp: true' /etc/mihomo/config.yaml || true
awk '/name: "BWG-HY2-JPTY-01"/{f=1} f&&/password/{print "BWG_pw_len=" length($2); exit}' /etc/mihomo/config.yaml
echo '== groups =='
for g in PROXY NODE-PICK US-MEDIA AUTO; do printf '%s=' "$g"; curl --noproxy '*' -s -m 4 http://127.0.0.1:9090/proxies/$g | jsonfilter -e '@.now'; done
printf 'PROXY.all: '; curl --noproxy '*' -s -m 4 http://127.0.0.1:9090/proxies/PROXY | jsonfilter -e '@.all[*]' | tr '\n' ' '; echo
echo '== node delays =='
for n in BWG-HY2-JPTY-01 DMIT-HY2-US-01 CF-CDN-GIA-01 CF2-CDN-GIA-01; do
  printf '%-18s ' "$n"
  curl --noproxy '*' -s -m 10 "http://127.0.0.1:9090/proxies/$n/delay?timeout=8000&url=http://www.gstatic.com/generate_204"; echo
done
echo '== transparent =='
printf 'gstatic   '; curl --noproxy '*' -s -o /dev/null -w '%{http_code} %{time_total}s' -m 8 https://www.gstatic.com/generate_204; echo
printf 'google    '; curl --noproxy '*' -s -o /dev/null -w '%{http_code} %{time_total}s' -m 8 https://www.google.com/generate_204; echo
printf 'youtube   '; curl --noproxy '*' -s -o /dev/null -w '%{http_code} %{time_total}s' -m 8 https://www.youtube.com/generate_204; echo
printf 'github    '; curl --noproxy '*' -s -o /dev/null -w '%{http_code} %{time_total}s' -m 8 https://www.github.com/generate_204; echo
printf 'baidu     '; curl --noproxy '*' -s -o /dev/null -w '%{http_code} %{time_total}s' -m 8 https://www.baidu.com; echo
printf 'exitip    '; curl --noproxy '*' -s -m 8 https://ipinfo.io/ip; echo
""", t=180))

r2.close()
gz.close()
print("STAGE1_DONE version=%s task=%s" % (version, task_id))
