"""CF09 part1: LAN-source forced-path test (real PC behind R2).

Pin R2 PROXY selector to each new node -> PC curl 204/exit_ip/download -> restore AUTO.
Selector PUT is runtime+cache-persistent; restore verified via 9090 now == AUTO.
"""
from pathlib import Path
import json
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "review/fleet-qos-readonly-audit-20261117.py"
ns = {"__file__": str(HELPER)}
prefix, sep, _ = HELPER.read_text(encoding="utf-8-sig").partition("\ntry:\n")
exec(compile(prefix, "<helper>", "exec"), ns)
connect, run, get_json = ns["connect"], ns["run"], ns["get_json"]

EXPECT = {"CF2D-HK-01": "191.223.215.12", "CF2D-BWG-01": "74.82.193.113"}


def pc_curl(url: str, extra=None, timeout=20) -> dict:
    cmd = ["curl", "-s", "-m", str(timeout), "--noproxy", "*", "-o", "-", "-w",
           "\n%{http_code} %{size_download} %{time_total}", url]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout + 5)
    lines = p.stdout.rsplit("\n", 1)
    body = lines[0] if lines else ""
    meta = lines[1].split() if len(lines) > 1 and lines[1] else []
    return {"body": body.strip()[:60], "http": meta[0] if meta else "?", "bytes": meta[1] if len(meta) > 1 else "?",
            "time": meta[2] if len(meta) > 2 else "?"}


def pin(r2, name):
    r = run(r2, f"""curl --noproxy '*' -sS -m 5 -X PUT -H 'Content-Type: application/json' \
-d '{json.dumps({"name": name})}' http://127.0.0.1:9090/proxies/PROXY -o /dev/null -w '%{{http_code}}'""")
    return r["stdout"].strip()


def group_now(r2, group):
    g = get_json(r2, f"curl --noproxy '*' -fsS -m 5 http://127.0.0.1:9090/proxies/{group}")
    return g.get("now"), g.get("all")


def main() -> int:
    results = {}
    gz = connect("134.175.151.213", 28722, "ubuntu", ns["DEV_PW"])
    try:
        r2 = connect("127.0.0.1", 13223, "root", ns["DEV_PW"], gz)
        try:
            # 基线：PROXY 当前选择
            before, _ = group_now(r2, "PROXY")
            auto_before, _ = group_now(r2, "AUTO")
            print(f"baseline: PROXY={before} AUTO={auto_before}")
            assert before == "AUTO", "基线非 AUTO，先查现场"

            for node, expect_ip in EXPECT.items():
                code = pin(r2, node)
                time.sleep(1)
                now, _ = group_now(r2, "PROXY")
                print(f"pinned {node}: PUT={code} now={now}")
                if now != node:
                    print(f"FAIL: 钉选未生效（now={now}）")
                    pin(r2, "AUTO")
                    return 1
                g204 = pc_curl("https://www.gstatic.com/generate_204")
                ip = pc_curl("https://api.ipify.org")
                dl = pc_curl("https://speed.cloudflare.com/__down?bytes=102400", timeout=40)
                results[node] = {"lan_204": g204["http"], "lan_exit_ip": ip["body"],
                                 "lan_exit_ok": ip["body"] == expect_ip,
                                 "lan_dl": f'{dl["http"]}/{dl["bytes"]}B/{dl["time"]}s'}
                print(f"  PC(LAN) gstatic={g204['http']} exit={ip['body']} (expect {expect_ip}) dl={dl['http']}/{dl['bytes']}B/{dl['time']}s")

            # 还原 AUTO（必须）
            code = pin(r2, "AUTO")
            time.sleep(1)
            now, _ = group_now(r2, "PROXY")
            print(f"restore: PUT={code} PROXY now={now}")
            results["restored"] = now == "AUTO"

            ok = all(r.get("lan_exit_ok") and r.get("lan_204") == "204" for r in results.values() if isinstance(r, dict)) and results["restored"]
            (ROOT / "backups/cf2d-golive-20261118/cf09-lan.json").write_text(
                json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
            print("CF09-LAN:", "PASS" if ok else "FAIL")
            return 0 if ok else 1
        finally:
            r2.close()
    finally:
        gz.close()


if __name__ == "__main__":
    sys.exit(main())
