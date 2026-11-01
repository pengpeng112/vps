#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""r4-onboard.py — R4(rtr-cd473f) 上线后的一键完整接入（全远程，无需现场）
【2026-10-02 留档：R4 已完成接入（管理面=DMIT 主、代理=LAX、排障口 13224@DMIT），本脚本使命完成，仅留档勿再执行；MGMT 常量等未更新为新架构】

背景（2026-09-30 定）：R4 的 tunnel.conf 仍配 RSSH 13224，被 R3 占用 → R4 一上线 -R 绑定
必失败且 dbclient 无 ExitOnForwardFailure 会卡死 → 无 shell。解法=换向：**先把 R3 移到
13227（R3 在线可安全操作），R4 保住 13224 不改配置**，重启 R4 隧道（API 下发
repair_mgmt_tunnel，走 -L 心跳通道）即自动抢占 13224。

前置（已提前做好，不占运行时间）：DO authorized_keys 已为 snR3 预授权 13227。

执行序（每步失败即停，不自动重试发布类动作）：
  S1 R3 移端口 13224→13227：改 tunnel.conf + 重启 mgmt-tunnel + 双通道验证（心跳/-R banner）
     （注：R3 已于 10-01 迁至 13227 且探针映射已更新，复跑用 --skip-s1）
  S2 API 下发 repair_mgmt_tunnel 给 R4（经 -L），等待 R4 抢占 13224 + banner 验证
  S3 sshpass 进 13224 验身份（必须 rtr-cd473f，否则停止）
  S3.5 升级 v2 温备就绪形态（2026-10-01）：调 deploy-parallel-tunnel.py --num 4 --with-bwg
     —— 三端点隧道 + MGMT_TARGET=127.0.0.1:8443 + agent 选路补丁 + discovery cron
  S4 点位体检：三节点 ICMP + TLS 往返 + 吞吐 + traceroute
  S5 UDP 门槛（仅当考虑 TX-HY2 主用）：R3 同款抓包法测 R4→TX UDP 443
  S6 探针补装（fleet-netprobe-install 同款逻辑）
  S7 绑定决策并发布：UDP通→保持 TX-HY2 主；UDP死但 BWG TLS 通→BWG-VLESS 主(failover 1/2)；
     全死→不发布仅报告。发布走 watchdog，失败即停。
  S8 台账提示输出（人工或脚本改 台账/记忆：R3=13227, R4=13224）

用法：python r4-onboard.py            # R4 已上线(管理台 online=true)后运行
      python r4-onboard.py --wait 90  # 先等 R4 上线最多 90 分钟再自动执行
