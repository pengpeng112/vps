"""Per-device x per-line connectivity matrix (read-only).

For each online router: enumerate its bound proxy nodes, run mihomo delay test
per node (gstatic), test AIRPORT group, and one user-path google check.
Output: compact matrix + JSON evidence.
"""
from pathlib import Path
import datetime
import json
import re
import sys

import paramiko

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
BT = chr(96)
OUT = ROOT / "logs/fleet-line-matrix-20261118.json"
DEVICES = [("R1", 13222), ("R2", 13223), ("R3", 13227), ("R4", 13224),
           ("R5", 13225), ("R6", 13226), ("R8", 13230), ("R9", 13231)]
GROUP_TYPES = ("selector", "urltest", "fallback", "loadbalance")
BUILTIN = {"DIRECT", "REJECT", "REJECT-DROP", "PASS", "PASS-RULE", "COMPATIBLE", "GLOBAL"}


def controlled_row(p, n):
    for line in (ROOT / p).read_text(encoding="utf-8-sig").splitlines():
        cells = [x.strip() for x in line.strip().strip("|").split("|")]
        if line.startswith("|") and cells[0] == n:
            return "|".join(cells[1:])
    raise ValueError(n)


PW = re.findall(BT + "([^" + BT + "]+)" + BT,
                controlled_row("backups/SN-R系列凭证总览.md", "**root/SSH 口令**"))[0]


def connect_pw(host, port, user, pw, jump=None, tmo=12):
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    sock = jump.get_transport().open_channel("direct-tcpip", (host, port), ("127.0.0.1", 0), timeout=tmo) if jump else None
    c.connect(host, port=port, username=user, password=pw, sock=sock,
              timeout=tmo, banner_timeout=tmo, auth_timeout=tmo, look_for_keys=False, allow_agent=False)
    return c


RES = {"started": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
       "writes": False, "devices": {}}
gz = connect_pw("134.175.151.213", 28722, "ubuntu", PW)
for name, port in DEVICES:
    entry = {}
    try:
        dev = connect_pw("127.0.0.1", port, "root", PW, jump=gz)
    except Exception as e:
        entry["reachable"] = False
        RES["devices"][name] = entry
        print("%-4s UNREACHABLE %s" % (name, type(e).__name__), flush=True)
        continue
    entry["reachable"] = True
    # 1) 节点清单
    i, o, e = dev.exec_command("curl --noproxy '*' -s -m 6 http://127.0.0.1:9090/proxies", timeout=20)
    try:
        doc = json.loads(o.read().decode()).get("proxies", {})
        nodes, groups = [], {}
        for n, p in doc.items():
            t = (p.get("type") or "").lower()
            if t in GROUP_TYPES:
                if n != "GLOBAL":
                    groups[n] = p.get("now")
            elif n not in BUILTIN and not n.startswith("AP |"):
                nodes.append(n)
        nodes.sort()
    except Exception as ex:
        nodes, groups = [], {"error": str(ex)[:80]}
    entry["groups"] = groups
    entry["node_count"] = len(nodes)

    # 2) 逐节点 delay（gstatic）
    results = {}
    for n in nodes:
        i, o, e = dev.exec_command(
            "curl --noproxy '*' -s -m 7 \"http://127.0.0.1:9090/proxies/%s/delay?timeout=5000&url=http://www.gstatic.com/generate_204\"" % n,
            timeout=12)
        r = o.read().decode().strip()
        m = re.search(r'"delay":(\d+)', r)
        results[n] = ("%sms" % m.group(1)) if m else ("FAIL" if r else "NO-RESP")
    # 3) AIRPORT 组
    i, o, e = dev.exec_command(
        "curl --noproxy '*' -s -m 7 \"http://127.0.0.1:9090/proxies/AIRPORT/delay?timeout=5000&url=http://www.gstatic.com/generate_204\"", timeout=12)
    r = o.read().decode().strip()
    m = re.search(r'"delay":(\d+)', r)
    results["AIRPORT(机场)"] = ("%sms" % m.group(1)) if m else ("FAIL" if r else "-")
    # 4) 用户路径 google
    i, o, e = dev.exec_command(
        "curl -x http://127.0.0.1:7890 -s -o /dev/null -w '%{http_code} %{time_total}s' -m 8 https://www.google.com/generate_204", timeout=15)
    entry["google_userpath"] = o.read().decode().strip()
    entry["nodes"] = results
    dev.close()
    RES["devices"][name] = entry
    ok = [k for k, v in results.items() if v.endswith("ms")]
    print("%-4s 节点%d个 可用%d/%d | google=%s | PROXY=%s" % (
        name, len(nodes), len(ok), len(results), entry["google_userpath"], groups.get("PROXY")), flush=True)
    for k in sorted(results):
        print("      %-22s %s" % (k, results[k]), flush=True)

gz.close()
RES["finished"] = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
OUT.write_text(json.dumps(RES, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("saved:", OUT)
