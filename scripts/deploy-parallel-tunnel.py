#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""deploy-parallel-tunnel.py — 在路由器上部署多端点并行管理隧道（mgmt-tunnel v2 温备就绪形态）

用法：
  python deploy-parallel-tunnel.py --num 3            # R3
  python deploy-parallel-tunnel.py --num 4 --with-bwg # R4（三路）
  python deploy-parallel-tunnel.py --num 2 --rssh-target 192.168.132.1:22

动作：
  1) 备份原 /etc/init.d/mgmt-tunnel 和 tunnel.conf
  2) 部署 mgmt-tunnel v2 init（含三坑修复：respawn 3600 0 0 + ExitOnForwardFailure）
  3) 写 tunnel.conf：MGMT_TARGET=127.0.0.1:8443（每台 VPS 服务自己的 API，DO 挂后
     温备可用）+ VPS2(TX)/VPS3(BWG) 备用端点；RSSH_TARGET 保留设备现网值
  4) 部署 mgmt-port-discovery.sh + agent 选路补丁（循环读 active_port + 心跳失败触发
     discovery）+ discovery 每分钟 cron
  5) 清理旧 agent 进程、重启 mgmt-tunnel 与 agent，验证全部实例与心跳

前置（2026-10-01 已就绪）：TX/BWG 28722 均为 sshd 直连（非 socat）；路由器公钥已在
TX/BWG authorized_keys（permitopen 双目标重复选项格式——严禁逗号列表）。

安全：
  - 原有 DO 隧道语义不变（只改转发目标为本机回环，DO 侧已开 127.0.0.1:8443）
  - 配置逐字段校验（白名单 key=value，不 source）；agent 补丁 sh -n 失败自动回滚
  - 回滚 = 恢复 .bak 文件 + 重启