注意：S1 会短暂重启 R3 的隧道（-L 心跳闪断一次），避开 R3 业务高峰执行更稳。
"""
import argparse, os, subprocess, sys, time, re, paramiko

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, 'scripts'))
MGMT = ('159.223.36.119', 28722)
MGMT_KEY = os.path.expanduser('~/.ssh/id_ed25519_digitalocean')

def mgmt_connect():
    mg = paramiko.SSHClient(); mg.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    mg.connect(MGMT[0], port=MGMT[1], username='root', key_filename=MGMT_KEY,
               look_for_keys=False, allow_agent=False, timeout=20, auth_timeout=20)
    return mg

def run_on_device(mg, port, pw, script, timeout=180):
    """经管理机 sshpass 链在设备上执行 shell（script 为 bash 片段）。"""
    inner = f'''umask 077; PWFILE=$(mktemp); printf '%s\\n' '{pw}' > "$PWFILE"
sshpass -f "$PWFILE" ssh -p {port} -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \\
  -o PreferredAuthentications=password -o PubkeyAuthentication=no -o ConnectTimeout=12 \\
  root@127.0.0.1 'bash -s' <<'REMOTE_EOF'
{script}
REMOTE_EOF
rc=$?; rm -f "$PWFILE"; echo "DEV_RC=$rc"'''
    si, so, se = mg.exec_command(inner, timeout=timeout)
    return so.read().decode('utf-8', 'replace')

def api(method, path, js=None, headers=None, timeout=20):
    import requests, urllib3
    urllib3.disable_warnings()
    from mgmt_api_test import _ledger_pw, totp, BASE
    raise SystemExit('placeholder')  # 由 main 里闭包实现，避免重复登录

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--wait', type=int, default=0, help='先等 R4 上线的分钟数上限')
    ap.add_argument('--skip-s1', action='store_true', help='R3 已在 13227 时跳过 S1')
    a = ap.parse_args()
    import requests, urllib3
    urllib3.disable_warnings()
    from mgmt_api_test import _ledger_pw, totp, BASE
    ADMIN_PW, TOTP_SECRET = _ledger_pw()

    sess = requests.Session(); sess.verify = False
    def login():
        for _ in range(4):
            r = sess.post(BASE+"/api/v1/auth/login", json={"username":"admin","password":ADMIN_PW}, timeout=12)
            r2 = sess.post(BASE+"/api/v1/auth/totp", json={"ticket":r.json().get("ticket"),"code":totp()}, timeout=12)
            if r2.status_code == 200:
                return {"Authorization":"Bearer "+r2.json().get("token","")}
            time.sleep(14)
        raise SystemExit('API 登录失败')
    H = login()

    def r4_online():
        try:
            d = sess.get(BASE+"/api/v1/devices/rtr-cd473f", headers=H, timeout=10).json().get("device")
            return bool(d.get("online"))
        except Exception:
            return False

    if a.wait:
        print(f'等待 R4 上线（最长 {a.wait} 分钟）…')
        t0 = time.time()
        while time.time() - t0 < a.wait*60:
            if r4_online():
                print('R4 已上线：', time.strftime('%H:%M:%S')); break
            time.sleep(60)
        else:
            print('R4_onboard: 超时仍未上线，退出（无任何变更）'); return

    mg = mgmt_connect()
    def mcmd(c, t=60):
        si, so, se = mg.exec_command(c, timeout=t)
        return (so.read()+se.read()).decode('utf-8','replace')

    pw3 = open(os.path.join(REPO,'backups','.snr3-pw'), encoding='utf-8').read().splitlines()[0].strip()
    pw4 = open(os.path.join(REPO,'backups','.snr4-pw'), encoding='utf-8').read().splitlines()[0].strip()

    # ---- S1: R3 移端口 ----
    if not a.skip_s1:
        print('== S1: R3 13224 -> 13227 ==')
        out = run_on_device(mg, 13224, pw3, '''
cp /etc/routermgmt/tunnel.conf /etc/routermgmt/tunnel.conf.bak-$(date +%Y%m%d-%H%M%S)
sed -i "s/^RSSH_REMOTE_PORT=13224/RSSH_REMOTE_PORT=13227/" /etc/routermgmt/tunnel.conf
grep RSSH_REMOTE_PORT /etc/routermgmt/tunnel.conf
/etc/init.d/mgmt-tunnel restart >/dev/null 2>&1; sleep 8
echo restart_done''')
        print(out.strip()[-200:])
        time.sleep(6)
        chk = mcmd("ss -tlnp | grep ':13227 ' && timeout 4 nc 127.0.0.1 13227 </dev/null | head -c 12; echo; ss -tlnp | grep ':13224 ' || echo 13224已释放")
        print(chk.strip())
        assert 'SSH-' in chk and '13224已释放' in chk, 'S1 验证失败：13227 无 banner 或 13224 未释放，停止'
        idv = run_on_device(mg, 13227, pw3, 'grep DEVICE_ID /etc/routermgmt/agent.conf')
        assert 'rtr-38aa54' in idv, 'S1 身份异常，停止'
        chk2 = mcmd("grep -q '^13227 rtr-38aa54' /etc/mgmt-rssh-probe.conf || echo '13227 rtr-38aa54' >> /etc/mgmt-rssh-probe.conf; grep -q '^13224 rtr-cd473f' /etc/mgmt-rssh-probe.conf || echo '13224 rtr-cd473f' >> /etc/mgmt-rssh-probe.conf; rm -f /var/lib/mgmt-rssh-probe/13224.state; systemctl start mgmt-rssh-probe.service 2>/dev/null; grep -E '^1322' /etc/mgmt-rssh-probe.conf")
        print('探测映射终态:', chk2.strip().replace(chr(10), ' ; '))
        print('S1 完成：R3 已在 13227，13224 已让出')
    else:
        print('S1 跳过（--skip-s1）')

    # ---- S2: 唤醒 R4 抢占 13224 ----
    print('== S2: repair_mgmt_tunnel -> R4 ==')
    if not r4_online():
        print('R4 不在线，无法下发修复任务，停止（S1 已完成，R4 上线后用 --skip-s1 重跑）'); return
    import uuid as _uuid
    rr = sess.post(BASE+"/api/v1/devices/rtr-cd473f/ops", headers={**H,"Idempotency-Key":str(_uuid.uuid4())},
                   json={"type":"repair_mgmt_tunnel"}, timeout=15)
    print('dispatch:', rr.status_code, rr.text[:100])
    ok = False
    for i in range(15):
        time.sleep(12)
        tk = sess.get(BASE+"/api/v1/tasks?limit=5&device_id=rtr-cd473f", headers=H, timeout=10).json()
        items = tk.get("tasks") or tk.get("items") or []
        st = next((t for t in items if t.get("type")=="repair_mgmt_tunnel"), None)
        if st and st.get("status") in ("ok","failed","expired"):
            print('repair:', st.get("status"), str(st.get("result"))[:100])
            ok = st.get("status") == "ok"; break
    if not ok:
        print('S2 修复任务未成功，停止'); return
    time.sleep(10)
    b = mcmd("timeout 4 nc 127.0.0.1 13224 </dev/null | head -c 12; echo")
    print('13224 banner:', b.strip())
    assert 'SSH-' in b, 'R4 未抢占 13224，停止'

    # ---- S3: R4 身份 ----
    print('== S3: R4 身份 ==')
    idv = run_on_device(mg, 13224, pw4, 'grep DEVICE_ID /etc/routermgmt/agent.conf; ifstatus wan | grep -E \\"address\\" | head -1')
    print(idv.strip()[:150])
    assert 'rtr-cd473f' in idv, '13224 背后不是 R4！停止并人工核查'

    # ---- S3.5: 升级 v2 温备就绪形态（2026-10-01）----
    # 复用 deploy-parallel-tunnel.py（自带备份/回滚/验证）：三端点隧道 +
    # MGMT_TARGET=127.0.0.1:8443 + agent 选路补丁 + discovery cron。
    print('== S3.5: 升级 v2 温备就绪形态 ==')
    r35 = subprocess.run([sys.executable, os.path.join(REPO, 'scripts', 'deploy-parallel-tunnel.py'),
                          '--num', '4', '--with-bwg'], capture_output=True, text=True, timeout=300)
    print(r35.stdout[-700:])
    if r35.returncode != 0 or 'AGENT-PATCH-OK' not in r35.stdout:
        print('S3.5 失败（可单独复跑 deploy-parallel-tunnel.py --num 4 --with-bwg），停止'); return
    if '发现端口: 18443' not in r35.stdout:
        print('S3.5 警告：未确认主端口 18443，人工复核 discovery 输出后继续')
    print('S3.5 完成：R4 温备就绪（三端点，MGMT_TARGET=127.0.0.1:8443）')

    # ---- S4: 体检 ----
    print('== S4: 点位体检（R4） ==')
    out = run_on_device(mg, 13224, pw4, '''
echo "-- ICMP 三节点 --"
for pair in "bwg 74.82.203.175" "tx 43.134.46.44" "do 159.223.36.119"; do
  set -- $pair; echo -n "  $1: "; ping -c 20 -q -w 25 "$2" 2>/dev/null | tail -2 | tr "\\n" " "; echo
done
echo "-- TLS 往返 --"
for pair in "bwg 74.82.203.175" "tx 43.134.46.44"; do
  set -- $pair; s=$(date +%s); wget --no-check-certificate -T 8 -q -O /dev/null "https://$2/" 2>/dev/null; echo "  $1: rc=$? 耗时$(( $(date +%s)-s ))s"
done
echo "-- 代理吞吐 12s --"
curl -s --max-time 12 -x http://127.0.0.1:7890 -o /dev/null -w "  %{speed_download}B/s\\n" "https://speed.cloudflare.com/__down?bytes=4000000"
echo "-- 路由特征 --"
traceroute -n -q 1 -w 2 -m 12 74.82.203.175 2>&1 | sed -n "3,8p"
wget -qO- --timeout=5 http://127.0.0.1:9090/proxies/NODE-PICK 2>/dev/null | grep -oE "\\"now\\":\\"[^\\"]+\\""
''', timeout=240)
    print(out)

    # ---- S5: UDP 门槛（TX） ----
    print('== S5: UDP 门槛 R4→TX:443（抓包在 TX 侧需另跑，此处先用 nc+nslookup 初判） ==')
    out = run_on_device(mg, 13224, pw4, '''
nslookup qq.com 43.134.46.44 2>/dev/null | tail -1
for i in 1 2 3; do echo u$i | nc -u -w 2 43.134.46.44 443 2>/dev/null; done; echo sent''')
    print(out.strip())
    print('  （准确判别需在 TX 侧源过滤抓包，若 nslookup 有应答=UDP 通；无应答再人工抓包复核）')

    # ---- S6: 探针 ----
    print('== S6: 探针补装 ==')
    probe = open(os.path.join(REPO,'mgmt','agent','netprobe.sh'), encoding='utf-8').read()
    out = run_on_device(mg, 13224, pw4, f'''
cat > /etc/routermgmt/netprobe.tmp <<'PROBE_EOF'
{probe}
PROBE_EOF
chmod 755 /etc/routermgmt/netprobe.tmp && mv -f /etc/routermgmt/netprobe.tmp /etc/routermgmt/netprobe.sh
mkdir -p /etc/routermgmt/logs
grep -q routermgmt-netprobe /etc/crontabs/root 2>/dev/null || echo '*/5 * * * * /etc/routermgmt/netprobe.sh #routermgmt-netprobe' >> /etc/crontabs/root
/etc/init.d/cron restart >/dev/null 2>&1 || true
grep routermgmt-netprobe /etc/crontabs/root
/etc/routermgmt/netprobe.sh; sleep 2; tail -n 5 /etc/routermgmt/logs/netprobe.log''')
    print(out.strip()[-500:])

    print('''
== S7 绑定决策（按 S4/S5 数据人工确认后执行）==
  UDP→TX 通   : 保持现绑定（TX-HY2 主）→ API apply
  UDP 死/BWG好: BWG-VLESS 设 primary(failover 1/2=TX-HY2,TX-VLESS) → preview 六项 → apply
  全死        : 不发布，报告数据
  发布命令参考本日执行记录（preview 校验务必用完整规则行匹配）
== S8 台账更新：R3=13227，R4=13224（保持原配置不再改）==''')
    mg.close()

if __name__ == '__main__':
    main()
