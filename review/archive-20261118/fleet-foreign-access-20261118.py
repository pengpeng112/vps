"""Fleet foreign-access fast check (read-only). Compact output per device."""
from pathlib import Path
import datetime, json, paramiko

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "logs/fleet-foreign-access-20261118.json"
BT = chr(96)
DEVICES = [("R1",13222),("R2",13223),("R3",13227),("R4",13224),("R5",13225),("R6",13226),("R8",13230),("R9",13231)]

def controlled_row(p, n):
    for line in (ROOT / p).read_text(encoding="utf-8-sig").splitlines():
        cells = [x.strip() for x in line.strip().strip("|").split("|")]
        if line.startswith("|") and cells[0] == n:
            return "|".join(cells[1:])
    raise ValueError(n)

PW = __import__("re").findall(BT + "([^" + BT + "]+)" + BT,
    controlled_row("backups/SN-R系列凭证总览.md", "**root/SSH 口令**"))[0]

DEV_SCRIPT = r'''
echo '== groups =='
for g in PROXY NODE-PICK US-MEDIA AUTO; do
  printf '%s=' "$g"; curl --noproxy '*' -s -m 4 http://127.0.0.1:9090/proxies/$g | jsonfilter -e '@.now' || echo n/a
done
echo '== foreign =='
printf 'gstatic   '; curl --noproxy '*' -s -o /dev/null -w '%{http_code} %{time_total}s' -m 6 https://www.gstatic.com/generate_204; echo " rc=$?"
printf 'google    '; curl --noproxy '*' -s -o /dev/null -w '%{http_code} %{time_total}s' -m 6 https://www.google.com/generate_204; echo " rc=$?"
printf 'youtube   '; curl --noproxy '*' -s -o /dev/null -w '%{http_code} %{time_total}s' -m 6 https://www.youtube.com/generate_204; echo " rc=$?"
printf 'github    '; curl --noproxy '*' -s -o /dev/null -w '%{http_code} %{time_total}s' -m 6 https://www.github.com/generate_204; echo " rc=$?"
printf 'wikipedia '; curl --noproxy '*' -s -o /dev/null -w '%{http_code} %{time_total}s' -m 6 https://www.wikipedia.org; echo " rc=$?"
printf 'x.com     '; curl --noproxy '*' -s -o /dev/null -w '%{http_code} %{time_total}s' -m 6 https://x.com; echo " rc=$?"
printf 'panel666  '; curl --noproxy '*' -s -o /dev/null -w '%{http_code} %{time_total}s' -m 6 https://www.666clouds.com; echo " rc=$?"
printf 'baidu     '; curl --noproxy '*' -s -o /dev/null -w '%{http_code} %{time_total}s' -m 6 https://www.baidu.com; echo " rc=$?"
printf 'exitip    '; curl --noproxy '*' -s -m 6 https://ipinfo.io/ip; echo " rc=$?"
echo '== delays =='
printf 'BWG-gs '; curl --noproxy '*' -s -m 8 "http://127.0.0.1:9090/proxies/BWG-HY2-JPTY-01/delay?timeout=6000&url=http://www.gstatic.com/generate_204"; echo
printf 'CF1-gs '; curl --noproxy '*' -s -m 8 "http://127.0.0.1:9090/proxies/CF-CDN-GIA-01/delay?timeout=6000&url=http://www.gstatic.com/generate_204"; echo
printf 'CF2-gs '; curl --noproxy '*' -s -m 8 "http://127.0.0.1:9090/proxies/CF2-CDN-GIA-01/delay?timeout=6000&url=http://www.gstatic.com/generate_204"; echo
'''

def connect_pw(host, port, user, pw, jump=None, tmo=12):
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    sock = jump.get_transport().open_channel("direct-tcpip", (host, port), ("127.0.0.1", 0), timeout=tmo) if jump else None
    c.connect(host, port=port, username=user, password=pw, sock=sock,
              timeout=tmo, banner_timeout=tmo, auth_timeout=tmo, look_for_keys=False, allow_agent=False)
    return c

RES = {"started": datetime.datetime.now().astimezone().isoformat(timespec="seconds"), "writes": False, "devices": {}}
gz = connect_pw("134.175.151.213", 28722, "ubuntu", PW)
for name, port in DEVICES:
    entry = {"port": port}
    try:
        dev = connect_pw("127.0.0.1", port, "root", PW, jump=gz)
    except Exception as e:
        entry.update(reachable=False, error=type(e).__name__)
        RES["devices"][name] = entry
        print("%-4s UNREACHABLE %s" % (name, type(e).__name__), flush=True)
        continue
    try:
        i, o, e = dev.exec_command("sh -s", timeout=150)
        i.write(DEV_SCRIPT)
        i.channel.shutdown_write()
        out = o.read().decode("utf-8", "replace")
        entry.update(reachable=True, raw=out)
        dev.close()
        RES["devices"][name] = entry
        gs = {}
        for line in out.splitlines():
            if "=" in line and not line.startswith("rc="):
                k, _, v = line.partition("=")
                if k in ("PROXY", "NODE-PICK", "US-MEDIA", "AUTO"):
                    gs[k] = v
        print("%-4s PROXY=%s NODE-PICK=%s" % (name, gs.get("PROXY"), gs.get("NODE-PICK")), flush=True)
        for l in out.splitlines():
            l = l.strip()
            if not l or l.startswith("== "):
                continue
            print("     " + l, flush=True)
    except Exception as ex:
        print("%-4s ERROR %s: %s" % (name, type(ex).__name__, str(ex)[:150]), flush=True)
        entry["error"] = str(ex)[:300]
        try: dev.close()
        except Exception: pass

gz.close()
RES["finished"] = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
OUT.write_text(json.dumps(RES, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("saved:", OUT)
