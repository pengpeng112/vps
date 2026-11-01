#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fleet-netprobe-install.py — 给 SN-R 舰队设备补装/刷新 netprobe 探针（幂等）

用法（设备上线后执行）：
  python fleet-netprobe-install.py --num 3            # R3（口令自动取 backups/.snr3-pw）
  python fleet-netprobe-install.py --num 4 --pw-file F:/vps/backups/.snr4-pw

动作：经管理机(179.255.137.64:28722, root+密钥) sshpass 链入设备 →
  1) 推送 mgmt/agent/netprobe.sh（含 bwg 东京目标）到 /etc/routermgmt/netprobe.sh (755)
     —— 设备 busybox 无 base64，用内层 heredoc 直传
  2) 确保 crontab 有 */5 条目并重启 cron
  3) 立即跑一轮 → tail 日志验证
安全：不改任何业务配置；脚本与 cron 均为只读探测；失败不重试不扩大。
"""
import argparse, os, sys, paramiko

MGMT = ('179.255.137.64', 28722)  # 2026-10-02 管理面 DMIT
MGMT_KEY = os.path.expanduser('~/.ssh/id_rsa_dmit')
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RSSH = {2: 13223, 3: 13227, 4: 13224, 5: 13225, 6: 13226}  # R3 已迁13227(10-01),R4=13224  # R4 改 13227 后更新

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--num', type=int, required=True, help='设备序号 N（SN-RN）')
    ap.add_argument('--rssh', type=int, default=None, help='覆盖默认 RSSH 端口')
    ap.add_argument('--pw-file', default=None, help='口令文件（默认 backups/.snrN-pw 第一行）')
    a = ap.parse_args()
    port = a.rssh or RSSH[a.num]
    pwfile = a.pw_file or os.path.join(REPO, 'backups', f'.snr{a.num}-pw')
    pw = open(pwfile, encoding='utf-8').read().splitlines()[0].strip()
    probe = open(os.path.join(REPO, 'mgmt', 'agent', 'netprobe.sh'), encoding='utf-8').read()
    assert 'PROBE_EOF' not in probe and 'REMOTE_EOF' not in probe, 'netprobe.sh 内含 heredoc 保留字'

    # 注意：remote 是 f-string，含花括号的 shell 块必须外置成普通字符串再嵌入
    # （10-01 教训：直接在 f-string 里写 { echo ... } = SyntaxError，脚本报废）
    hook_install = r'''cp /etc/rc.local /etc/rc.local.bak-cronfix 2>/dev/null
sed -i '/# netprobe-cron-restore/,+1d' /etc/rc.local 2>/dev/null
if ! grep -q "routermgmt-cron-restore" /etc/rc.local 2>/dev/null; then
  sed -i '/^exit 0$/i\# routermgmt-cron-restore (10-01: 厂商开机重写crontab, netprobe+discovery 两条开机重加; discovery 丢了温备切换只剩心跳失败触发一条路)\n(sleep 10; grep -q routermgmt-netprobe /etc/crontabs/root 2>/dev/null || echo "*/5 * * * * /etc/routermgmt/netprobe.sh #routermgmt-netprobe" >> /etc/crontabs/root; grep -q mgmt-port-discovery /etc/crontabs/root 2>/dev/null || echo "* * * * * /etc/routermgmt/mgmt-port-discovery.sh #mgmt-port" >> /etc/crontabs/root; /etc/init.d/cron restart) > /dev/null 2>&1 &' /etc/rc.local
fi
sh -n /etc/rc.local && echo "rc.local 双条恢复钩子OK" || { cp /etc/rc.local.bak-cronfix /etc/rc.local; echo "WARN: 语法失败已回滚"; }'''

    mg = paramiko.SSHClient(); mg.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    mg.connect(MGMT[0], port=MGMT[1], username='root', key_filename=MGMT_KEY,
               look_for_keys=False, allow_agent=False, timeout=20, auth_timeout=20)
    remote = f'''echo "== 身份 =="; grep DEVICE_ID /etc/routermgmt/agent.conf
echo "== 推送 netprobe.sh =="
cat > /etc/routermgmt/netprobe.tmp <<'PROBE_EOF'
{probe}
PROBE_EOF
chmod 755 /etc/routermgmt/netprobe.tmp
mv -f /etc/routermgmt/netprobe.tmp /etc/routermgmt/netprobe.sh
mkdir -p /etc/routermgmt/logs
echo "== cron =="
grep -q routermgmt-netprobe /etc/crontabs/root 2>/dev/null || echo '*/5 * * * * /etc/routermgmt/netprobe.sh #routermgmt-netprobe' >> /etc/crontabs/root
echo "== rc.local 双条恢复钩子（10-01 实锤：厂商开机重写 crontab，netprobe/discovery 都会丢）=="
{hook_install}
/etc/init.d/cron restart >/dev/null 2>&1 || /etc/init.d/crond restart >/dev/null 2>&1
(crontab -l 2>/dev/null || cat /etc/crontabs/root) | grep routermgmt-netprobe
echo "== 首轮采样 =="
/etc/routermgmt/netprobe.sh; sleep 2
tail -n 7 /etc/routermgmt/logs/netprobe.log'''
    script = f'''umask 077; PWFILE=$(mktemp); printf '%s\\n' '{pw}' > "$PWFILE"
sshpass -f "$PWFILE" ssh -p {port} -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \\
  -o PreferredAuthentications=password -o PubkeyAuthentication=no -o ConnectTimeout=12 \\
  root@127.0.0.1 'bash -s' <<'REMOTE_EOF'
{remote}
REMOTE_EOF
rc=$?; rm -f "$PWFILE"; echo "RC=$rc"
'''
    si, so, se = mg.exec_command(script, timeout=180)
    print(so.read().decode('utf-8', 'replace'))
    err = se.read().decode('utf-8', 'replace')
    if err.strip(): print('[stderr]', err[:300])
    mg.close()

if __name__ == '__main__':
    main()
