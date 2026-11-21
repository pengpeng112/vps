#!/usr/bin/env python3
# 雅典娜管理面升级 DMIT 一次性部署脚本 v2（2026-10-10）
# 路径：本机 → DO(id_ed25519_digitalocean:28722) → 雅典娜(18222, 密码=环境变量 ATHENA_PW)
# 传输：命令整体 base64 后在设备端 `openssl base64 -d -A | sh` 执行——两层 ssh 间无引号嵌套
# 内容：v3 隧道(三端点, -R 13228) + 当前版 agent(多端口补丁) + discovery/connlog/netprobe
#       + 雅典娜变体 onboot-hooks(仅cron恢复) + agent.conf(rtr-5e1a8d)
# 红线：不动 handlers/mihomo/nftables/WiFi/密码；不部署 SN-R iptables 脚本；备份先行，md5 门禁。
import base64, hashlib, json, os, sys, time
import paramiko

REPO = r"F:\vps"
DO_HOST, DO_PORT = "159.223.36.119", 28722
PW = os.environ["ATHENA_PW"]
PKG = json.load(open(REPO + r"\backups\.athena-enroll.json", encoding="utf-8"))

TUNNEL_CONF = """\
# mgmt-tunnel v3 配置 — 2026-10-10 雅典娜管理面迁 DMIT（原 v1→DO 18222，备份 .bak-athena-*）
# 主端点（DMIT，-L 管理 API + -R 排障口 13228）
VPS_HOST=179.255.137.64
VPS_PORT=28722
MGMT_LOCAL_PORT=18443
MGMT_TARGET=127.0.0.1:8443
RSSH_REMOTE_PORT=13228
RSSH_TARGET=127.0.0.1:22
# 备用端点 2（TX 腾讯，仅 -L，本地 :18444；8443 中继待修，discovery 会自动跳过）
VPS2_HOST=43.134.46.44
VPS2_PORT=28722
# 备用端点 3（BWG 东京，仅 -L，本地 :18445）
VPS3_HOST=74.82.203.175
VPS3_PORT=28722
"""

ONBOOT_ATH = """\
#!/bin/sh
# onboot-hooks — 雅典娜变体（2026-10-10 管理面迁 DMIT）
# 仅 crontab 恢复。与 SN-R 版差异：无 QUIC/iptables（本机 nftables 红线）、无每日 0 点重启（家庭/医院依赖设备）。
( sleep 20
  CT=/etc/crontabs/root
  mkdir -p /etc/crontabs; touch "$CT"
  for E in \\
    '*/5 * * * * /etc/routermgmt/netprobe.sh #routermgmt-netprobe' \\
    '* * * * * /etc/routermgmt/mgmt-port-discovery.sh #mgmt-port' \\
    '*/5 * * * * /etc/routermgmt/connlog.sh #routermgmt-connlog'; do
    TAG=$(echo "$E" | sed 's/.*#\\([^ ]]*\\).*$/#\\1/')
    grep -q "$TAG" "$CT" 2>/dev/null || echo "$E" >> "$CT"
  done
  /etc/init.d/cron restart
) > /dev/null 2>&1 &
exit 0
"""

AGENT_CONF = (f"DEVICE_ID={PKG['device_id']}\n"
              f"HMAC_KEY={PKG['hmac_key']}\n"
              "MGMT_URL=https://127.0.0.1:18443\n"
              f"SUB_URL={PKG.get('sub_url','')}\n"
              "SIGN_PUB=installed\n")

SIGN_PEM = PKG["sign_public_key"] + "\n"

