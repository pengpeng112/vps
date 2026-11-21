"""Selected-field, read-only CF/GZ/R2 audit. No API login or production writes.

Only DNS/RDAP, TLS/WS handshakes, selected SQL and configuration reads run.
Credentials and WS paths stay in memory; exception messages are not exported.
Cold domains receive DNS/RDAP queries only, never WS/proxy traffic.
"""
from pathlib import Path
import base64
import concurrent.futures
import datetime
import hashlib
import json
import os
import socket
import ssl

import requests
import yaml

ROOT = Path(__file__).resolve().parents[1]
ACTIVE = ("hk.6849271.xyz", "usbwg.6849271.xyz")
EDGES = ("104.18.32.7", "162.159.135.42")


def http_json(url, **kwargs):
    with requests.Session() as session:
        session.trust_env = False
        r = session.get(url, timeout=(6, 10), **kwargs)
        return r.status_code, r.json() if r.ok else {}


def dns(name, kind):
    try:
        code, data = http_json("https://dns.google/resolve", params={"name": name, "type": kind})
        return {"name": name, "type": kind, "http": code, "rcode": data.get("Status"),
                "answers": [{"type": x["type"], "data": x["data"]} for x in data.get("Answer", [])]}
    except Exception as exc:
        return {"name": name, "type": kind, "error_type": type(exc).__name__}


def rdap(domain):
    try:
        code, data = http_json("https://rdap.org/domain/" + domain)
        return {"domain": domain, "http": code, "ldhName": data.get("ldhName"),
                "status": data.get("status"),
                "nameservers": [x.get("ldhName") for x in data.get("nameservers", [])]}
    except Exception as exc:
        return {"domain": domain, "error_type": type(exc).__name__}


def ws(host, edge, path):
    result = {"sni": host, "edge": edge, "port": 2053, "strict_certificate_validation": True}
    try:
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        with socket.create_connection((edge, 2053), timeout=7) as raw:
            with ssl.create_default_context().wrap_socket(raw, server_hostname=host) as conn:
                result["tls"] = conn.version()
                result["certificate_san"] = [v for k, v in conn.getpeercert().get("subjectAltName", []) if k == "DNS"]
                request = ("GET " + path + " HTTP/1.1\r\nHost: " + host +
                           "\r\nConnection: Upgrade\r\nUpgrade: websocket\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: " + key + "\r\n\r\n")
                conn.sendall(request.encode("ascii"))
                data = b""
                while b"\r\n\r\n" not in data and len(data) < 16384:
                    part = conn.recv(2048)
                    if not part:
                        break
                    data += part
                lines = data.split(b"\r\n\r\n", 1)[0].decode("latin1").split("\r\n")
                pieces = lines[0].split(" ", 2)
                result["http_status"] = int(pieces[1]) if len(pieces) > 1 and pieces[1].isdigit() else None
                headers = {}
                for line in lines[1:]:
                    if ":" in line:
                        k, v = line.split(":", 1)
                        headers[k.lower()] = v.strip()
                expected = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
                result["accept_valid"] = headers.get("sec-websocket-accept") == expected
                result["upgrade_valid"] = (headers.get("upgrade", "").lower() == "websocket" and
                                           "upgrade" in [x.strip().lower() for x in headers.get("connection", "").split(",")])
                result["ws101_pass"] = result["http_status"] == 101 and result["accept_valid"] and result["upgrade_valid"]
                result["cf_ray"] = headers.get("cf-ray")
    except Exception as exc:
        result["error_type"] = type(exc).__name__
    return result


