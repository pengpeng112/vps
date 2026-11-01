"""R7 Codex-runbook step-2 read-only precheck: bind execution params."""
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

SCRIPT = r"""
echo "@@bind_basic"
date '+%Y-%m-%d %H:%M:%S %z'
cat /proc/sys/kernel/random/boot_id
R7_MPID=$(pidof mihomo)
echo "mihomo_pid=$R7_MPID"
echo "@@api_auth_test"
curl --noproxy '*' -s -m 4 -o /dev/null -w 'noauth_version=%{http_code}\n' http://127.0.0.1:9090/version
echo "@@controller_bind"
netstat -tln 2>/dev/null | grep 9090
echo "@@jsonfilter"
command -v jsonfilter || echo NO-jsonfilter
command -v ubus || echo NO-ubus
echo "@@cache_db_path"
ls -l /proc/$R7_MPID/fd 2>/dev/null | grep -i cache
ls -l /etc/mihomo/cache.db 2>/dev/null
df -Pk /etc/mihomo | tail -1
echo "@@profile_section"
sed -n '/^profile:/,/^[a-z]/p' /etc/mihomo/config.yaml | head -8
grep -n 'store-selected\|store-fake-ip' /etc/mihomo/config.yaml
echo "@@external_controller"
grep -n 'external-controller\|secret' /etc/mihomo/config.yaml | head -4
echo "@@txn_state"
ls -l /etc/routermgmt/state/txn 2>/dev/null
find /etc/routermgmt/state/txn -name uncommitted 2>/dev/null | wc -l
ls /tmp/selfheal.pause 2>/dev/null || echo no-pause
echo "@@tunnel_conf_watchers"
grep -RnsE 'tunnel\.conf|VPS4_|159\.223\.36\.119|inotify|inotifywait' /etc/init.d /etc/routermgmt /etc/hotplug.d /etc/rc.local /etc/crontabs 2>/dev/null | grep -v '^Binary' | head -15
echo "@@dropbear_keys"
ls /etc/dropbear/ 2>/dev/null
wc -l /etc/dropbear/authorized_keys 2>/dev/null || echo no-authorized_keys
echo "@@mihomo_environ_keys"
tr '\0' '\n' < /proc/$R7_MPID/environ 2>/dev/null | cut -d= -f1 | head -20
echo "@@disk_space"
df -Pk /root /etc/routermgmt /tmp | tail -3
echo "@@tools"
command -v curl wget sed cmp mktemp nc curl; echo tools-done
echo "@@done_precheck"
"""

stdin, stdout, stderr = r7 = None, None, None
chan = gz.get_transport().open_channel("direct-tcpip", ("127.0.0.1", 13229), ("127.0.0.1", 0))
r7 = paramiko.SSHClient()
r7.set_missing_host_key_policy(paramiko.AutoAddPolicy())
r7.connect("127.0.0.1", port=13229, username="root", password=PW, sock=chan,
           timeout=20, look_for_keys=False, allow_agent=False)

stdin, stdout, stderr = r7.exec_command("sh -s", timeout=90)
stdin.channel.sendall(SCRIPT.encode())
stdin.channel.shutdown_write()
out = stdout.read().decode("utf-8", "replace")
err = stderr.read().decode("utf-8", "replace")
print("@@r7-precheck")
print(out)
if err.strip():
    print("@@stderr-head")
    print(err[:300])

r7.close()
gz.close()
print("@@saved_precheck")