PUSH = [
    (REPO + r"\mgmt\agent\init\mgmt-tunnel-v3", "/etc/init.d/mgmt-tunnel", 755),
    (TUNNEL_CONF,  "/etc/routermgmt/tunnel.conf", 600),
    (REPO + r"\mgmt\agent\routermgmt-agent.sh",  "/etc/routermgmt/routermgmt-agent.sh", 700),
    (AGENT_CONF,   "/etc/routermgmt/agent.conf", 600),
    (SIGN_PEM,     "/etc/routermgmt/sign_pub.pem", 644),
    (REPO + r"\mgmt\agent\mgmt-port-discovery.sh", "/etc/routermgmt/mgmt-port-discovery.sh", 755),
    (REPO + r"\mgmt\agent\connlog.sh",   "/etc/routermgmt/connlog.sh", 755),
    (REPO + r"\mgmt\agent\netprobe.sh",  "/etc/routermgmt/netprobe.sh", 755),
    (ONBOOT_ATH,   "/etc/routermgmt/onboot-hooks.sh", 755),
    (REPO + r"\mgmt\agent\init\routermgmt", "/etc/init.d/routermgmt", 755),
]

cli = paramiko.SSHClient(); cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(DO_HOST, port=DO_PORT, username="root",
            key_filename=REPO + r"\VPS远程工具包\id_ed25519_digitalocean",
            timeout=15, allow_agent=False, look_for_keys=False)

def do(cmd, timeout=60):
    _, o, e = cli.exec_command(cmd, timeout=timeout)
    return o.read().decode(errors="replace"), e.read().decode(errors="replace")

do("umask 077; printf '%s' '" + PW + "' > /tmp/.apw")
INNER = "sshpass -f /tmp/.apw ssh -p 18222 -o StrictHostKeyChecking=no root@127.0.0.1"

def athena(cmd, timeout=60):
    b = base64.b64encode(cmd.encode()).decode()
    out, err = do(f'{INNER} "echo {b} | openssl base64 -d -A | sh"', timeout=timeout)
    return out + (("\n[err] " + err) if err.strip() and "Warning" not in err else "")

# 0) 备份
ts = time.strftime("%Y%m%d-%H%M%S")
print(athena(f"TS={ts}; for f in /etc/init.d/mgmt-tunnel /etc/init.d/routermgmt /etc/routermgmt/tunnel.conf /etc/routermgmt/agent.conf /etc/routermgmt/sign_pub.pem /etc/routermgmt/routermgmt-agent.sh /etc/routermgmt/netprobe.sh; do [ -f $f ] && cp -a $f $f.bak-athena-$TS; done; echo BACKUP-DONE-$TS").strip())

# 1) 推文件（分块追加 + md5 门禁）
def content_of(src):
    if isinstance(src, str) and ("\n" in src or len(src) < 200) and not os.path.isfile(src):
        return src.encode()
    return open(src, "rb").read()

for src, dst, mode in PUSH:
    data = content_of(src)
    want = hashlib.md5(data).hexdigest()
    b64 = base64.b64encode(data).decode()
    chunks = [b64[i:i+1500] for i in range(0, len(b64), 1500)]
    for i, ch in enumerate(chunks):
        op = ">" if i == 0 else ">>"
        athena(f"printf '%s\\n' '{ch}' {op} {dst}.b64", timeout=60)
    got = athena(f"openssl base64 -d < {dst}.b64 > {dst}.new && rm -f {dst}.b64 && md5sum {dst}.new", timeout=90).split()[0]
    if got != want:
        print(f"MD5-FAIL {dst}: {got} != {want}"); do("rm -f /tmp/.apw"); sys.exit(1)
    athena(f"mv {dst}.new {dst} && chmod {mode} {dst}")
    print(f"OK {dst} md5={want[:12]}")

