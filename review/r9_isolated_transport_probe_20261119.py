"""Small read-only VLESS/WS transfer probes through R9 SSH TCP channels.

No router files, listeners, selections or services are changed. Secrets stay in memory.
"""
from pathlib import Path
import argparse
import base64
import concurrent.futures
import datetime
import hashlib
import http.client
import importlib.util
import io
import json
import os
import ssl
import struct
import time
import uuid
import yaml

ROOT = Path(__file__).resolve().parents[1]


class BioTLS:
    def __init__(self, lower, host, deadline):
        self.lower, self.deadline = lower, deadline
        self.incoming, self.outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
        self.tls = ssl.create_default_context().wrap_bio(self.incoming, self.outgoing, server_side=False, server_hostname=host)
        for _ in range(100):
            try:
                self.tls.do_handshake()
                self.flush()
                break
            except ssl.SSLWantReadError:
                self.flush()
                self.pump()
        else:
            raise TimeoutError("TLS budget")

    def flush(self):
        while self.outgoing.pending:
            self.lower.sendall(self.outgoing.read())

    def pump(self):
        if time.monotonic() > self.deadline:
            raise TimeoutError("probe deadline")
        data = self.lower.recv(65536)
        if not data:
            raise EOFError("transport closed")
        self.incoming.write(data)

    def sendall(self, data):
        view = memoryview(data)
        while view:
            try:
                size = self.tls.write(view)
                view = view[size:]
                self.flush()
            except ssl.SSLWantReadError:
                self.flush()
                self.pump()

    def recv(self, size):
        while True:
            try:
                return self.tls.read(size)
            except ssl.SSLWantReadError:
                self.flush()
                self.pump()
            except ssl.SSLZeroReturnError:
                return b""

    def makefile(self, mode):
        return io.BufferedReader(ReadAdapter(self))


class ReadAdapter(io.RawIOBase):
    def __init__(self, transport):
        self.transport = transport

    def readable(self):
        return True

    def readinto(self, target):
        data = self.transport.recv(len(target))
        count = len(data)
        target[:count] = data
        return count


class WS:
    def __init__(self, lower, host, path):
        self.lower, self.buffer, self.data = lower, bytearray(), bytearray()
        key = base64.b64encode(os.urandom(16)).decode()
        expected = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
        request = "GET " + path + " HTTP/1.1\r\nHost: " + host + ":2053\r\nConnection: Upgrade\r\nUpgrade: websocket\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: " + key + "\r\n\r\n"
        lower.sendall(request.encode())
        while b"\r\n\r\n" not in self.buffer:
            if len(self.buffer) > 16384:
                raise ValueError("WS headers budget")
            self.buffer.extend(lower.recv(4096))
        head, tail = bytes(self.buffer).split(b"\r\n\r\n", 1)
        self.buffer = bytearray(tail)
        rows = head.decode().split("\r\n")
        headers = {k.lower().strip(): v.strip() for k, v in (r.split(":", 1) for r in rows[1:] if ":" in r)}
        if rows[0].split()[1] != "101" or headers.get("sec-websocket-accept") != expected:
            raise ValueError("WS handshake")

    def exact(self, count):
        while len(self.buffer) < count:
            data = self.lower.recv(65536)
            if not data:
                raise EOFError("WS closed")
            self.buffer.extend(data)
        result = bytes(self.buffer[:count])
        del self.buffer[:count]
        return result

    def frame(self, data, opcode=2):
        mask = os.urandom(4)
        length = len(data)
        suffix = bytes([0x80 | length]) if length < 126 else b"\xfe" + struct.pack("!H", length) if length < 65536 else b"\xff" + struct.pack("!Q", length)
        self.lower.sendall(bytes([0x80 | opcode]) + suffix + mask + bytes(v ^ mask[i % 4] for i, v in enumerate(data)))

    def sendall(self, data):
        self.frame(data)

    def recv(self, size):
        while not self.data:
            first, second = self.exact(2)
            opcode, masked, length = first & 15, second & 128, second & 127
            if length == 126:
                length = struct.unpack("!H", self.exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self.exact(8))[0]
            if length > 4 * 1024 * 1024:
                raise ValueError("WS frame budget")
            mask = self.exact(4) if masked else None
            payload = self.exact(length)
            if mask:
                payload = bytes(v ^ mask[i % 4] for i, v in enumerate(payload))
            if opcode in (0, 2):
                self.data.extend(payload)
            elif opcode == 9:
                self.frame(payload, 10)
            elif opcode == 8:
                return b""
            elif opcode != 10:
                raise ValueError("unexpected WS opcode")
        result = bytes(self.data[:size])
        del self.data[:size]
        return result


