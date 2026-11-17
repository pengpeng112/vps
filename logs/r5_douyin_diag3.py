"""R5 round-3: unexpected 16:43 reboot forensics + WiFi/ client reality. READ-ONLY."""
import os
import sys

import paramiko

PW = os.environ.get("DEVPW")

SCRIPT = r"""
echo "@@now"
date '+%H:%M:%S'
echo "uptime_s=$(cut -d' ' -f1 /proc/uptime)"
echo "@@reboot_cron"
crontab -l 2>/dev/null | grep -i reboot
echo "@@boot_log"
logread 2>/dev/null | head -12
echo "@@preboot_tail"
logread 2>/dev/null | grep -E 'reboot|shutdown|watchdog|panic|oops' | head -6
dmesg 2>/dev/null | grep -iE 'panic|watchdog|reset reason' | head -4
echo "@@radio_full"
iwinfo 2>/dev/null | grep -E '^\w|ESSID|Access Point|Signal|Noise|Channel|Mode' | head -30
echo "@@assoc_counts"
for ifc in $(iw dev 2>/dev/null | grep Interface | awk '{print $2}'); do
  n=$(iw dev $ifc station dump 2>/dev/null | grep -c 'Station')
  echo "$ifc assoc=$n"
done
echo "@@leases"
wc -l /tmp/dhcp.leases 2>/dev/null
head -10 /tmp/dhcp.leases 2>/dev/null | awk '{print $2,$3,$4}'
echo "@@wan_hist"
grep -c 'event=probe' /etc/routermgmt/logs/wan-watch.log 2>/dev/null
head -3 /etc/routermgmt/logs/wan-watch.log 2>/dev/null
echo R5_DONE3
"""

gz = paramiko.SSHClient()
gz.set_missing_host_key_policy(paramiko.AutoAddPolicy())
gz.connect("134.175.151.213", port=28722, username="ubuntu", password=PW,
           timeout=20, look_for_keys=False, allow_agent=False)
chan = gz.get_transport().open_channel("direct-tcpip", ("127.0.0.1", 13225), ("127.0.0.1", 0))
r5 = paramiko.SSHClient()
r5.set_missing_host_key_policy(paramiko.AutoAddPolicy())
r5.connect("127.0.0.1", port=13225, username="root", password=PW, sock=chan,
           timeout=20, look_for_keys=False, allow_agent=False)
stdin, stdout, stderr = r5.exec_command("sh -s", timeout=120)
stdin.channel.sendall(SCRIPT.encode())
stdin.channel.shutdown_write()
print(stdout.read().decode("utf-8", "replace"))
err = stderr.read().decode("utf-8", "replace")
if err.strip():
    print("@@stderr:", err[:200])
r5.close()
gz.close()
