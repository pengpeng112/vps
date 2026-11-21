"""CF03: strict TLS + WS101 retest for hk.6849271.xyz / usbwg.6849271.xyz x 2 edges.
Reuses the audited ws() implementation from governance_cf_readonly_audit_20261118.
"""
from pathlib import Path
import base64
import hashlib
import json
import os
import socket
import ssl
import datetime

ROOT = Path(__file__).resolve().parents[1]
ACTIVE = ("hk.6849271.xyz", "usbwg.6849271.xyz")
EDGES = ("104.18.32.7", "162.159.135.42")


def san_matches(host: str, sans: list[str]) -> bool:
    """Exact or leftmost-label wildcard match (Python TLS handshake already strict-verified)."""
    for san in sans:
        if san == host:
            return True
        if san.startswith("*.") and host.endswith(san[1:]) and "." not in host[: len(host) - len(san[1:])]:
            return True
    return False


def ws(host, edge, path):
    result = {"sni": host, "edge": edge, "port": 2053, "strict": True}
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    with socket.create_connection((edge, 2053), timeout=8) as raw:
        with ssl.create_default_context().wrap_socket(raw, server_hostname=host) as conn:
            result["tls"] = conn.version()
            san = [v for k, v in conn.getpeercert().get("subjectAltName", []) if k == "DNS"]
            result["san"] = san
            result["san_ok"] = san_matches(host, san)
            req = ("GET " + path + " HTTP/1.1\r\nHost: " + host +
                   "\r\nConnection: Upgrade\r\nUpgrade: websocket\r\nSec-WebSocket-Version: 13\r\n"
                   "Sec-WebSocket-Key: " + key + "\r\n\r\n")
            conn.sendall(req.encode())
            data = b""
            while b"\r\n\r\n" not in data and len(data) < 16384:
                part = conn.recv(2048)
                if not part:
                    break
                data += part
    lines = data.split(b"\r\n\r\n", 1)[0].decode("latin1").split("\r\n")
    pieces = lines[0].split(" ", 2)
    result["http"] = int(pieces[1]) if len(pieces) > 1 and pieces[1].isdigit() else None
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.lower()] = v.strip()
    expected = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
    result["accept_ok"] = headers.get("sec-websocket-accept") == expected
    result["upgrade_ok"] = (headers.get("upgrade", "").lower() == "websocket" and
                            "upgrade" in [x.strip().lower() for x in headers.get("connection", "").split(",")])
    result["pass"] = result["http"] == 101 and result["accept_ok"] and result["upgrade_ok"] and result["san_ok"]
    return result


def main():
    params = json.loads((ROOT / "backups/.cf-node-params.json").read_text(encoding="utf-8-sig"))
    out = {"tested_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"), "cases": []}
    all_pass = True
    for host in ACTIVE:
        for edge in EDGES:
            r = None
            for attempt in range(3):  # 边缘偶发超时重试
                try:
                    r = ws(host, edge, params["ws_path"])
                    if r.get("pass"):
                        break
                except Exception as exc:
                    r = {"sni": host, "edge": edge, "error_type": type(exc).__name__, "pass": False}
                import time
                time.sleep(2)
            out["cases"].append(r)
            all_pass &= bool(r.get("pass"))
            print(f"  {'PASS' if r.get('pass') else 'FAIL'} {host} @ {edge}: http={r.get('http')} "
                  f"san_ok={r.get('san_ok')} accept={r.get('accept_ok')} upgrade={r.get('upgrade_ok')} "
                  f"err={r.get('error_type','')}")
    out["all_pass"] = all_pass
    target = ROOT / "review/CF03-tls-ws-重测-20261118-路径A.json"
    target.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print("CF03:", "4/4 PASS" if all_pass else "FAIL", "->", target)


if __name__ == "__main__":
    main()
