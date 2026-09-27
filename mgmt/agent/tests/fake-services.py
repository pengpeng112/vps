#!/usr/bin/env python3
"""fake-services.py — e2e 测试专用假服务（仅测试工具，路由器上不存在）。

在同一进程监听 9090，模拟：
  - mihomo external-controller：GET /version、GET/PUT /proxies/PROXY、PUT /configs、GET /connections
  - 假出口探测：GET /ip 返回 /var/routermgmt/exit_ip 内容（任何经 -x 代理端口的请求同样命中）
  - 国内直连探测：GET /cn
状态文件：/var/routermgmt/exit_ip、/var/routermgmt/group_now、/var/routermgmt/health_fail
"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

STATE_DIR = "/var/routermgmt"
EXIT_IP = f"{STATE_DIR}/exit_ip"
GROUP_NOW = f"{STATE_DIR}/group_now"
HEALTH_FAIL = f"{STATE_DIR}/health_fail"


def norm_path(raw: str) -> str:
    # 经 HTTP 代理的请求是绝对 URI（http://host/path），取其路径部分
    return urlsplit(raw).path


def read(path, default):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 静默
        pass

    def _send(self, code, body=b"", ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = norm_path(self.path)
        if path == "/version":
            self._send(200, b'{"version":"fake-1.0"}')
        elif path == "/proxies/PROXY":
            now = read(GROUP_NOW, "E2E-NODE1")
            self._send(200, json.dumps({"now": now}).encode())
        elif path == "/connections":
            now = read(GROUP_NOW, "E2E-NODE1")
            conns = [
                {"chains": ["DIRECT"], "download": 100, "upload": 20},
                {"chains": [now], "download": 5_000_000, "upload": 1_000_000},
            ]
            self._send(200, json.dumps({"connections": conns, "downloadTotal": 123, "uploadTotal": 45}).encode())
        elif path == "/ip":
            ip = read(EXIT_IP, "203.0.113.200")
            self._send(200, ip.encode(), "text/plain")
        elif path == "/cn":
            self._send(200, b"ok", "text/plain")
        else:
            self._send(404)

    def do_PUT(self):
        path = norm_path(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        if path == "/configs" or path.startswith("/configs"):
            self._send(204)
        elif path == "/proxies/PROXY":
            try:
                name = json.loads(body)["name"]
            except Exception:
                name = ""
            with open(GROUP_NOW, "w") as f:
                f.write(name)
            self._send(204)
        else:
            self._send(404)


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", 9090), Handler)
    srv.serve_forever()