"""
import argparse, os, sys, time, paramiko

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MGMT = ('179.255.137.64', 28722)  # 2026-10-02 管理面 DMIT 中心化
MGMT_KEY = os.path.expanduser('~/.ssh/id_rsa_dmit')
UNIFIED_PW = '[REDACTED]'

# 各设备的 RSSH 端口（与 fleet 脚本同表）
RSSH = {2: 13223, 3: 13227, 4: 13224, 5: 13225, 6: 13226}  # R3 已迁13227(10-01),R4=13224

INIT_V2 = open(os.path.join(REPO, 'mgmt', 'agent', 'init', 'mgmt-tunnel-v2'), encoding='utf-8').read()
PORT_DISCOVERY = open(os.path.join(REPO, 'mgmt', 'agent', 'mgmt-port-discovery.sh'), encoding='utf-8').read()

def tunnel_conf_v2(rssh_port, with_bwg=False, rssh_target='192.168.132.1:22'):
    lines = [
        '# mgmt-tunnel v3 配置 — 2026-10-02 管理面 DMIT 主台（四端点）',
        '# 主端点（DMIT 直连，-L + -R）；MGMT_TARGET=127.0.0.1 = 每台 VPS 服务自己的 API',
        'VPS_HOST=179.255.137.64',
        'VPS_PORT=28722',
        'MGMT_LOCAL_PORT=18443',
        'MGMT_TARGET=127.0.0.1:8443',
        f'RSSH_REMOTE_PORT={rssh_port}',
        f'RSSH_TARGET={rssh_target}',
        '',
        '# 备用端点 2（TX 腾讯 sshd 直连，仅 -L，本地 :18444）',
        'VPS2_HOST=43.134.46.44',
        'VPS2_PORT=28722',
    ]
    if with_bwg:
        lines += [
            '',
            '# 备用端点 3（BWG 东京 sshd 直连，仅 -L，本地 :18445）',
            'VPS3_HOST=74.82.203.175',
            'VPS3_PORT=28722',
        ]
    lines.append('')
    return '\n'.join(lines)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--num', type=int, required=True)
    ap.add_argument('--rssh', type=int, default=None)
    ap.add_argument('--pw', default=UNIFIED_PW)
    ap.add_argument('--with-bwg', action='store_true')
    ap.add_argument('--rssh-target', default='192.168.132.1:22',
                    help='无现网值时的 RSSH_TARGET 兜底（默认 SN-R 系列值）')
    a = ap.parse_args()

    port = a.rssh or RSSH[a.num]
    pw = a.pw
    # RSSH_TARGET 现网值优先（remote 侧替换 __RSSH_TARGET__ 占位符），参数仅兜底
    conf = tunnel_conf_v2(port, a.with_bwg, '__RSSH_TARGET__')

    mg = paramiko.SSHClient(); mg.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    mg.connect(MGMT[0], port=MGMT[1], username='root', key_filename=MGMT_KEY,
               look_for_keys=False, allow_agent=False, timeout=20, auth_timeout=20)

    remote = f'''echo "== 身份 =="
grep DEVICE_ID /etc/routermgmt/agent.conf
echo "== 备份 =="
TS=$(date +%Y%m%d-%H%M%S)
cp /etc/init.d/mgmt-tunnel /etc/init.d/mgmt-tunnel.bak-$TS
cp /etc/routermgmt/tunnel.conf /etc/routermgmt/tunnel.conf.bak-$TS
cp /etc/routermgmt/routermgmt-agent.sh /etc/routermgmt/routermgmt-agent.sh.bak-$TS 2>/dev/null
cp /usr/bin/routermgmt-agent /usr/bin/routermgmt-agent.bak-$TS 2>/dev/null
echo "备份: mgmt-tunnel.bak-$TS tunnel.conf.bak-$TS"

echo "== 部署 init v2 =="
cat > /etc/init.d/mgmt-tunnel <<'INIT_EOF'
{INIT_V2}
INIT_EOF
chmod 755 /etc/init.d/mgmt-tunnel

echo "== 部署 tunnel.conf v2 =="
OLD_RT=$(grep '^RSSH_TARGET=' /etc/routermgmt/tunnel.conf 2>/dev/null | head -1 | cut -d= -f2)
[ -n "$OLD_RT" ] || OLD_RT='{a.rssh_target}'
echo "RSSH_TARGET 使用: $OLD_RT"
cat > /etc/routermgmt/tunnel.conf <<'CONF_EOF'
{conf}
CONF_EOF
sed -i "s|__RSSH_TARGET__|$OLD_RT|" /etc/routermgmt/tunnel.conf
chmod 600 /etc/routermgmt/tunnel.conf
grep -E "^(MGMT_TARGET|RSSH_TARGET|VPS2_HOST|VPS3_HOST)" /etc/routermgmt/tunnel.conf

echo "== 部署 port-discovery =="
cat > /etc/routermgmt/mgmt-port-discovery.sh <<'PD_EOF'
{PORT_DISCOVERY}
PD_EOF
chmod 755 /etc/routermgmt/mgmt-port-discovery.sh

echo "== agent 选路补丁（幂等，sh -n 失败回滚） =="
if ! grep -q mgmt_active_port /etc/routermgmt/routermgmt-agent.sh; then
  awk '{{
    print
    if ($0 ~ /^    while :; do$/) {{
      print "        # 温备多端口选路（2026-10-01）：discovery 结果优先于 agent.conf 固定值"
      print "        [ -f /tmp/mgmt_active_port ] && MGMT_URL=\\"https://127.0.0.1:$(cat /tmp/mgmt_active_port 2>/dev/null)\\""
    }}
  }}' /etc/routermgmt/routermgmt-agent.sh > /tmp/agent.new && mv /tmp/agent.new /etc/routermgmt/routermgmt-agent.sh
fi
if ! grep -q "mgmt-port-discovery.sh >/dev/null" /etc/routermgmt/routermgmt-agent.sh; then
  sed -i "s|send_heartbeat && _last_hb=\$_now|send_heartbeat \\&\\& _last_hb=\$_now \\|\\| /etc/routermgmt/mgmt-port-discovery.sh >/dev/null 2>\\&1 \\&|" /etc/routermgmt/routermgmt-agent.sh
fi
if sh -n /etc/routermgmt/routermgmt-agent.sh; then
  chmod 700 /etc/routermgmt/routermgmt-agent.sh
  cp /etc/routermgmt/routermgmt-agent.sh /usr/bin/routermgmt-agent && chmod 755 /usr/bin/routermgmt-agent
  echo "AGENT-PATCH-OK"
else
  cp /etc/routermgmt/routermgmt-agent.sh.bak-$TS /etc/routermgmt/routermgmt-agent.sh
  cp /usr/bin/routermgmt-agent.bak-$TS /usr/bin/routermgmt-agent
  echo "AGENT-PATCH-FAILED-ROLLEDBACK"; exit 1
fi

echo "== discovery cron =="
(crontab -l 2>/dev/null | grep -v mgmt-port-discovery; echo "* * * * * /etc/routermgmt/mgmt-port-discovery.sh #mgmt-port") | crontab -

echo "== 重启隧道与 agent =="
/etc/init.d/mgmt-tunnel restart
for p in $(ps w | grep "[r]outermgmt-agent" | awk "{{print \\$1}}"); do kill $p 2>/dev/null; done
sleep 1
/etc/init.d/routermgmt restart
sleep 8

echo "== 验证：实例列表 =="
ps w | grep 1844 | grep -v grep | wc -l | xargs echo "活跃隧道实例数:"

echo "== 验证：各端口 API 可达（温备停用属正常显示 000）=="
for p in 18443 18444 18445; do
  R=$(curl -sk -m 3 -o /dev/null -w "%{{http_code}}" https://127.0.0.1:$p/api/v1/health 2>/dev/null)
  echo "  :$p → $R"
done

echo "== 验证：port-discovery =="
/etc/routermgmt/mgmt-port-discovery.sh && echo "发现端口: $(cat /tmp/mgmt_active_port)" || echo "三端口均不可达"

echo "== 验证：agent 补丁与心跳 =="
grep -c mgmt_active_port /usr/bin/routermgmt-agent | xargs echo "补丁标记数:"
sleep 5
logread | grep routermgmt-agent | tail -2
'''

    inner = f'''umask 077; PWFILE=$(mktemp); printf '%s\\n' '{pw}' > "$PWFILE"
sshpass -f "$PWFILE" ssh -p {port} -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \\
  -o PreferredAuthentications=password -o PubkeyAuthentication=no -o ConnectTimeout=12 \\
  root@127.0.0.1 'bash -s' <<'REMOTE_EOF'
{remote}
REMOTE_EOF
rc=$?; rm -f "$PWFILE"; echo "DEV_RC=$rc"'''

    si, so, se = mg.exec_command(inner, timeout=120)
    print(so.read().decode('utf-8', 'replace'))
    err = se.read().decode('utf-8', 'replace')
    if err.strip():
        print('[stderr]', err[:300])
    mg.close()

if __name__ == '__main__':
    main()
