"""Fleet link audit v2 (read-only): mgmt tunnels + router->VPS node health + foreign access.

v1 修正：可达设备结果落入 JSON；GZ→VPS TCP 用参数展开（stdin 脚本内禁 heredoc）；
egress 打印带 rc 与双 IP 回显；补 baidu 国内对照与 US666/BWG-HY2 健康历史时间线。
Evidence: logs/fleet-link-audit-20261117.json
"""
from pathlib import Path
import datetime
import json
import paramiko

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "logs/fleet-link-audit-20261117.json"
BT = chr(96)


def controlled_row(path, name):
    for line in (ROOT / path).read_text(encoding="utf-8-sig").splitlines():
        cells = [x.strip() for x in line.strip().strip("|").split("|")]
        if line.startswith("|") and cells[0] == name:
            return "|".join(cells[1:])
    raise ValueError("controlled credential field missing: %s" % name)


def quoted(v):
    return __import__("re").findall(BT + "([^" + BT + "]+)" + BT, v)


DEV_PW = quoted(controlled_row("backups/SN-R系列凭证总览.md", "**root/SSH 口令**"))[0]

DEVICES = [("R1", 13222), ("R2", 13223), ("R3", 13227), ("R4", 13224), ("R5", 13225),
           ("R6", 13226), ("R7", 13229), ("R8", 13230), ("R9", 13231), ("R10", 13232),
           ("R11", 13233), ("GL", 13234), ("ATHENA", 13228)]

VPS_TCP = [("GIA-vless443", "154.36.139.155", "443"), ("GIA-ws2053", "154.36.139.155", "2053"),
           ("GIA-bulk2054", "154.36.139.155", "2054"), ("BWG-vless443", "74.82.193.113", "443"),
           ("BWG-ws2053", "74.82.193.113", "2053"), ("CF1-2053", "104.18.32.7", "2053"),
           ("CF2-2053", "162.159.135.42", "2053")]


def connect(host, port, user, pw, jump=None, tmo=12):
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    sock = None
    if jump:
        sock = jump.get_transport().open_channel("direct-tcpip", (host, port), ("127.0.0.1", 0), timeout=tmo)
    c.connect(host, port=port, username=user, password=pw, sock=sock,
              timeout=tmo, banner_timeout=tmo, auth_timeout=tmo,
              look_for_keys=False, allow_agent=False)
    return c


def run(c, script, timeout=45):
    i, o, e = c.exec_command("sh -s", timeout=timeout)
    i.write(script)
    i.channel.shutdown_write()
    return {"rc": o.channel.recv_exit_status(),
            "stdout": o.read().decode("utf-8", "replace"),
            "stderr": e.read().decode("utf-8", "replace")[-400:]}


def raw(c, cmd, timeout=15):
    i, o, e = c.exec_command(cmd, timeout=timeout)
    return o.read().decode("utf-8", "replace")


RES = {"started": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
       "writes": False, "gz": {}, "devices": {}}

gz = connect("134.175.151.213", 28722, "ubuntu", DEV_PW)
RES["gz"]["health"] = run(gz, """date -Is
for s in gz-api nginx postgresql; do printf '%s=' "$s"; systemctl is-active $s; done
curl --noproxy '*' -fsS -m 5 http://127.0.0.1:8080/api/v1/health && echo
""")
RES["gz"]["listeners"] = run(gz, "ss -tln | awk '$4 ~ /:132[0-9][0-9]$/ {print $4}' | sort | tr '\\n' ' '; echo")
RES["gz"]["db"] = run(gz, """sudo -u postgres psql -X -A -t -F'|' routermgmt <<'SQL'
SELECT id, lifecycle_status, current_config_version, highest_accepted_version,
       last_heartbeat_at, round(extract(epoch from now()-last_heartbeat_at)) AS hb_age_s,
       current_exit_ip
FROM devices ORDER BY id;
SQL
""")
# GZ→VPS TCP：纯参数展开，stdin 脚本内不用 heredoc
tcp_args = " ".join("%s %s %s" % t for t in VPS_TCP)
RES["gz"]["vps_tcp"] = run(gz, """for a in """ + tcp_args + """; do :; done
set -- """ + tcp_args + """
while [ $# -ge 3 ]; do
  name=$1; ip=$2; port=$3; shift 3
  if timeout 3 bash -c 'echo > /dev/tcp/'$ip'/'$port 2>/dev/null; then echo "$name ok"; else echo "$name fail"; fi
done
""", timeout=90)