def remote_selected():
    helper = ROOT / "review/fleet-qos-readonly-audit-20261117.py"
    ns = {"__file__": str(helper)}
    prefix, separator, _ = helper.read_text(encoding="utf-8-sig").partition("\ntry:\n")
    if not separator:
        raise RuntimeError("Read-only helper boundary changed; refusing to execute")
    exec(compile(prefix, "<audited-readonly-definitions>", "exec"), ns)
    connect, run, get_json = ns["connect"], ns["run"], ns["get_json"]
    gz = r2 = None
    result = {}
    try:
        gz = connect("134.175.151.213", 28722, "ubuntu", ns["DEV_PW"])
        result["GZ"] = {"services": run(gz, "date -Is\nsystemctl is-active gz-api nginx postgresql\nstat -c '%y %s' /opt/mgmt/server\nsha256sum /opt/mgmt/server\n")}
        result["GZ"]["database"] = run(gz, """sudo -u postgres psql routermgmt -X -t -A -F '|' <<'SQL'
BEGIN READ ONLY;
SELECT 'schema',COALESCE(max(version),0) FROM schema_migrations;
SELECT id,name,current_config_version,desired_config_version,highest_accepted_version,
round(extract(epoch FROM now()-last_heartbeat_at)) FROM devices ORDER BY id;
SELECT a.device_id,n.id,n.name,n.host,n.port,n.proto,n.purpose,a.is_primary,a.failover_priority
FROM node_assignments a JOIN nodes n ON n.id=a.node_id
WHERE a.device_id='rtr-a9bcba' ORDER BY a.is_primary DESC,a.failover_priority,n.id;
SELECT 'new_cf_node',id,name,host,port,proto FROM nodes WHERE name LIKE 'CF2D%';
SELECT 'active_task',id,device_id,type,status FROM tasks WHERE status IN ('pending','running');
COMMIT;
SQL
""")
        r2 = connect("127.0.0.1", 13223, "root", ns["DEV_PW"], gz)
        config_result = run(r2, "cat /etc/mihomo/config.yaml")
        config = yaml.safe_load(config_result["stdout"]) or {}
        result["R2"] = {"config_metadata": {
            "cf_nodes": [{k: p.get(k) for k in ("name", "server", "port", "servername", "udp")}
                         for p in config.get("proxies", []) if p.get("network") == "ws"],
            "tail_rules": config.get("rules", [])[-5:],
            "fallback_dns": config.get("dns", {}).get("fallback", []),
            "listeners_count": len(config.get("listeners", [])),
        }}
        result["R2"]["groups"] = {}
        for name in ("PROXY", "AUTO", "NODE-PICK", "US-MEDIA", "DOWNLOAD"):
            group = get_json(r2, "curl --noproxy '*' -fsS -m 5 http://127.0.0.1:9090/proxies/" + name)
            result["R2"]["groups"][name] = {k: group[k] for k in ("type", "now", "all", "unavailable", "rc") if k in group}
        result["R2"]["state"] = run(r2, "date '+%F %T %z'\ntc qdisc show\ncat /etc/routermgmt/state/proxy-meter-v3.json\n")
    except Exception as exc:
        result["error_type"] = type(exc).__name__
    finally:
        for client in (r2, gz):
            if client:
                client.close()
    return result


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    target = Path(args.output)
    if target.exists():
        raise SystemExit("Refusing to overwrite earlier evidence")
    params = json.loads((ROOT / "backups/.cf-node-params.json").read_text(encoding="utf-8-sig"))
    result = {"started_local": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
              "production_configuration_writes": False, "api_login": False,
              "cloudflare_account_authenticated": False,
              "limitations": ["Public DNS/RDAP cannot establish account ownership or origin A-record content behind orange cloud.",
                              "WS101 does not test VLESS authentication or full proxy traffic."]}
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        jobs = {}
        for domain in ("6849271.xyz", "8364927.xyz", "83649271.xyz"):
            jobs[pool.submit(rdap, domain)] = "rdap"
            jobs[pool.submit(dns, domain, "NS")] = "dns"
        for host in (*ACTIVE, "hk.8364927.xyz", "usbwg.8364927.xyz", "hk.83649271.xyz", "usbwg.83649271.xyz"):
            jobs[pool.submit(dns, host, "A")] = "dns"
        for host in ACTIVE:
            for edge in EDGES:
                jobs[pool.submit(ws, host, edge, params["ws_path"])] = "tls_ws"
        jobs[pool.submit(remote_selected)] = "remote"
        for future in concurrent.futures.as_completed(jobs):
            result.setdefault(jobs[future], []).append(future.result())
    result["finished_local"] = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
