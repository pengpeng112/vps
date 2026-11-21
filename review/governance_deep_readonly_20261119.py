"""Bounded read-only diagnostics for the governance handoff; never deploys or logs in to API."""
from pathlib import Path
import argparse
import base64
import concurrent.futures
import datetime
import hashlib
import importlib.util
import json
import re
import shlex
import time

import paramiko
import yaml

ROOT = Path(__file__).resolve().parents[1]


def audit_module():
    spec = importlib.util.spec_from_file_location("readonly_audit", ROOT / "review/cf_postdeploy_audit_20261118.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def key_connect(host, key_path, jump=None):
    c = paramiko.SSHClient()
    c.load_system_host_keys()
    c.set_missing_host_key_policy(paramiko.RejectPolicy())
    sock = jump.get_transport().open_channel("direct-tcpip", (host, 22), ("127.0.0.1", 0), timeout=12) if jump else None
    try:
        c.connect(host, port=22, username="root", key_filename=str(key_path), sock=sock,
                  timeout=12, banner_timeout=12, auth_timeout=12, look_for_keys=False, allow_agent=False)
        return c
    except Exception:
        c.close()
        if sock:
            sock.close()
        raise


def read_bytes(c, path):
    _, out, err = c.exec_command("cat " + shlex.quote(path), timeout=12)
    data = out.read()
    err.read()
    if out.channel.recv_exit_status() != 0:
        raise OSError("controlled read failed")
    return data


def ws_probe(c, run, node, edge):
    # The authenticated WS path is delivered over stdin, never exported to evidence.
    host = node["servername"]
    key = base64.b64encode(b"CodexROProbe2026").decode()
    expected = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
    url = "https://" + host + ":2053" + node["ws-opts"]["path"]
    # Strict certificate checks remain enabled. curl may return 28 after a successful upgrade.
    conf = 'url = ' + json.dumps(url) + '\n'
    script = "curl --noproxy '*' --http1.1 --resolve " + shlex.quote(host + ":2053:" + edge)
    script += " --connect-timeout 4 --max-time 5 -sS -D - -o /dev/null"
    for header in ("Connection: Upgrade", "Upgrade: websocket", "Sec-WebSocket-Version: 13", "Sec-WebSocket-Key: " + key):
        script += " -H " + shlex.quote(header)
    script += " --config - <<'CODEX_RO_CURL'\n" + conf + "CODEX_RO_CURL\n"
    r = run(c, script, timeout=9)
    statuses = re.findall(r"^HTTP/\S+\s+(\d+)", r["stdout"], re.M)
    accepts = re.findall(r"^sec-websocket-accept:\s*(\S+)", r["stdout"], re.M | re.I)
    err = r["stderr"].lower()
    category = "timeout" if "timed out" in err else "certificate" if "certificate" in err else "connect" if "connect" in err else "other" if err else None
    return {"edge": edge, "domain": host, "status": statuses[-1] if statuses else None,
            "accept_valid": expected in accepts, "curl_rc": r["rc"], "error_category": category,
            "strict_tls": True}


def device(cname, port, gz, ns):
    c = None
    out = {"at": datetime.datetime.now().astimezone().isoformat(timespec="seconds")}
    try:
        c = ns["connect"]("127.0.0.1", port, "root", ns["DEV_PW"], gz)
        run = ns["run"]
        cfg = yaml.safe_load(read_bytes(c, "/etc/mihomo/config.yaml"))
        out["system"] = run(c, """date '+%F %T %z'
cat /proc/uptime
cat /proc/sys/kernel/random/boot_id
awk '/MemTotal|MemAvailable/ {print}' /proc/meminfo
df -k /overlay /tmp
crontab -l 2>/dev/null | awk '/reboot/ && $0 !~ /^[[:space:]]*#/ {print}'
tc qdisc show dev wan 2>/dev/null
command -v python3 || true
command -v flock || true
""")
        out["cf_ws"] = []
        for node in cfg.get("proxies", []):
            if node.get("name") in ("CF2D-HK-01", "CF2D-BWG-01"):
                for edge in ("162.159.135.42", "104.18.32.7"):
                    out["cf_ws"].append(ws_probe(c, run, node, edge))
        out["delay_rounds"] = []
        for idx in range(3):
            row = {}
            for node in ("CF2D-HK-01", "CF2D-BWG-01"):
                value = ns["get_json"](c, "curl --noproxy '*' -fsS -m 9 'http://127.0.0.1:9090/proxies/" + node + "/delay?timeout=6500&url=https%3A%2F%2Fwww.gstatic.com%2Fgenerate_204'")
                row[node] = {k: value[k] for k in ("delay", "unavailable", "rc") if k in value}
            out["delay_rounds"].append(row)
            if idx < 2:
                time.sleep(1)
        out["cf_fields"] = [{"name": p["name"], "edge": p["server"], "sni": p.get("servername"),
                             "host_matches_sni": p.get("ws-opts", {}).get("headers", {}).get("Host") == p.get("servername"),
                             "uuid_sha256": hashlib.sha256(str(p.get("uuid", "")).encode()).hexdigest(),
                             "path_sha256": hashlib.sha256(str(p.get("ws-opts", {}).get("path", "")).encode()).hexdigest()}
                            for p in cfg.get("proxies", []) if p.get("name") in ("CF2D-HK-01", "CF2D-BWG-01")]
    except Exception as exc:
        out["error_type"] = type(exc).__name__
    finally:
        if c:
            c.close()
    return cname, out


def server_metadata(c, ns):
    run = ns["run"]
    info = {"connected": True}
    info["system"] = run(c, """date -Is
uname -r
awk '/MemTotal|MemAvailable/ {print}' /proc/meminfo
df -k / /var
ip -4 route show default
tc -s qdisc show
ss -H -ltnu | awk '{print $1,$5}'
systemctl list-units --type=service --all --no-pager --no-legend | awk '/s-box|sing-box|hysteria|cf2053|bulk|qos|meter/ {print $1,$3,$4}'
sha256sum /usr/local/bin/gia-meter-sample.sh /etc/routermgmt/ratelimit-server-executor.sh 2>/dev/null
""")
    info["inbounds"] = []
    for svc in ("s-box-ws.service", "s-box-bulk.service", "sing-box.service"):
        r = run(c, "systemctl show " + svc + " -p MainPID --value 2>/dev/null")
        pid = r["stdout"].strip()
        if not pid.isdigit() or pid == "0":
            continue
        try:
            argv = [x.decode() for x in read_bytes(c, "/proc/" + pid + "/cmdline").split(b"\0") if x]
            config_path = next(argv[i + 1] for i, x in enumerate(argv[:-1]) if x in ("-c", "--config"))
            raw = read_bytes(c, config_path)
            cfg = json.loads(raw)
            for x in cfg.get("inbounds", []):
                tr = x.get("transport") or {}
                tls = x.get("tls") or {}
                info["inbounds"].append({"service": svc, "type": x.get("type"), "tag": x.get("tag"),
                                         "port": x.get("listen_port"), "transport": tr.get("type"),
                                         "tls_enabled": tls.get("enabled"), "users_count": len(x.get("users") or []),
                                         "uuid_sha256": [hashlib.sha256(str(u.get("uuid", "")).encode()).hexdigest() for u in x.get("users", []) if u.get("uuid")],
                                         "path_sha256": hashlib.sha256(str(tr.get("path", "")).encode()).hexdigest()})
        except Exception as exc:
            info["inbounds"].append({"service": svc, "metadata_error": type(exc).__name__})
    info["meter_inventory"] = run(c, """for p in /var/lib/gia-meter/samples.jsonl /etc/routermgmt/ratelimit-server/manifests.json /etc/routermgmt/ratelimit-server/config.json; do
  if [ -f "$p" ]; then stat -c '%n %s %y' "$p"; fi
done
command -v vnstat || true
""")
    return info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", required=True)
    args = ap.parse_args()
    dest = Path(args.output)
    if dest.exists():
        raise SystemExit("refusing to overwrite evidence")
    a = audit_module()
    ns = a.helpers()
    report = {"started": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
              "production_writes": False, "api_login": False, "devices": {}, "servers": {}}
    clients = []
    try:
        gz = ns["connect"]("134.175.151.213", 28722, "ubuntu", ns["DEV_PW"])
        clients.append(gz)
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            futs = [pool.submit(device, n, a.PORTS[n], gz, ns) for n in ("R1", "R2", "R4", "R5", "R8", "R9")]
            for f in concurrent.futures.as_completed(futs):
                n, row = f.result()
                report["devices"][n] = row
                print(json.dumps({"device": n, "error": row.get("error_type"), "delay_rounds": row.get("delay_rounds"), "ws": row.get("cf_ws")}, ensure_ascii=False), flush=True)
        ssh_dir = Path.home() / ".ssh"
        hk = None
        try:
            hk = key_connect("191.223.215.12", ssh_dir / "id_ed25519_dmit_hkg")
            clients.append(hk)
            report["servers"]["HK"] = server_metadata(hk, ns)
        except Exception as exc:
            report["servers"]["HK"] = {"connected": False, "error_type": type(exc).__name__}
        if hk:
            try:
                bwg = key_connect("74.82.193.113", ssh_dir / "id_ed25519_bwg", hk)
                clients.append(bwg)
                report["servers"]["BWG"] = server_metadata(bwg, ns)
            except Exception as exc:
                report["servers"]["BWG"] = {"connected": False, "error_type": type(exc).__name__}
        try:
            gia = ns["connect"]("154.36.139.155", 22, "root", ns["GIA_PW"])
            clients.append(gia)
            report["servers"]["GIA"] = server_metadata(gia, ns)
            data = ns["get_json"](gia, "nft -j list table inet gia_usage_meter")
            elems = data.get("nftables", [])
            report["servers"]["GIA"]["meter_counters"] = [{k: e["counter"].get(k) for k in ("name", "packets", "bytes")} for e in elems if "counter" in e]
        except Exception as exc:
            report["servers"]["GIA"] = {"connected": False, "error_type": type(exc).__name__}
    finally:
        for c in reversed(clients):
            c.close()
        report["finished"] = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
        dest.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"output": str(dest), "servers": {n: {"connected": x.get("connected"), "error": x.get("error_type"), "inbounds": x.get("inbounds")} for n, x in report["servers"].items()}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
