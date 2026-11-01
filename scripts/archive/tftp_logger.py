import socket, time
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.bind(("0.0.0.0", 69))
s.settimeout(2)
print("TFTP listen 60s...", flush=True)
end = time.time() + 180
while time.time() < end:
    try:
        data, addr = s.recvfrom(2048)
    except socket.timeout:
        continue
    if data[:2] == b"\x00\x01":
        fname = data[2:].split(b"\x00")[0].decode(errors="replace")
        print(time.strftime('%H:%M:%S'), addr[0], "RRQ:", fname, flush=True)
        s.sendto(b"\x00\x05\x00\x01log-only\x00", addr)
    else:
        print(time.strftime('%H:%M:%S'), addr[0], "op", data[:2].hex(), flush=True)
print("done", flush=True)
