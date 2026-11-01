#!/usr/bin/env python
# R4 部署：watchdog + tunnel.conf 迁中继（经 PC→VPS→13224 链）
import paramiko, time, sys
sys.path.insert(0, r"F:\vps\scripts")

# 1) PC → VPS
vps = paramiko.SSHClient(); vps.set_missing_host_key_policy(paramiko.AutoAddPolicy())
vps.connect("159.223.36.119", port=28722, username="root",
            key_filename=r"C:\Users\Administrator\.ssh\id_ed25519_digitalocean", timeout=20)

# 2) 上传 watchdog 到 VPS /tmp
sftp = vps.open_sftp()
sftp.put(r"F:\vps\mgmt\agent\tunnel-watchdog.sh", "/tmp/twd.sh")
sftp.close()

# 3) VPS → R4（13224），流式传文件+执行
wd = open(r"F:\vps\mgmt\agent\tunnel-watchdog.sh").read()
chan = vps.get_transport().open_channel("direct-tcpip", ("127.0.0.1", 13224), ("127.0.0.1", 0))
r4 = paramiko.SSHClient(); r4.set_missing_host_key_policy(paramiko.AutoAddPolicy())
r4.connect("127.0.0.1", username="root", key_filename="/root/.ssh/id_rsa_snr_rssh",
           sock=chan, allow_agent=False, look_for_keys=False)

# watchdog
cmd1 = "cat > /etc/routermgmt/tunnel-watchdog.sh && chmod 755 /etc/routermgmt/tunnel-watchdog.sh && sh -n /etc/routermgmt/tunnel-watchdog.sh && echo WD_INSTALLED"
_, o, e = r4.exec_command(cmd1, timeout=30)
o.channel.sendall(wd.encode()); o.channel.shutdown_write()
print((o.read()+e.read()).decode(errors="replace").strip())

_, o, e = r4.exec_command(
    "grep -q routermgmt-tunnelwd /etc/crontabs/root 2>/dev/null || "
    "echo '* * * * * /etc/routermgmt/tunnel-watchdog.sh #routermgmt-tunnelwd' >> /etc/crontabs/root; "
    "/etc/init.d/cron restart >/dev/null 2>&1; "
    "echo cron=$(crontab -l | grep -c tunnelwd); "
    "/etc/routermgmt/tunnel-watchdog.sh; echo wd_run_rc=$?", timeout=40)
print((o.read()+e.read()).decode(errors="replace").strip())

# 4) tunnel.conf 迁中继（VPS_HOST -> 43.134.46.44）
_, o, e = r4.exec_command(
    "sed -i 's|^VPS_HOST=.*|VPS_HOST=43.134.46.44|' /etc/routermgmt/tunnel.conf && "
    "grep VPS_HOST /etc/routermgmt/tunnel.conf; "
    "/etc/init.d/mgmt-tunnel restart >/dev/null 2>&1; sleep 10; "
    "curl -sk -m 8 -o /dev/null -w 'health_via_relay=%{http_code}\\n' https://127.0.0.1:18443/api/v1/health", timeout=60)
print((o.read()+e.read()).decode(errors="replace").strip())
r4.close(); vps.close()
PYEOF
