"""Raw-socket ttyd client for the router terminal (auto-login + run commands)."""
import socket, base64, os, sys, time, re, json

HOST, PORT = "192.168.2.1", 7681

def ws_connect():
    s = socket.create_connection((HOST, PORT), timeout=8)
    key = base64.b64encode(os.urandom(16)).decode()
    s.sendall((f"GET /ws HTTP/1.1\r\nHost: {HOST}:{PORT}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
    hdr = b""
    while b"\r\n\r\n" not in hdr:
        hdr += s.recv(4096)
    assert b"101" in hdr.split(b"\r\n")[0], hdr[:100]
    return s

def send_frame(sock, payload: bytes, opcode=2):
    mask = os.urandom(4)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    head = bytes([0x80 | opcode])
    n = len(payload)
    if n < 126: head += bytes([0x80 | n])
    elif n < 65536: head += bytes([0x80 | 126]) + n.to_bytes(2, "big")
    else: head += bytes([0x80 | 127]) + n.to_bytes(8, "big")
    sock.sendall(head + mask + masked)

def send_json(sock, obj):
    send_frame(sock, json.dumps(obj).encode(), opcode=1)

def send_input(sock, text):
    send_frame(sock, b"\x01" + text.encode(), opcode=2)

def recv_frame(sock, timeout=1.0):
    sock.settimeout(timeout)
    try:
        h = sock.recv(2)
        if len(h) < 2: return None
        fin_op = h[0]; n = h[1] & 0x7F
        if n == 126: n = int.from_bytes(sock.recv(2), "big")
        elif n == 127: n = int.from_bytes(sock.recv(8), "big")
        data = b""
        while len(data) < n:
            chunk = sock.recv(n - len(data))
            if not chunk: break
            data += chunk
        return (fin_op & 0x0F, data)
    except socket.timeout:
        return None

ansi = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b[()][A-Z0-9]|\r")
def clean(s): return ansi.sub("", s)

sock = ws_connect()
send_json(sock, {"AuthToken": "", "columns": 200, "rows": 30})
out = ""
def pump(sec):
    global out
    end = time.time() + sec
    while time.time() < end:
        f = recv_frame(sock, timeout=0.5)
        if f and f[0] == 2 and f[1][:1] == b"\x00":
            out += f[1][1:].decode(errors="replace")

pump(2)
seen = clean(out)
if "login" in seen:
    send_input(sock, "root\n"); pump(1.5)
    if "assword" in clean(out)[-200:]:
        send_input(sock, "password\n"); pump(2)

cmds = sys.argv[1:] or ["echo READY"]
for c in cmds:
    send_input(sock, "\x03")
    pump(0.4)
    out = ""
    send_input(sock, c + "\n")
    pump(3)
    print(f"$ {c}")
    print(clean(out))
    print("-" * 40)
sock.close()
