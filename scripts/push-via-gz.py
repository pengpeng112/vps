#!/usr/bin/env python3
"""通过广州控制服务器推送命令到设备（利用排障口 132xx）"""
import paramiko, sys, time, json
from pathlib import Path

_credentials = json.loads((Path(__file__).resolve().parents[1] / "backups/.gz-ops-credentials.json").read_text(encoding="utf-8"))
GZ_HOST = _credentials["GZ_HOST"]
GZ_USER = _credentials["GZ_USER"]
GZ_PASS = _credentials["GZ_PASS"]
DEV_PASS = _credentials["DEV_PASS"]

def push_to_device(gz, port, b64_payload):
    """通过广州的排障口推送命令到设备"""
    cmd = f'''echo {b64_payload} | base64 -d > /tmp/payload.sh
sshpass -p {DEV_PASS} scp -O -P {port} -o StrictHostKeyChecking=no /tmp/payload.sh root@127.0.0.1:/tmp/payload.sh
sshpass -p {DEV_PASS} ssh -o StrictHostKeyChecking=no -p {port} root@127.0.0.1 "chmod 755 /tmp/payload.sh && sh /tmp/payload.sh && rm /tmp/payload.sh"'''
    _, o, e = gz.exec_command(cmd, timeout=60)
    return o.read().decode().strip(), e.read().decode().strip()

def main():
    b64 = open(r"F:\vps\backups\.gz-switch-twd-b64.txt").read().strip()
    
    # 设备列表（广州上可用的排障口）
    devices = {13222: "R1临朐"}
    
    # 连接广州
    gz = paramiko.SSHClient()
    gz.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    gz.connect(GZ_HOST, port=28722, username=GZ_USER, password=GZ_PASS, timeout=15)
    
    # 检查广州上当前有哪些排障口
    _, o, _ = gz.exec_command("sudo ss -tln | grep -oE '127.0.0.1:132[0-9]{2}' | cut -d: -f2")
    available_ports = [int(p) for p in o.read().decode().split()]
    print(f"广州可用排障口: {available_ports}")
    
    for port, name in devices.items():
        if port in available_ports:
            print(f"\n推送到 {name} (port {port})...")
            out, err = push_to_device(gz, port, b64)
            print(f"  结果: {out}")
            if err and 'WARNING' not in err:
                print(f"  错误: {err[:100]}")
        else:
            print(f"\n{name} (port {port}) 排障口不在广州上，跳过")
    
    gz.close()

if __name__ == "__main__":
    main()
