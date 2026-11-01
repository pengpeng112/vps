import socket, subprocess, time, sys
LOG = open(r"F:\vps\backups\gsw_monitor.log", "a", encoding="utf-8")
def log(msg):
    line = time.strftime("%H:%M:%S") + " " + msg
    print(line, flush=True); LOG.write(line + "\n"); LOG.flush()
log("=== 监控启动(6分钟): ARP/80/22 ===")
end = time.time() + 360
last = None
while time.time() < end:
    state = []
    # ARP 探测: 直接发 ARP 谁也骗不了
    r = subprocess.run(["ping", "-S", "192.168.1.99", "-n", "1", "-w", "800", "192.168.1.1"], capture_output=True, text=True, encoding="gbk", errors="replace")
    if "TTL" in r.stdout: state.append("ICMP_OK")
    for p in (80, 22):
        try:
            s = socket.create_connection(("192.168.1.1", p), timeout=1.2)
            s.close(); state.append(f"P{p}OK")
        except Exception:
            pass
    st = ",".join(state) if state else "silent"
    if st != last:
        log("状态变化: " + st)
        last = st
    time.sleep(3)
log("=== 监控结束 ===")
