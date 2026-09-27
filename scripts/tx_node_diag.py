# -*- coding: utf-8 -*-
# 诊断 雅典娜(公司网出口) -> 腾讯节点43.134.46.44 的连通性，带对照组
import paramiko, sys

c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.2.1", username="root", password="[REDACTED]", timeout=10)

cmds = [
    # 对照组：DO 与国内是否正常
    ("ctrl DO:28722 TCP", "timeout 4 bash -c 'echo > /dev/tcp/159.223.36.119/28722' 2>/dev/null && echo OPEN || echo FAIL"),
    ("ctrl 223.5.5.5 ICMP", "ping -c 4 -W 2 -q 223.5.5.5 2>&1 | tail -2"),
    ("ctrl DO ICMP", "ping -c 4 -W 2 -q 159.223.36.119 2>&1 | tail -2"),
    # 目标：腾讯节点
    ("TX:443 TCP", "timeout 5 curl -o /dev/null -s -m 4 -w 'connect=%{time_connect} code=%{http_code}\\n' -k https://43.134.46.44/ || echo CURL_FAIL"),
    ("TX:443 TCP raw", "timeout 4 bash -c 'echo > /dev/tcp/43.134.46.44/443' 2>/dev/null && echo OPEN || echo FAIL"),
    ("TX:22 TCP raw", "timeout 4 bash -c 'echo > /dev/tcp/43.134.46.44/22' 2>/dev/null && echo OPEN || echo FAIL"),
    ("TX ICMP", "ping -c 4 -W 2 -q 43.134.46.44 2>&1 | tail -2"),
    # 本机出栈是否被自家 mihomo 劫持（OUTPUT 链）
    ("nft OUTPUT hijack?", "nft list ruleset 2>/dev/null | grep -E 'chain output|redirect to|7892|7893' | head -8"),
    ("mihomo running", "pidof mihomo || echo NOT_RUNNING"),
    # 路径定位
    ("traceroute TX", "traceroute -n -m 14 -w 1 -q 1 43.134.46.44 2>&1 | head -16"),
    ("traceroute DO", "traceroute -n -m 14 -w 1 -q 1 159.223.36.119 2>&1 | head -16"),
]
for label, cmd in cmds:
    _, o, e = c.exec_command(cmd, timeout=40)
    out = (o.read() + e.read()).decode(errors="replace").strip()
    print(f"=== {label} ===\n{out}\n")
c.close()