def exact(stream, count):
    buf = bytearray()
    while len(buf) < count:
        part = stream.recv(count - len(buf))
        if not part:
            raise EOFError("VLESS response missing")
        buf.extend(part)
    return bytes(buf)


def probe(client, node, size=262144):
    channel = None
    result = {"name": node["name"], "edge": node["server"], "domain": node["servername"], "requested_bytes": size}
    started = time.monotonic()
    try:
        channel = client.get_transport().open_channel("direct-tcpip", (node["server"], int(node["port"])), ("127.0.0.1", 0), timeout=8)
        channel.settimeout(8)
        deadline = started + 35
        outer = BioTLS(channel, node["servername"], deadline)
        ws = WS(outer, node["servername"], node["ws-opts"]["path"])
        host = b"speed.cloudflare.com"
        ws.sendall(b"\x00" + uuid.UUID(node["uuid"]).bytes + b"\x00\x01" + (443).to_bytes(2, "big") + b"\x02" + bytes([len(host)]) + host)
        # Send TLS ClientHello before waiting for VLESS response: the server can coalesce it.
        class Vless:
            def __init__(self):
                self.response_read = False
            def sendall(self, data):
                ws.sendall(data)
            def recv(self, count):
                if not self.response_read:
                    version, extras = exact(ws, 2)
                    if version != 0:
                        raise ValueError("VLESS version")
                    if extras:
                        exact(ws, extras)
                    self.response_read = True
                return ws.recv(count)
        inner = BioTLS(Vless(), host.decode(), deadline)
        result["handshake_s"] = round(time.monotonic() - started, 4)
        body_start = time.monotonic()
        inner.sendall(("GET /__down?bytes=" + str(size) + " HTTP/1.1\r\nHost: speed.cloudflare.com\r\nAccept-Encoding: identity\r\nConnection: close\r\n\r\n").encode())
        response = http.client.HTTPResponse(inner)
        response.begin()
        result["http_status"] = response.status
        body = response.read(size + 1)
        result["bytes"] = len(body)
        result["body_s"] = round(time.monotonic() - body_start, 4)
        result["total_s"] = round(time.monotonic() - started, 4)
        result["payload_Bps"] = round(len(body) / max(result["body_s"], 0.001))
        result["passed"] = response.status == 200 and len(body) == size
        response.close()
    except Exception as exc:
        result["error_type"] = type(exc).__name__
        result["total_s"] = round(time.monotonic() - started, 4)
        result["passed"] = False
    finally:
        if channel:
            channel.close()
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", required=True)
    args = ap.parse_args()
    dest = Path(args.output)
    if dest.exists():
        raise SystemExit("refusing to overwrite evidence")
    p = ROOT / "review/cf_postdeploy_audit_20261118.py"
    spec = importlib.util.spec_from_file_location("r9_transport_ro", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    ns = mod.helpers()
    gz = client = None
    out = {"started": datetime.datetime.now().astimezone().isoformat(timespec="seconds"), "production_writes": False,
           "source": "R9-origin TCP channels; independent TLS/WS/VLESS clients, formal selections unchanged"}
    try:
        gz = ns["connect"]("134.175.151.213", 28722, "ubuntu", ns["DEV_PW"])
        client = ns["connect"]("127.0.0.1", 13231, "root", ns["DEV_PW"], gz)
        out["before"] = ns["run"](client, "sha256sum /etc/mihomo/config.yaml\npidof mihomo\n")
        cfg = yaml.safe_load(ns["run"](client, "cat /etc/mihomo/config.yaml")["stdout"])
        nodes = [p for p in cfg["proxies"] if p["name"] in ("CF2D-HK-01", "CF2D-BWG-01")]
        out["results"] = [probe(client, p) for p in nodes]
        out["after"] = ns["run"](client, "sha256sum /etc/mihomo/config.yaml\npidof mihomo\n")
        out["formal_config_pid_unchanged"] = out["before"] == out["after"]
    finally:
        if client:
            client.close()
        if gz:
            gz.close()
        out["finished"] = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
        dest.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