DEV_SCRIPT = """date -Is
ps | grep "[r]outermgmt-agent.sh" >/dev/null && echo agent=running || echo agent=not-running
for g in PROXY US-MEDIA NODE-PICK AUTO AIRPORT DOWNLOAD; do
  now=$(curl --noproxy '*' -s -m 4 http://127.0.0.1:9090/proxies/$g 2>/dev/null | jsonfilter -e '@.now' 2>/dev/null)
  echo "grp $g=${now:-n/a}"
done
echo '---egress---'
printf 'gstatic '; curl --noproxy '*' -s -o /dev/null -w '%{http_code} %{time_total}s' -m 12 https://www.gstatic.com/generate_204; echo " rc=$?"
printf 'google  '; curl --noproxy '*' -s -o /dev/null -w '%{http_code} %{time_total}s' -m 12 https://www.google.com/generate_204; echo " rc=$?"
printf 'youtube '; curl --noproxy '*' -s -o /dev/null -w '%{http_code} %{time_total}s' -m 12 https://www.youtube.com/generate_204; echo " rc=$?"
printf 'baidu   '; curl --noproxy '*' -s -o /dev/null -w '%{http_code} %{time_total}s' -m 8 https://www.baidu.com -o /dev/null; echo " rc=$?"
printf 'exitip_ipify '; curl --noproxy '*' -s -m 12 https://api.ipify.org; echo " rc=$?"
printf 'exitip_ifme  '; curl --noproxy '*' -s -m 12 http://ifconfig.me/ip; echo " rc=$?"
"""

for name, port in DEVICES:
    entry = {"port": port}
    try:
        dev = connect("127.0.0.1", port, "root", DEV_PW, jump=gz, tmo=10)
    except Exception as e:
        entry["reachable"] = False
        entry["error"] = type(e).__name__
        RES["devices"][name] = entry
        print("%-7s UNREACHABLE %s" % (name, type(e).__name__), flush=True)
        continue
    entry["reachable"] = True
    entry["basic"] = run(dev, DEV_SCRIPT, timeout=120)
    pj = raw(dev, "curl --noproxy '*' -s -m 8 http://127.0.0.1:9090/proxies 2>/dev/null", timeout=20)
    try:
        doc = json.loads(pj).get("proxies", {})
        groups, nodes = {}, []
        for n, p in doc.items():
            t = (p.get("type") or "").lower()
            if t in ("selector", "urltest", "fallback", "loadbalance"):
                if n != "GLOBAL":
                    groups[n] = p.get("now")
            elif n in ("DIRECT", "REJECT", "PASS", "COMPATIBLE"):
                continue
            else:
                h = p.get("history") or []
                nodes.append({"name": n, "type": p.get("type"), "alive": p.get("alive"),
                              "delay_ms": h[-1].get("delay") if h else None})
        entry["groups"] = groups
        entry["nodes"] = nodes
    except Exception as ex:
        entry["proxies_parse"] = "fail:%s" % ex
    if name in ("R1", "R2"):
        entry["node_history"] = {
            "US666": raw(dev, "curl --noproxy '*' -s -m 6 'http://127.0.0.1:9090/proxies/US666-CN2GIA-01' 2>/dev/null", 15),
            "BWG-HY2": raw(dev, "curl --noproxy '*' -s -m 6 'http://127.0.0.1:9090/proxies/BWG-HY2-JPTY-01' 2>/dev/null", 15),
        }
    if name == "R2":
        entry["meter_v3"] = run(dev, "cat /etc/routermgmt/state/proxy-meter-daily-v3.json 2>/dev/null; echo")
    dev.close()
    RES["devices"][name] = entry
    g = entry.get("groups", {})
    lines = [l for l in entry["basic"]["stdout"].splitlines()
             if l.split(" ")[0] in ("gstatic", "google", "youtube", "baidu", "exitip_ipify", "exitip_ifme")]
    dead = [n["name"] for n in entry.get("nodes", []) if n.get("alive") is False]
    print("%-7s ok PROXY=%s NODE-PICK=%s US-MEDIA=%s | %s" % (
        name, g.get("PROXY"), g.get("NODE-PICK"), g.get("US-MEDIA"),
        " | ".join(x.replace("  ", " ").strip() for x in lines)), flush=True)
    if dead:
        print("        dead_nodes: " + ",".join(dead[:10]), flush=True)

gz.close()
RES["finished"] = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
OUT.write_text(json.dumps(RES, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("saved:", OUT)
