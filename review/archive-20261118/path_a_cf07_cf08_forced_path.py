"""CF07/CF08: per-new-node forced-path acceptance on R2 via TEMP ISOLATED mihomo instance.

- Independent dir/ports (127.0.0.1:19090 ctl, :17891 mixed), ONLY the one node under test
- No TUN/redir/transparent rules, formal process/cache untouched
- Verifies: gstatic 204, exit IP (expect origin egress), 64-128KiB file with bytes/time
"""
from pathlib import Path
import json
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "review/fleet-qos-readonly-audit-20261117.py"
ns = {"__file__": str(HELPER)}
prefix, sep, _ = HELPER.read_text(encoding="utf-8-sig").partition("\ntry:\n")
exec(compile(prefix, "<helper>", "exec"), ns)
connect, run = ns["connect"], ns["run"]

CTRL = ROOT / "backups/cf2d-golive-20261118"
EXPECT_EXIT = {"CF2D-HK-01": "191.223.215.12", "CF2D-BWG-01": "74.82.193.113"}


def node_yaml(name: str, sni: str, host: str, uuid: str, path: str) -> str:
    return f'''port: 0
socks-port: 0
mixed-port: 17891
bind-address: 127.0.0.1
allow-lan: false
mode: rule
log-level: warning
external-controller: 127.0.0.1:19090
proxies:
  - name: "{name}"
    type: vless
    server: {host}
    port: 2053
    uuid: {uuid}
    tls: true
    servername: {sni}
    network: ws
    ws-opts:
      path: {path}
      headers:
        Host: {sni}
rules:
  - MATCH,{name}
'''


def main() -> int:
    params = json.loads((ROOT / "backups/.cf-node-params.json").read_text(encoding="utf-8-sig"))
    specs = {
        "CF2D-HK-01": {"sni": "hk.6849271.xyz", "host": "104.18.32.7"},
        "CF2D-BWG-01": {"sni": "usbwg.6849271.xyz", "host": "162.159.135.42"},
    }
    gz = connect("134.175.151.213", 28722, "ubuntu", ns["DEV_PW"])
    results = {}
    try:
        r2 = connect("127.0.0.1", 13223, "root", ns["DEV_PW"], gz)
        try:
            for name, spec in specs.items():
                cfg = node_yaml(name, spec["sni"], spec["host"], params["uuid"], params["ws_path"])
                i, o, e = r2.exec_command(f"mkdir -p /root/cf2d-test && cat > /root/cf2d-test/config.yaml && chmod 600 /root/cf2d-test/config.yaml && echo WROTE", timeout=20)
                i.write(cfg)
                i.channel.shutdown_write()
                if "WROTE" not in o.read().decode():
                    print(f"{name}: config 写入失败")
                    return 1

                run(r2, "cd /root/cf2d-test && nohup mihomo -d /root/cf2d-test > /root/cf2d-test/log.txt 2>&1 & echo $! > /root/cf2d-test/pid; sleep 4")
                v = run(r2, r"""P=17891
code204=$(curl -s -o /dev/null -w '%{http_code}' -m 15 -x http://127.0.0.1:$P https://www.gstatic.com/generate_204)
exit_ip=$(curl -s -m 15 -x http://127.0.0.1:$P https://api.ipify.org)
t0=$(date +%s%N)
bytes=$(curl -s -m 40 -x http://127.0.0.1:$P -o /tmp/cf2d-dl.bin -w '%{size_download} %{http_code}' 'https://speed.cloudflare.com/__down?bytes=102400')
t1=$(date +%s%N)
ms=$(( (t1-t0)/1000000 ))
echo "RESULT|$code204|$exit_ip|$bytes|$ms"
""", timeout=90)
                out = v["stdout"].strip()
                print(f"{name}: {out}")
                parts = out.split("|") if out.startswith("RESULT") else []
                if len(parts) == 5:
                    results[name] = {"http_204": parts[1] == "204", "exit_ip": parts[2],
                                     "exit_ok": parts[2] == EXPECT_EXIT[name],
                                     "dl": parts[3], "ms": parts[4]}
                run(r2, "kill $(cat /root/cf2d-test/pid) 2>/dev/null; sleep 1; rm -rf /root/cf2d-test /tmp/cf2d-dl.bin; echo CLEANED")
            ok = all(r.get("http_204") and r.get("exit_ok") and int(r.get("dl", "0").split()[0]) >= 65536 for r in results.values())
            (CTRL / "cf07-cf08.json").write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n",
                                                 encoding="utf-8", newline="\n")
            print("CF07/08:", "PASS" if ok else "FAIL", json.dumps(results, ensure_ascii=False))
            return 0 if ok else 1
        finally:
            r2.close()
    finally:
        gz.close()


if __name__ == "__main__":
    sys.exit(main())
