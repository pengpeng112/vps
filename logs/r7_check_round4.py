"""R7 round-4 micro: PROXY.now, watchdog invocation chain, VPS4/DO live attempts."""
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

chan = gz.get_transport().open_channel("direct-tcpip", ("127.0.0.1", 13229), ("127.0.0.1", 0))
r7 = paramiko.SSHClient()
r7.set_missing_host_key_policy(paramiko.AutoAddPolicy())
r7.connect("127.0.0.1", port=13229, username="root", password=PW, sock=chan,
           timeout=20, look_for_keys=False, allow_agent=False)

SCRIPT = r"""
echo "@@proxy_now"
curl -s -m 5 http://127.0.0.1:9090/proxies/PROXY 2>/dev/null | grep -o '"now":"[^"]*"'
echo "@@watchdog_callers"
grep -l 'tunnel-watchdog' /etc/routermgmt/*.sh /etc/init.d/* 2>/dev/null || echo no-caller-found
grep -n 'tunnel-watchdog' /etc/routermgmt/netprobe.sh /etc/routermgmt/mgmt-port-discovery.sh /etc/routermgmt/self-heal.sh /etc/routermgmt/onboot-hooks.sh 2>/dev/null | head -6
echo "@@vps4_port_active"
grep -n '^VPS[2345]_' /etc/routermgmt/tunnel.conf 2>/dev/null
echo "@@live_endpoint_conns"
netstat -tnp 2>/dev/null | grep -E '43\.134\.46\.44|74\.82\.203\.175|159\.223\.36\.119|154\.36\.139\.155' | head -8
echo "@@tunnel_procs"
ps w | grep '[s]sh -i /etc/routermgmt' | wc -l
echo "@@done4"
"""

stdin, stdout, stderr = r7.exec_command("sh -s", timeout=60)
stdin.channel.sendall(SCRIPT.encode())
stdin.channel.shutdown_write()
out = stdout.read().decode("utf-8", "replace")
err = stderr.read().decode("utf-8", "replace")
print("@@r7-round4")
print(out)
if err.strip():
    print("@@r7-round4-stderr")
    print(err[:300])

r7.close()
gz.close()
print("@@saved4")
