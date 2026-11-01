"""R7 round-3: GZ devices schema + heartbeat age; device misc files."""
import os
import sys

import paramiko

PW = os.environ.get("DEVPW")
if not PW:
    sys.exit("DEVPW not set")

gz = paramiko.SSHClient()
gz.set_missing_host_key_policy(paramiko.AutoAddPolicy())
gz.connect("134.175.151.213", port=28722, username="ubuntu", password=PW,
           timeout=20, look_for_keys=False, allow_agent=False)


def run_gz(cmd, t=30):
    _, o, e = gz.exec_command(cmd, timeout=t)
    return o.read().decode("utf-8", "replace") + e.read().decode("utf-8", "replace")


print("@@gz-devices-cols")
print(run_gz(
    "sudo -u postgres psql routermgmt -t -A -c \"SELECT column_name FROM "
    "information_schema.columns WHERE table_name='devices' ORDER BY ordinal_position;\" 2>&1 | tr '\\n' ' '"
).strip())

print("@@gz-r7-heartbeat")
print(run_gz(
    "sudo -u postgres psql routermgmt -t -A -F'|' -c \"SELECT name,"
    "round(extract(epoch from (now()-last_heartbeat_at))) AS age_s,"
    "to_char(last_heartbeat_at,'MM-DD HH24:MI:SS') FROM devices ORDER BY name;\" 2>&1"
).strip())

print("@@gz-cron")
print(run_gz("sudo crontab -l 2>/dev/null | grep -E 'rssh|probe|backup' ; ls /etc/systemd/system/timers.target.wants/ 2>/dev/null | grep -i rssh").strip())

chan = gz.get_transport().open_channel("direct-tcpip", ("127.0.0.1", 13229), ("127.0.0.1", 0))
r7 = paramiko.SSHClient()
r7.set_missing_host_key_policy(paramiko.AutoAddPolicy())
r7.connect("127.0.0.1", port=13229, username="root", password=PW, sock=chan,
           timeout=20, look_for_keys=False, allow_agent=False)

SCRIPT = r"""
echo "@@misc_files"
for f in tunnel-watchdog.sh connlog.sh onboot-hooks.sh wan-watchdog.sh mgmt-port-discovery.sh; do
  ls -l /etc/routermgmt/$f 2>/dev/null || echo "MISSING $f"
done
echo "@@mihomo_ipt"
ls -l /etc/mihomo-ipt.sh 2>/dev/null || echo "MISSING /etc/mihomo-ipt.sh"
echo "@@include_full"
uci -q show firewall | grep -A0 include | head -12
echo "@@done3"
"""

stdin, stdout, stderr = r7.exec_command("sh -s", timeout=60)
stdin.channel.sendall(SCRIPT.encode())
stdin.channel.shutdown_write()
out = stdout.read().decode("utf-8", "replace")
err = stderr.read().decode("utf-8", "replace")
print("@@r7-round3")
print(out)
if err.strip():
    print("@@r7-round3-stderr")
    print(err[:300])

r7.close()
gz.close()
print("@@saved3")
