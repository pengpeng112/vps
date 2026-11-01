#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""point-check.py — 新点位/新上线路由器体检（含运营商判断与绑定建议）

用法（设备上线后）：
  python point-check.py --num 4            # R4（口令自动取 backups/.snr4-pw，RSSH 默认 13224）
  python point-check.py --num 7 --rssh 13227

输出：三节点 ICMP 丢包/延迟、TLS 往返、当前代理吞吐（12s 限时）、路由特征（59.43=CN2/其它）、
      运营商线索、以及按决策表的绑定建议。全程只读，不改任何配置。
"""
import argparse, os, paramiko

MGMT = ('179.255.137.64', 28722)  # 2026-10-02 管理面 DMIT
MGMT_KEY = os.path.expanduser('~/.ssh/id_rsa_dmit')
RSSH = {2: 13223, 3: 13227, 4: 13224, 5: 13225, 6: 13226}  # R3 已迁13227(10-01),R4=13224

REMOTE = r'''
echo "== 身份与网络 =="
grep DEVICE_ID /etc/routermgmt/agent.conf
ifstatus wan 2>/dev/null | grep -E '"address"' | head -1
echo "== 三节点 ICMP（各20包）=="
for pair in "bwg 74.82.203.175" "tx 43.134.46.44" "do 159.223.36.119"; do
  set -- $pair
  echo -n "  $1($2): "
  ping -c 20 -q -w 25 "$2" 2>/dev/null | tail -2 | tr '\n' ' '; echo
done
echo "== TLS 往返（wget 非静默，秒）=="
for pair in "bwg 74.82.203.175" "tx 43.134.46.44"; do
  set -- $pair
  s=$(date +%s)
  wget --no-check-certificate -T 8 -q -O /dev/null "https://$2/" 2>/dev/null
  rc=$?; e=$(date +%s)
  echo "  $1: rc=$rc 耗时$((e-s))s (rc8=TLS通获HTTP错误响应=正常)"
done
echo "== 当前代理吞吐（12s 限时 4MB）=="
curl -s --max-time 12 -x http://127.0.0.1:7890 -o /dev/null -w "  速度=%{speed_download}B/s 下行=%{size_download}B\n" "https://speed.cloudflare.com/__down?bytes=4000000"
echo "== 路由特征（到东京）=="
traceroute -n -q 1 -w 2 -m 14 74.82.203.175 2>&1 | sed -n '3,8p'
echo "== 运营商线索（ traceroute 前几跳 + 公网出口 ）=="
traceroute -n -q 1 -w 2 -m 6 223.5.5.5 2>&1 | sed -n '3,6p'
echo "== 当前 NODE-PICK =="
wget -qO- --timeout=5 http://127.0.0.1:9090/proxies/NODE-PICK 2>/dev/null | grep -oE '"now":"[^"]+"'
'''

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--num', type=int, required=True)
    ap.add_argument('--rssh', type=int, default=None)
    ap.add_argument('--pw-file', default=None)
    a = ap.parse_args()
    port = a.rssh or RSSH[a.num]
    pwfile = a.pw_file or rf'F:\vps\backups\.snr{a.num}-pw'
    pw = open(pwfile, encoding='utf-8').read().splitlines()[0].strip()
    mg = paramiko.SSHClient(); mg.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    mg.connect(MGMT[0], port=MGMT[1], username='root', key_filename=MGMT_KEY,
               look_for_keys=False, allow_agent=False, timeout=20, auth_timeout=20)
    script = f'''
umask 077; PWFILE=$(mktemp); printf '%s\\n' '{pw}' > "$PWFILE"
sshpass -f "$PWFILE" ssh -p {port} -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \\
  -o PreferredAuthentications=password -o PubkeyAuthentication=no -o ConnectTimeout=12 \\
  root@127.0.0.1 'bash -s' <<'REMOTE'
{REMOTE}
REMOTE
rc=$?; rm -f "$PWFILE"; echo "RC=$rc"
'''
    si, so, se = mg.exec_command(script, timeout=300)
    print(so.read().decode('utf-8', 'replace'))
    err = se.read().decode('utf-8', 'replace')
    if err.strip(): print('[stderr]', err[:300])
    mg.close()
    print('''
== 绑定建议决策表（人工核对上面数据后套用）==
  路由含 59.43(CN2) 或电信线    → 主用 BWG-VLESS-JPTY-01，备 腾讯vless→腾讯hy2
  联通线（吞吐 ≥1MB/s 且丢包<10%）→ 主用 BWG-VLESS-JPTY-01，备 腾讯vless→腾讯hy2
  移动/广电线（202.x/223.x 骨干） → 主用 腾讯hy2，备 腾讯vless→BWG（待 R4 广电实测校准）
  代理吞吐 <200KB/s（流级不稳）  → 用 TLS 往返最快者当主用，其余全挂备用
  确定后：管理台 assignments/batch 改 primary → preview 六项核对 → apply → P5 八项验收''')

if __name__ == '__main__':
    main()
