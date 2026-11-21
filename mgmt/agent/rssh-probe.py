#!/usr/bin/env python3
"""rssh-probe — 排障口存活探测 + 数据库更新 (2026-10-31)
每分钟由 cron 调起，检查每个 132xx 端口是否有 SSH banner，
直接更新 devices 表的 remote_ssh_alive 和 last_remote_ssh_check_at。
"""
import subprocess, sys, os

# 2026-11-19（governance-20261119/01-C6.1 安全堵洞）：移除硬编码连接串。
# DSN 只从环境变量 ROUTERMGMT_DB_DSN 读取（cron unit / 部署侧注入），缺失立即报错退出；
# 任何路径都不得把 DSN 原文打印到 stdout/stderr/日志。
DB = os.environ.get("ROUTERMGMT_DB_DSN", "").strip()
if not DB:
    sys.stderr.write("ROUTERMGMT_DB_DSN not set; refusing to run (no fallback to hardcoded credentials)\n")
    sys.exit(2)

def get_listening_ports():
    """获取当前监听的 132xx 端口"""
    result = subprocess.run(
        ["ss", "-tln"], capture_output=True, text=True, timeout=5
    )
    ports = set()
    for line in result.stdout.splitlines():
        if ":132" in line and "LISTEN" in line:
            parts = line.split()
            for part in parts:
                if part.startswith("127.0.0.1:13"):
                    port = int(part.split(":")[-1])
                    ports.add(port)
    return ports

def check_ssh_banner(port):
    """检查端口是否有 SSH banner"""
    import socket
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3)
        sock.connect(("127.0.0.1", port))
        banner = sock.recv(4).decode("ascii", errors="ignore")
        sock.close()
        return banner.startswith("SSH-")
    except:
        return False

def main():
    import psycopg2
    conn = psycopg2.connect(DB)
    cur = conn.cursor()

    # 获取所有有排障口的设备
    cur.execute(
        "SELECT id, name, remote_ssh_port FROM devices "
        "WHERE remote_ssh_port IS NOT NULL AND remote_ssh_port != ''"
    )
    devices = cur.fetchall()

    listening = get_listening_ports()

    for dev_id, name, port_str in devices:
        try:
            port = int(port_str)
        except (ValueError, TypeError):
            continue

        if port in listening:
            alive = check_ssh_banner(port)
        else:
            alive = False

        cur.execute(
            "UPDATE devices SET remote_ssh_alive = %s, "
            "last_remote_ssh_check_at = now() "
            "WHERE id = %s",
            (alive, dev_id)
        )

        status = "✅" if alive else "❌"
        print(f"  {status} {name}: port={port} alive={alive}")

    conn.commit()
    cur.close()
    conn.close()

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Parser/driver errors may echo parts of a DSN. Never log their text.
        sys.stderr.write("rssh-probe failed: " + type(exc).__name__ + " (details withheld)\n")
        sys.exit(1)
