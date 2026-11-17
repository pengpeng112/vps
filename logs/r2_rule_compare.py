"""Compare R2 current config rules vs preview masked yaml (names + IP-CIDR lines only)."""
import json
import os
import time

import paramiko

PW = os.environ["DEVPW"]
gz = paramiko.SSHClient()
gz.set_missing_host_key_policy(paramiko.AutoAddPolicy())
gz.connect("134.175.151.213", port=28722, username="ubuntu", password=PW,
           timeout=20, look_for_keys=False, allow_agent=False)

# 1) R2 current config: node names + IP-CIDR lines
_, o, e = gz.exec_command(
    f"SSHPASS='{PW}' sshpass -e ssh -p 13223 -o StrictHostKeyChecking=yes "
    f"root@127.0.0.1 \"grep -E '^  - name:|IP-CIDR' /etc/mihomo/config.yaml | head -40\"", timeout=45)
o.channel.sendall(PW.encode())
o.channel.shutdown_write()
cur = o.read().decode("utf-8", "replace").strip()
err = e.read().decode("utf-8", "replace").strip()
if err and "post-quantum" not in err:
    print("r2-ssh-err:", err[:150])
print("@@r2-current (names + CIDR):")
print(cur[:1200])

# 2) preview masked yaml: extract IP-CIDR lines + group names via GZ python (login again)
led = open(r"F:\vps\登录信息与配置总表-20260917.md", encoding="utf-8").read()
au = led.split("| 用户名 | `")[1].split("`")[0]
apw = led.split("| 密码 | `")[1].split("`")[0]
totp = led.split("TOTP 密钥（重绑验证器用） | `")[1].split("`")[0]

gz_script = r'''
import json, urllib.request, base64, hashlib, hmac, struct, time, re
API="http://127.0.0.1:8080"
inp=json.loads(open("/tmp/b1-input.json").read())
def post(path,payload,token=None):
    req=urllib.request.Request(API+path,method="POST")
    req.add_header("Content-Type","application/json")
    if token: req.add_header("Authorization","Bearer "+token)
    req.data=json.dumps(payload).encode()
    with urllib.request.urlopen(req,timeout=20) as r: return json.loads(r.read().decode())
def totp_now(sec):
    key=base64.b32decode(sec+"="*((8-len(sec)%8)%8),casefold=True)
    d=hmac.new(key,struct.pack(">Q",int(time.time())//30),hashlib.sha1).digest()
    off=d[-1]&0x0F
    return "%06d"%((struct.unpack(">I",d[off:off+4])[0]&0x7FFFFFFF)%1000000)
r=post("/api/v1/auth/login",{"username":inp["admin_user"],"password":inp["admin_pw"]})
r=post("/api/v1/auth/totp",{"ticket":r["ticket"],"code":totp_now(inp["totp_secret"])})
tok=r["token"]
r=post("/api/v1/devices/rtr-a9bcba/configs/preview",{},token=tok)
my=r.get("masked_yaml","")
cidr=[l.strip() for l in my.splitlines() if "IP-CIDR" in l]
names=[l.strip() for l in my.splitlines() if l.strip().startswith("- name:")]
print(json.dumps({"revision":r.get("revision"),"next_version":r.get("next_version"),
 "current_version":r.get("current_version"),"cidr":cidr,"names":names},ensure_ascii=False))
'''
open("/tmp/b1-input.json", "w").write(json.dumps({"admin_user": au, "admin_pw": apw, "totp_secret": totp}))
sftp = gz.open_sftp()
sftp.put(r"F:\vps\logs\b1_r2_stage1.py", "/tmp/b1_r2_stage1.py")  # keep updated script in place
import io
sftpfile = sftp.open("/tmp/b1-preview-report.py", "w")
sftpfile.write(gz_script)
sftpfile.close()
try:
    sftp.chmod("/tmp/b1-preview-report.py", 0o600)
except Exception:
    pass
sftp.close()

_, o, e = gz.exec_command("python3 /tmp/b1-preview-report.py", timeout=90)
out = o.read().decode("utf-8", "replace").strip()
err2 = e.read().decode("utf-8", "replace").strip()
print("@@preview (names + CIDR):")
print(out[:1500])
if err2:
    print("gz-err:", err2[:200])
gz.close()
