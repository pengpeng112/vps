"""Install wan-watch + persistent syslog onto the Athena router."""
import os, posixpath, paramiko, sys

ROOT = os.path.dirname(os.path.abspath(__file__))
HOST, USER, PASS = "192.168.2.1", "root", "[REDACTED]"

FILES = [
    (os.path.join(ROOT, "wan_watch.sh"), "/usr/sbin/wan-watch.sh", 0o755),
    (os.path.join(ROOT, "wan_watch.init"), "/etc/init.d/wan-watch", 0o755),
    (os.path.join(ROOT, "wan_watch.hotplug"), "/etc/hotplug.d/iface/98-wan-watch", 0o755),
]

REMOTE_SETUP = r"""
set -e
mkdir -p /etc/routermgmt/logs
chmod 755 /usr/sbin/wan-watch.sh /etc/init.d/wan-watch /etc/hotplug.d/iface/98-wan-watch
/etc/init.d/wan-watch enable

# 系统日志落到 overlay，重启还能翻 netifd/udhcpc/内核链路
uci -q set system.@system[0].log_file='/etc/routermgmt/logs/syslog'
uci -q set system.@system[0].log_size='1024'
uci commit system
/etc/init.d/log restart
sleep 1
/etc/init.d/wan-watch restart
sleep 2
echo '===== ENABLED ====='
/etc/init.d/wan-watch enabled && echo wan-watch:enabled
ls -l /etc/rc.d | grep wan-watch
echo '===== UCI LOG ====='
uci -q get system.@system[0].log_file
uci -q get system.@system[0].log_size
echo '===== FIRST PROBE ====='
/usr/sbin/wan-watch.sh probe
echo '===== LOG TAIL ====='
tail -5 /etc/routermgmt/logs/wan-watch.log
echo '===== SYSLOG EXISTS ====='
ls -l /etc/routermgmt/logs/
echo '===== PROC ====='
ps w | grep wan-watch | grep -v grep
echo '===== DONE ====='
"""

def main():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PASS, timeout=10,
              allow_agent=False, look_for_keys=False)
    sftp = c.open_sftp()
    for local, remote, mode in FILES:
        tmp = remote + ".new"
        print(f"put {local} -> {remote}")
        sftp.put(local, tmp)
        sftp.chmod(tmp, mode)
        try:
            sftp.remove(remote)
        except FileNotFoundError:
            pass
        sftp.rename(tmp, remote)
    sftp.close()
    stdin, stdout, stderr = c.exec_command(REMOTE_SETUP, timeout=90)
    sys.stdout.write(stdout.read().decode("utf-8", "replace"))
    err = stderr.read().decode("utf-8", "replace")
    rc = stdout.channel.recv_exit_status()
    c.close()
    if err:
        sys.stderr.write(err)
    return rc

if __name__ == "__main__":
    sys.exit(main())
