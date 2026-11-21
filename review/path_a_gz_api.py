"""GZ mgmt API client for Path-A execution: login (TOTP from controlled ledger),
then requests tunneled through SSH direct-tcpip to gz-api 127.0.0.1:8080.

Sensitive: token stays in memory; never print credentials/WS paths/full node DTOs.
"""
from pathlib import Path
import base64
import hashlib
import hmac
import json
import struct
import time

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "登录信息与配置总表-20260917.md"
HELPER = ROOT / "review/fleet-qos-readonly-audit-20261117.py"


def _ledger_pw():
    src = LEDGER.read_text(encoding="utf-8")
    return (src.split("| 用户名 | `")[1].split("`")[0],
            src.split("| 密码 | `")[1].split("`")[0],
            src.split("TOTP 密钥（重绑验证器用） | `")[1].split("`")[0])


def totp(secret: str) -> str:
    key = base64.b32decode(secret + "=" * ((8 - len(secret) % 8) % 8), casefold=True)
    counter = struct.pack(">Q", int(time.time()) // 30)
    d = hmac.new(key, counter, hashlib.sha1).digest()
    off = d[-1] & 0x0F
    return f"{(struct.unpack('>I', d[off:off+4])[0] & 0x7FFFFFFF) % 1000000:06d}"


class GzApi:
    """HTTP-over-SSH-channel client. One SSH connection, one API session."""

    def __init__(self):
        ns = {"__file__": str(HELPER)}
        prefix, sep, _ = HELPER.read_text(encoding="utf-8-sig").partition("\ntry:\n")
        exec(compile(prefix, "<helper>", "exec"), ns)
        self._ns = ns
        self.gz = ns["connect"]("134.175.151.213", 28722, "ubuntu", ns["DEV_PW"])
        self.token = None

    def close(self):
        if self.gz:
            self.gz.close()
            self.gz = None

    def _http(self, method: str, path: str, body: dict | None = None, headers: dict | None = None,
              timeout: float = 20) -> tuple[int, dict]:
        import shlex
        payload = json.dumps(body) if body is not None else None
        cmd = ["curl", "-sS", "-m", str(int(timeout)), "-X", method,
               "-H", "Content-Type: application/json"]
        if self.token:
            cmd += ["-H", "Authorization: Bearer " + self.token]
        for k, v in (headers or {}).items():
            cmd += ["-H", f"{k}: {v}"]
        if payload is not None:
            cmd += ["--data-raw", payload]
        cmd += ["-w", "\n%{http_code}", "http://127.0.0.1:8080" + path]
        script = " ".join(shlex.quote(c) for c in cmd) + "\n"
        r = self._ns["run"](self.gz, script, timeout=timeout + 15)
        out = r["stdout"]
        bodytxt, _, codetxt = out.rpartition("\n")
        try:
            parsed = json.loads(bodytxt or "{}")
        except ValueError:
            parsed = {"raw": (bodytxt or "")[:500]}
        return int(codetxt) if codetxt.strip().isdigit() else 0, parsed

    def login(self):
        import shlex
        import time as _t
        user, pw, secret = _ledger_pw()
        last = None
        for attempt in range(2):
            body = shlex.quote(json.dumps({"username": user, "password": pw}))
            r = self._ns["run"](self.gz, f"""curl -sS -m 10 -X POST -H 'Content-Type: application/json' -d {body} http://127.0.0.1:8080/api/v1/auth/login
""", timeout=25)
            data = json.loads(r["stdout"] or "{}")
            ticket = data.get("ticket")
            if not ticket:
                raise RuntimeError(f"login no ticket: {list(data)}")
            code = totp(secret)
            body2 = shlex.quote(json.dumps({"ticket": ticket, "code": code}))
            r2 = self._ns["run"](self.gz, f"""curl -sS -m 10 -X POST -H 'Content-Type: application/json' -d {body2} http://127.0.0.1:8080/api/v1/auth/totp
""", timeout=25)
            data2 = json.loads(r2["stdout"] or "{}")
            tok = data2.get("token")
            if tok:
                self.token = tok
                return True
            last = data2
            _t.sleep(32)  # 换新 30s TOTP 窗口后重试（限流 10 次/5 分钟，保守）
        raise RuntimeError(f"totp no token after retries: {list(last or {})}")

    def get(self, path, **kw):
        return self._http("GET", path, None, **kw)

    def post(self, path, body=None, **kw):
        return self._http("POST", path, body if body is not None else {}, **kw)