# 2) agent 双位置 + 多端口补丁（flash-snr 同款）
print(athena(r"""
R=/etc/routermgmt
cp $R/routermgmt-agent.sh /usr/bin/routermgmt-agent && chmod 755 /usr/bin/routermgmt-agent
if ! grep -q mgmt_active_port $R/routermgmt-agent.sh; then
  awk '{
    print
    if ($0 ~ /^    while :; do$/) {
      print "        # 温备多端口选路（2026-10-01）：discovery 结果优先于 agent.conf 固定值"
      print "        [ -f /tmp/mgmt_active_port ] && MGMT_URL=\"https://127.0.0.1:$(cat /tmp/mgmt_active_port 2>/dev/null)\""
    }
  }' $R/routermgmt-agent.sh > /tmp/agent.new && mv /tmp/agent.new $R/routermgmt-agent.sh
fi
if ! grep -q "mgmt-port-discovery.sh >/dev/null" $R/routermgmt-agent.sh; then
  sed -i "s|send_heartbeat && _last_hb=\$_now|send_heartbeat \&\& _last_hb=\$_now \|\| /etc/routermgmt/mgmt-port-discovery.sh >/dev/null 2>\&1 \&|" $R/routermgmt-agent.sh
fi
sh -n $R/routermgmt-agent.sh || { echo AGENT_SYNTAX_FAIL; exit 1; }
sh -n $R/onboot-hooks.sh || { echo ONBOOT_SYNTAX_FAIL; exit 1; }
cp $R/routermgmt-agent.sh /usr/bin/routermgmt-agent && chmod 755 /usr/bin/routermgmt-agent
echo PATCH-OK
""").strip())

# 3) cron + rc.local + respawn + init enable（幂等）
print(athena(r"""
mkdir -p /etc/crontabs
grep -q routermgmt-netprobe /etc/crontabs/root || echo '*/5 * * * * /etc/routermgmt/netprobe.sh #routermgmt-netprobe' >> /etc/crontabs/root
grep -q "discovery.sh #mgmt-port" /etc/crontabs/root || echo '* * * * * /etc/routermgmt/mgmt-port-discovery.sh #mgmt-port' >> /etc/crontabs/root
grep -q routermgmt-connlog /etc/crontabs/root || echo '*/5 * * * * /etc/routermgmt/connlog.sh #routermgmt-connlog' >> /etc/crontabs/root
grep -q onboot-hooks /etc/rc.local || sed -i 's|^exit 0|/etc/routermgmt/onboot-hooks.sh\nexit 0|' /etc/rc.local
sed -i 's/respawn 3600 [0-9]* [0-9]*/respawn 3600 0 0/' /etc/init.d/routermgmt
/etc/init.d/routermgmt enable; /etc/init.d/mgmt-tunnel enable
grep -v '^#' /etc/crontabs/root | grep routermgmt
echo CRON-OK
""").strip())

# 4) 预检（重启前必须全过）
pre = athena(r"""
echo CONF_KEYS: $(grep -cE '^(VPS_HOST|VPS_PORT|MGMT_LOCAL_PORT|MGMT_TARGET|RSSH_REMOTE_PORT|RSSH_TARGET|VPS2_HOST|VPS3_HOST)=' /etc/routermgmt/tunnel.conf)
echo AGENT_KEYS: $(grep -cE '^(DEVICE_ID|HMAC_KEY|MGMT_URL|SIGN_PUB)=' /etc/routermgmt/agent.conf)
echo NO_IPT: $(grep -c iptables /etc/routermgmt/onboot-hooks.sh || true)
""")
print(pre.strip())
if "CONF_KEYS: 8" not in pre or "AGENT_KEYS: 4" not in pre or "NO_IPT: 0" not in pre:
    print("PRECHECK-FAIL — 不重启，人工核查"); do("rm -f /tmp/.apw"); sys.exit(1)

# 5) 延迟重启（杀 v1 双进程→v3 起→agent 起）；本会话经 18222 会断，属预期
print(athena("nohup sh -c 'sleep 3; pkill -f \"ssh -i /etc/routermgmt/id_dropbear_mgmt\" 2>/dev/null; sleep 1; /etc/init.d/mgmt-tunnel restart; sleep 2; /etc/init.d/routermgmt restart' >/dev/null 2>&1 & echo RESTART-SCHEDULED").strip())
do("rm -f /tmp/.apw")
print("DONE — 40 秒后经 DMIT 13228 回连验收")
