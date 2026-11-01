#!/usr/bin/env python3
"""rssh-probe — 排障口存活探测 + 数据库更新 (2026-10-31)
每分钟由 cron 调起，检查每个 132xx 端口是否有 SSH banner，
直接更新 devices 表的 remote_ssh_alive 和 last_remote_ssh_check_at。
"""
import subprocess, psycopg2, sys, os

DB = "dbname=routermgmt user=mgmt_app password=3de2b212bb553333039f8a3901968a02 host=127.0.0.1"

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
    main()
