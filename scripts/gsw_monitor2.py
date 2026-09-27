import socket, subprocess, time
SRC = "192.168.1.99"
LOG = open(r"F:\vps\backups\gsw_monitor2.log", "a", encoding="utf-8")
def log(m):
    line = time.strftime("%H:%M:%S") + " " + m
    print(line, flush=True); LOG.write(line + "\n"); LOG.flush()
def tcp(ip, p):
    try:
        s = socket.socket(); s.settimeout(1.2)
        s.bind((SRC, 0)); s.connect((ip, p)); s.close(); return True
    except Exception:
        return False
def ping(ip):
    r = subprocess.run(["ping", "-S", SRC, "-n", "1", "-w", "900", ip],
                       capture_output=True, encoding="gbk", errors="replace")
    return "TTL" in (r.stdout or "")
log("=== 绑定源监视启动(8分钟) ===")
last = None
end = time.time() + 480
tick = 0
while time.time() < end:
    st = []
    if ping("192.168.1.1"): st.append("ICMP")
    if tcp("192.168.1.1", 80): st.append("P80")
    if tcp("192.168.1.1", 22): st.append("P22")
    tick += 1
    if tick % 4 == 0:  # 每12秒扫 2-20 抓它换地址
        for i in range(2, 21):
            ip = f"192.168.1.{i}"
            if ping(ip):
                st.append(f"FOUND_{ip}")
        for i in (2, 3, 4, 5):
            if tcp(f"192.168.1.{i}", 80): st.append(f"P80_at_{i}")
    s = ",".join(st) if st else "silent"
    if s != last:
        log("状态: " + s); last = s
    time.sleep(3)
log("=== 结束 ===")
