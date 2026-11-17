"""B1 stage-1 driver: extract ledger creds in memory, run GZ-side b1_r2_stage1.py via stdin."""
import json
import os
import sys

import paramiko

PW = os.environ["DEVPW"]

# ledger extraction identical in shape to mgmt_api_test.py (M05 controlled source)
led = open(r"F:\vps\登录信息与配置总表-20260917.md", encoding="utf-8").read()
admin_user = led.split("| 用户名 | `")[1].split("`")[0]
admin_pw = led.split("| 密码 | `")[1].split("`")[0]
totp_secret = led.split("TOTP 密钥（重绑验证器用） | `")[1].split("`")[0]

payload = json.dumps({
    "admin_user": admin_user,
    "admin_pw": admin_pw,
    "totp_secret": totp_secret,
    "device_id": "rtr-a9bcba",
}).encode()

gz = paramiko.SSHClient()
gz.set_missing_host_key_policy(paramiko.AutoAddPolicy())
gz.connect("134.175.151.213", port=28722, username="ubuntu", password=PW,
           timeout=20, look_for_keys=False, allow_agent=False)
sftp = gz.open_sftp()
sftp.put(r"F:\vps\logs\b1_r2_stage1.py", "/tmp/b1_r2_stage1.py")
st = sftp.stat("/tmp/b1_r2_stage1.py")
sftp.chmod("/tmp/b1_r2_stage1.py", 0o600)
sftp.close()
print(f"script uploaded ({st.st_size} bytes)")

stdin, stdout, stderr = gz.exec_command("python3 /tmp/b1_r2_stage1.py", timeout=120)
stdin.channel.sendall(payload)
stdin.channel.shutdown_write()
out = stdout.read().decode("utf-8", "replace")
err = stderr.read().decode("utf-8", "replace")
gz.close()

try:
    j = json.loads(out.strip())
    print(json.dumps(j, ensure_ascii=False, indent=1)[:1600])
    sys.exit(0 if j.get("ok") else 2)
except Exception:
    print("RAW:", out[:800])
    if err.strip():
        print("stderr:", err[:400])
    sys.exit(2)
