#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tx-split-check.py — 腾讯分流资格体检（视频/下载→腾讯、其余→东京 的前置判定）

用途：新设备上线后、或既有设备欲启用"腾讯分担视频流量"前运行。三项体检 + 定稿门槛判定，
全程零破坏（矩阵测试不切换绑定；吞吐测试经管理台 switch_node 试切并自动回切，双验出口 IP）。

体检项与门槛（2026-10-01 用户定稿）：
  1. 网站通过率：腾讯节点 ≥14/15（youtube/netflix 必须通——视频要走的路）
  2. 延迟差：腾讯中位 − 东京中位 ≤150ms
  3. 丢包/吞吐（二选一满足）：
     a) 腾讯方向 ICMP 丢包 ≤5%（TCP 类出口硬指标）
     b) 豁免：走 hy2 且 ICMP >5% 时，长流吞吐（三并发 30MB）≥ 东京同测的 60%
  4. 长流吞吐（视频分流关键）：三并发合计给出报告值，供人工参考（无独立否决线，并入 3b 判定）

用法：
  python tx-split-check.py --device rtr-38aa54 --rssh-port 13227
  python tx-split-check.py --device rtr-a9bcba --rssh-port 13223   # 自动判不合格即止于矩阵（不试切）

输出：逐项数据 + 结论 ENABLE / DENY（含不达标项）。DENY 则该点位维持东京独扛。
"""
import argparse, base64, json, os, subprocess, sys, time, uuid
import requests, urllib3
urllib3.disable_warnings()

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, 'scripts'))
from mgmt_api_test import _ledger_pw, totp, BASE

DO_KEY = os.path.expanduser('~/.ssh/id_rsa_dmit')
UNIFIED_PW = '[REDACTED]'
MGMT_SSH = ['ssh', '-p', '28722', '-i', DO_KEY, '-o', 'IdentitiesOnly=yes', 'root@179.255.137.64']

# 设备 → 腾讯/东京节点绑定 id（node_assignments；BWG=4 全队主用，TX-HY2=3）
NODE_IDS = {'tx': 3, 'bwg': 4}

URLS = [  # 15 站，类型覆盖见 nodetest 报告；youtube/netflix 为视频必测
    "www.google.com", "www.youtube.com", "twitter.com", "chat.openai.com",
    "github.com", "www.wikipedia.org", "www.bbc.com", "www.netflix.com",
    "www.amazon.com", "www.paypal.com", "web.telegram.org", "www.microsoft.com",
    "www.cloudflare.com", "www.baidu.com", "www.apple.com",
]
enc = lambda h: "https%3A%2F%2F" + h

TP_SCRIPT = r'''#!/bin/sh
P=127.0.0.1:7890
echo "exit=$(curl -s -m 8 -x http://$P https://api.ipify.org)"
rm -f /tmp/s1 /tmp/s2 /tmp/s3
( curl -s -m 90 -x http://$P -o /dev/null -w '%{speed_download}' 'https://speed.cloudflare.com/__down?bytes=30000000' > /tmp/s1 ) &
( curl -s -m 90 -x http://$P -o /dev/null -w '%{speed_download}' 'https://speed.cloudflare.com/__down?bytes=30000000' > /tmp/s2 ) &
( curl -s -m 90 -x http://$P -o /dev/null -w '%{speed_download}' 'https://speed.cloudflare.com/__down?bytes=30000000' > /tmp/s3 ) &
wait
i=1; for f in /tmp/s1 /tmp/s2 /tmp/s3; do
  v=$(cat $f 2>/dev/null || echo 0); awk -v v=$v -v i=$i 'BEGIN{printf "%.2f\n", v/1048576}'; i=$((i+1))
done
rm -f /tmp/s1 /tmp/s2 /tmp/s3
'''

def on_device(rssh_port, script, timeout=200):
    b64 = base64.b64encode(script.encode()).decode()
    inner = (f"sshpass -p '{UNIFIED_PW}' ssh -p {rssh_port} -o StrictHostKeyChecking=no "
             f"root@127.0.0.1 'echo {b64} | openssl base64 -d -A > /tmp/tsc.sh; sh /tmp/tsc.sh; rm -f /tmp/tsc.sh' 2>/dev/null")
    r = subprocess.run(MGMT_SSH + [inner], capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip()

def matrix_and_icmp(rssh_port):
    sh = "#!/bin/sh\nLOG=/tmp/tsc-matrix.log\n: > $LOG\nAPI=http://127.0.0.1:9090\n"
    sh += "for pair in 'TX 43.134.46.44' 'BWG 74.82.203.175'; do set -- $pair; "
    sh += "ping -c 10 -q -w 15 $2 2>/dev/null | tail -2 | tr '\\n' ' ' | sed \"s/^/ICMP_$1 /\" >> $LOG; echo >> $LOG; done\n"
    for node in ["TX-HY2-SG-01", "BWG-VLESS-JPTY-01"]:
        for u in URLS:
            sh += (f"r=$(curl -s -m 10 \"$API/proxies/{node}/delay?url={enc(u)}&timeout=9000\" 2>/dev/null"
                   f" | grep -o '\"delay\":[0-9]*' | cut -d: -f2); "
                   f"echo \"SITE {node} {u} ${{r:-FAIL}}\" >> $LOG\n")
    sh += "cat $LOG\n"
    return on_device(rssh_port, sh, timeout=400)

def parse(out):
    icmp, sites = {}, {}
    for line in out.splitlines():
        p = line.split()
        if line.startswith("ICMP_TX"):
            m = [x for x in p if "packet" not in x and "loss" not in x and "=" not in x and "round-trip" not in x]
            icmp['tx'] = next((x for x in p if x.endswith("%")), "?")
        elif line.startswith("ICMP_BWG"):
            icmp['bwg'] = next((x for x in p if x.endswith("%")), "?")
        elif line.startswith("SITE "):
            v = p[3] if len(p) > 3 else "FAIL"
            sites[(p[1], p[2])] = None if v == "FAIL" else int(v)
    return icmp, sites

def login():
    ADMIN_PW, _ = _ledger_pw()
    s = requests.Session(); s.verify = False
    for _ in range(4):
        r = s.post(BASE + "/api/v1/auth/login", json={"username": "admin", "password": ADMIN_PW}, timeout=12)
        r2 = s.post(BASE + "/api/v1/auth/totp", json={"ticket": r.json().get("ticket"), "code": totp()}, timeout=12)
        if r2.status_code == 200:
            return s, {"Authorization": "Bearer " + r2.json()["token"]}
        time.sleep(14)
    raise SystemExit("API 登录失败")

def switch(sess, H, dev, node_id):
    r = sess.post(BASE + f"/api/v1/devices/{dev}/ops", headers={**H, "Idempotency-Key": str(uuid.uuid4())},
                  json={"type": "switch_node", "params": {"node_id": node_id}}, timeout=15)
    for _ in range(20):
        time.sleep(8)
        tk = sess.get(BASE + f"/api/v1/tasks?limit=6&device_id={dev}", headers=H, timeout=10).json()
        items = tk.get("tasks") or tk.get("items") or []
        st = next((t for t in items if t.get("type") == "switch_node"), None)
        if st and st.get("status") in ("ok", "failed", "expired"):
            return st.get("status") == "ok"
    return False

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--device', required=True)
    ap.add_argument('--rssh-port', type=int, required=True)
    ap.add_argument('--skip-throughput', action='store_true', help='只跑矩阵+ICMP（无切换）')
    a = ap.parse_args()

    print("== 1/3 矩阵 + ICMP（零影响）==")
    out = matrix_and_icmp(a.rssh_port)
    icmp, sites = parse(out)
    tx = [v for (n, u), v in sites.items() if n == "TX-HY2-SG-01"]
    bwg = [v for (n, u), v in sites.items() if n == "BWG-VLESS-JPTY-01"]
    tx_ok = sum(1 for v in tx if v is not None)
    video_ok = all(sites.get(("TX-HY2-SG-01", u)) is not None for u in ("www.youtube.com", "www.netflix.com"))
    med = lambda xs: sorted(xs)[len(xs)//2] if xs else None
    tx_med, bwg_med = med([v for v in tx if v]), med([v for v in bwg if v])
    tx_loss = float(icmp.get('tx', '?').rstrip('%')) if icmp.get('tx', '?') != '?' else None
    print(f"腾讯通过 {tx_ok}/15（视频站必通: {'是' if video_ok else '否'}）；中位 腾讯{tx_med}ms vs 东京{bwg_med}ms")
    print(f"腾讯 ICMP 丢包: {icmp.get('tx', '?')}；东京: {icmp.get('bwg', '?')}")

    c1 = tx_ok >= 14 and video_ok
    c2 = tx_med and bwg_med and (tx_med - bwg_med) <= 150
    print(f"门槛1 通过率≥14且视频站通: {'✓' if c1 else '✗'}；门槛2 延迟差≤150ms: {'✓' if c2 else '✗'}")
    if not (c1 and c2):
        print("== 结论: DENY（矩阵不达标，不做吞吐试切）=="); return

    if a.skip_throughput:
        print("(--skip-throughput: 未测吞吐，不输出最终结论)"); return

    print("== 2/3 吞吐试切（经管理台 switch_node，自动回切）==")
    sess, H = login()
    tx_tp = bwg_tp = None
    try:
        if not switch(sess, H, a.device, NODE_IDS['tx']):
            print("切腾讯失败"); return
        time.sleep(6)
        out = on_device(a.rssh_port, TP_SCRIPT, timeout=200)
        print("腾讯:\n" + out)
        nums = [float(l) for l in out.splitlines() if l and l[0].isdigit()]
        if nums and "43.134.46.44" in out: tx_tp = sum(nums)
    finally:
        ok = switch(sess, H, a.device, NODE_IDS['bwg'])
        print("回切东京:", "ok" if ok else "失败（人工核查！）")
        time.sleep(6)
        chk = on_device(a.rssh_port, "curl -s -m 8 -x http://127.0.0.1:7890 https://api.ipify.org", timeout=60)
        print("回切后出口:", chk, "(应为 74.82.203.175)")
    if not ok or "74.82.203.175" not in chk:
        print("!! 回切未确认，人工处理"); return
    # 东京基准吞吐（当前绑定即东京，直接测）
    out = on_device(a.rssh_port, TP_SCRIPT, timeout=200)
    print("东京基准:\n" + out)
    nums = [float(l) for l in out.splitlines() if l and l[0].isdigit()]
    if nums and "74.82.203.175" in out: bwg_tp = sum(nums)

    print("== 3/3 判定 ==")
    if tx_tp and bwg_tp:
        ratio = tx_tp / bwg_tp * 100
        print(f"三并发合计: 腾讯 {tx_tp:.2f} MB/s vs 东京 {bwg_tp:.2f} MB/s（{ratio:.0f}%）")
        c3 = (tx_loss is not None and tx_loss <= 5) or ratio >= 60
        print(f"门槛3: ICMP≤5% 或 hy2 长流≥东京60% → {'✓' if c3 else '✗'}（ICMP={tx_loss}%，比值={ratio:.0f}%）")
        print("== 结论:", "ENABLE（可启用腾讯分流：视频/下载→腾讯，其余→东京）" if (c1 and c2 and c3) else "DENY（暂不启用，维持东京独扛）", "==")
    else:
        print("吞吐数据不完整（出口 IP 未验证或解析失败），人工复核")

if __name__ == '__main__':
    main()
