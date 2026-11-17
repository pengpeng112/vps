"""Driver: unbind node14 from R2 (stage A2-A4). Creds via stdin, memory only."""
import json
import os
import sys

import paramiko

PW = os.environ["DEVPW"]
led = open(r"F:\vps\登录信息与配置总表-20260917.md", encoding="utf-8").read()
payload = json.dumps({
    "admin_user": led.split("| 用户名 | `")[1].split("`")[0],
    "admin_pw": led.split("| 密码 | `")[1].split("`")[0],
    "totp_secret": led.split("TOTP 密钥（重绑验证器用） | `")[1].split("`")[0],
}).encode()

gz = paramiko.SSHClient()
gz.set_missing_host_key_policy(paramiko.AutoAddPolicy())
gz.connect("134.175.151.213", port=28722, username="ubuntu", password=PW,
           timeout=20, look_for_keys=False, allow_agent=False)
sftp = gz.open_sftp()
sftp.put(r"F:\vps\logs\a_unbind_r2.py", "/tmp/a_unbind_r2.py")
try:
    sftp.chmod("/tmp/a_unbind_r2.py", 0o600)
except Exception:
    pass
sftp.close()

stdin, stdout, stderr = gz.exec_command("python3 /tmp/a_unbind_r2.py", timeout=120)
stdin.channel.sendall(payload)
stdin.channel.shutdown_write()
out = stdout.read().decode("utf-8", "replace")
err = stderr.read().decode("utf-8", "replace")
gz.close()
print(out.strip()[:1200])
if err.strip():
    print("stderr:", err.strip()[:300])
try:
    j = json.loads(out.strip())
    sys.exit(0 if j.get("ok") else 2)
except Exception:
    sys.exit(2)
