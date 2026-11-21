"""DMIT standby usability probe (read-only).

- Parse DMIT endpoint from controlled archive (values never printed).
- GZ -> DMIT: ping + TCP ssh port.
- DMIT: uptime/docker/services/listeners; GIA reachability from DMIT (global side);
  DMIT own egress test.
- R2 -> DMIT: TCP reachability on DMIT's exposed proxy ports (fleet-side direction).
No writes anywhere.
"""
from pathlib import Path
import datetime
import json
import paramiko
import re

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "logs/dmit-usability-probe-20261117.json"
BT = chr(96)
GIA = "154.36.139.155"


def controlled_row(path, name):
    for line in (ROOT / path).read_text(encoding="utf-8-sig").splitlines():
        cells = [x.strip() for x in line.strip().strip("|").split("|")]
        if line.startswith("|") and cells[0] == name:
            return "|".join(cells[1:])
    return None


def quoted(v):
    return re.findall(BT + "([^" + BT + "]+)" + BT, v)


# --- parse DMIT archive in memory; print only field labels, never values ---
ARC = ROOT / "backups/DMIT美西-凭据档案-20261001.md"
labels = []
for line in ARC.read_text(encoding="utf-8-sig").splitlines():
    if line.strip().startswith("|"):
        labels.append(line.strip().strip("|").split("|")[0].strip())
print("archive fields:", labels, flush=True)

ips = set(re.findall(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b", ARC.read_text(encoding="utf-8-sig")))
print("archive IPs (labels only):", len(ips), "unique", flush=True)
DMIT_IP = "69.63.219.156"  # per 总览 §1.1 authoritative

pw = None
port = 22
user = "root"
for f in labels:
    if pw is None:
        try:
            q = quoted(controlled_row("backups/DMIT美西-凭据档案-20261001.md", f))
            if q:
                pw = q[-1]
        except Exception:
            continue
raw = ARC.read_text(encoding="utf-8-sig")
m = re.search(r"端口[^\n]*?" + BT + r"(\d+)" + BT, raw)
if m:
    port = int(m.group(1))
m = re.search(r"用户[^\n]*?" + BT + r"(\w+)" + BT, raw)
if m:
    user = m.group(1)
print("endpoint: %s@%s:%s pw=%s" % (user, DMIT_IP, port, "found" if pw else "NOT-FOUND"), flush=True)
assert pw, "DMIT credential parse failed"


def connect(host, port, user, pw, jump=None, tmo=15):
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    sock = None
    if jump:
        sock = jump.get_transport().open_channel("direct-tcpip", (host, port), ("127.0.0.1", 0), timeout=tmo)
    c.connect(host, port=port, username=user, password=pw, sock=sock,
              timeout=tmo, banner_timeout=tmo, auth_timeout=tmo, look_for_keys=False, allow_agent=False)
    return c


def run(c, script, timeout=45):
    i, o, e = c.exec_command("sh -s", timeout=timeout)
    i.write(script)
    i.channel.shutdown_write()
    return {"rc": o.channel.recv_exit_status(), "stdout": o.read().decode("utf-8", "replace")[-6000:],
            "stderr": e.read().decode("utf-8", "replace")[-400:]}


RES = {"started": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
       "writes": False, "dmit_ip": DMIT_IP}

gz = connect("134.175.151.213", 28722, "root" if False else "ubuntu",
             quoted(controlled_row("backups/SN-R系列凭证总览.md", "**root/SSH 口令**"))[0])
RES["gz_to_dmit"] = run(gz, """ping -c3 -W2 %s 2>&1 | tail -2
timeout 3 bash -c 'echo > /dev/tcp/%s/%s' 2>/dev/null && echo 'ssh-port ok' || echo 'ssh-port fail'
""" % (DMIT_IP, DMIT_IP, port), timeout=60)

try:
    dm = connect(DMIT_IP, port, user, pw)
    RES["dmit"] = run(dm, """date -Is; uptime
echo '--- services ---'
systemctl list-units --type=service --state=running 2>/dev/null | grep -Ei "s-box|xray|sing|v2ray|hysteria|nginx|docker" | head -10
docker ps --format '{{.Names}} {{.Status}} {{.Ports}}' 2>/dev/null | head -10
echo '--- listeners ---'
ss -tlnp 2>/dev/null | awk '{print $4, $6}' | grep -vE '127.0.0.1|::1' | head -15
echo '--- GIA from DMIT (global side) ---'
ping -c3 -W2 %s 2>&1 | tail -2
for p in 443 2053 2054; do timeout 3 bash -c 'echo > /dev/tcp/%s/'$p 2>/dev/null && echo "GIA:$p ok" || echo "GIA:$p fail"; done
echo '--- DMIT own egress ---'
printf 'exitip '; curl -s -m 10 https://api.ip.sb/ip 2>/dev/null; echo
printf 'google '; curl -s -o /dev/null -w '%%{http_code} %%{time_total}s' -m 10 https://www.google.com/generate_204; echo
""" % (GIA, GIA), timeout=120)
    dm.close()
except Exception as e:
    RES["dmit"] = {"error": "%s: %s" % (type(e).__name__, str(e)[:200])}
    print("DMIT ssh:", RES["dmit"]["error"], flush=True)

# fleet-side direction: R2 -> DMIT exposed ports (from device WAN)
try:
    sock = gz.get_transport().open_channel("direct-tcpip", ("127.0.0.1", 13223), ("127.0.0.1", 0), timeout=12)
    r2 = connect("127.0.0.1", 13223, "root",
                 quoted(controlled_row("backups/SN-R系列凭证总览.md", "**root/SSH 口令**"))[0], jump=gz)
    RES["r2_to_dmit"] = run(r2, """ping -c3 -W2 %s 2>&1 | tail -2
for p in 443 2053 8443 34567; do timeout 3 bash -c 'echo > /dev/tcp/%s/'$p 2>/dev/null && echo "DMIT:$p ok" || echo "DMIT:$p fail"; done
""" % (DMIT_IP, DMIT_IP), timeout=60)
    r2.close()
except Exception as e:
    RES["r2_to_dmit"] = {"error": type(e).__name__}
    print("R2 probe:", RES["r2_to_dmit"]["error"], flush=True)

gz.close()
RES["finished"] = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
OUT.write_text(json.dumps(RES, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
for k in ("gz_to_dmit", "dmit", "r2_to_dmit"):
    v = RES.get(k, {})
    print("== %s ==" % k)
    print(v.get("stdout") or v.get("error"))
