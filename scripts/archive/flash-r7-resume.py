#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SN-R7 刷机续跑（2026-10-13）：中断点=步骤3后（口令统一/去内置/web3/openssl3 已完成）。
逐字复刻 flash-snr.py 步骤 4-10，N=7，RSSH=13229（已核实空闲）。设备 192.168.132.1 直连。"""
import base64, json, os, re, subprocess, sys, time
import paramiko, requests
sys.path.insert(0, r"F:\vps\scripts")
import mgmt_api_test as M

N = 7; NAME = f"SN-R{N}-AX3000"; RSSH = 13229
HOST = "192.168.132.1"
REPO = r"F:\vps"
OPENSSL3 = REPO + r"\firmware\snr1-openssl3\usr"
MIHOMO_REPO = REPO + r"\firmware\openclash"
AGENT_DIR = REPO + r"\mgmt\agent"
UNIFIED_PW = "[REDACTED]"
PW = UNIFIED_PW
VPS_SSH = ['ssh','-p','28722','-i','C:/Users/Administrator/.ssh/id_rsa_dmit','-o','IdentitiesOnly=yes','root@179.255.137.64']

def ssh(pw):
    c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=pw, timeout=10, allow_agent=False, look_for_keys=False)
    return c

def push(c, local, remote, mode=None):
    data = open(local, "rb").read()
    _, o, e = c.exec_command(f"cat > '{remote}'", timeout=300)
    o.channel.sendall(data); o.channel.shutdown_write(); o.read()
    if mode: c.exec_command(f"chmod {mode} '{remote}'")[1].read()

def run(c, script, timeout=240):
    _, o, e = c.exec_command(script, timeout=timeout)
    return (o.read()+e.read()).decode(errors="replace")

def wait_final(tid, timeout=240):
    t0 = time.time()
    while time.time() - t0 < timeout:
        rr = requests.get(M.BASE + "/api/v1/tasks?size=30", headers=H, verify=False, timeout=15).json()
        t = next((x for x in (rr.get("items") or []) if x["id"] == tid), None)
        if t and t["status"] in ("ok","failed","rolled_back","expired","timeout"): return t
        time.sleep(10)
    return None

print(f"[{time.strftime('%H:%M:%S')}] 续跑 {NAME} (RSSH {RSSH}) 从步骤4开始")

# 4) 管理台录入
M.ensure_tunnel()
H = {"Authorization": "Bearer " + M.token()}
r = requests.post(M.BASE + "/api/v1/devices", headers=H,
                  json={"name": NAME, "hw_model": f"SN-R{N}-AX3000-MT7981"}, verify=False, timeout=15)
dev = r.json()
open(REPO + rf"\backups\.snr{N}-enroll.json", "w").write(json.dumps(dev, ensure_ascii=False))
r = requests.post(M.BASE + "/api/v1/devices/enroll", json={"enroll_code": dev["enroll_code"]}, verify=False, timeout=15)
pkg = r.json(); pkg["mgmt_url"] = "https://127.0.0.1:18443"
open(REPO + rf"\backups\pkg-snr{N}.json", "w").write(json.dumps(pkg, ensure_ascii=False, indent=1))
os.chmod(REPO + rf"\backups\pkg-snr{N}.json", 0o600)
DEVID = dev["id"]
print(f"  管理台录入 {DEVID}")

c = ssh(PW)

# 5) agent 文件推送 + install
c.exec_command("mkdir -p /tmp/stage/handlers /tmp/stage/init")[1].read()
files = [
    (AGENT_DIR + r"\routermgmt-agent.sh", "/tmp/stage/routermgmt-agent.sh", 755),
    (AGENT_DIR + r"\handlers\switch_node.sh", "/tmp/stage/handlers/switch_node.sh", 644),
    (AGENT_DIR + r"\access-fw.sh", "/tmp/stage/access-fw.sh", 755),
    (AGENT_DIR + r"\netprobe.sh", "/tmp/stage/netprobe.sh", 755),
    (AGENT_DIR + r"\init\routermgmt", "/tmp/stage/init/routermgmt", 755),
    (AGENT_DIR + r"\init\mgmt-tunnel-v3", "/tmp/stage/init/mgmt-tunnel-v3", 755),
    (AGENT_DIR + r"\mgmt-port-discovery.sh", "/tmp/stage/mgmt-port-discovery.sh", 755),
    (AGENT_DIR + r"\wan-watch.sh", "/tmp/stage/wan-watch.sh", 755),
    (AGENT_DIR + r"\init\wan-watch", "/tmp/stage/init/wan-watch", 755),
    (AGENT_DIR + r"\wan-watch.hotplug", "/tmp/stage/wan-watch.hotplug", 755),
    (AGENT_DIR + r"\setup-mgmt-tunnel.sh", "/tmp/stage/setup-mgmt-tunnel.sh", 755),
    (AGENT_DIR + r"\tunnel.conf.example", "/tmp/stage/tunnel.conf.example", 600),
    (AGENT_DIR + r"\install-agent.sh", "/tmp/stage/install-agent.sh", 755),
    (REPO + r"\scripts\snr1-mihomo-ipt.sh", "/tmp/stage/mihomo-ipt.sh", 755),
    (AGENT_DIR + r"\connlog.sh", "/tmp/stage/connlog.sh", 755),
    (AGENT_DIR + r"\onboot-hooks.sh", "/tmp/stage/onboot-hooks.sh", 755),
]
for src, dst, m in files:
    push(c, src, dst, m)
_, o, e = c.exec_command("cat > /tmp/stage/pkg.json && chmod 600 /tmp/stage/pkg.json", timeout=30)
o.channel.sendall(json.dumps(pkg).encode()); o.channel.shutdown_write(); o.read()
run(c, "cd /tmp/stage && sh install-agent.sh /tmp/stage/pkg.json 2>&1 | grep -vE 'install: not found' | tail -3")
wan_mac = f"76:a7:c8:e0:4f:{0x20 + N:02x}"
brlan_mac = f"76:a7:c8:e0:4f:{0x30 + N:02x}"
out = run(c, r'''
R=/etc/routermgmt; S=/tmp/stage
cp $S/routermgmt-agent.sh /usr/bin/routermgmt-agent && chmod 755 /usr/bin/routermgmt-agent
cp $S/routermgmt-agent.sh $R/routermgmt-agent.sh && chmod 700 $R/routermgmt-agent.sh
cp $S/handlers/switch_node.sh $R/handlers/switch_node.sh && chmod 600 $R/handlers/switch_node.sh
cp $S/access-fw.sh $R/access-fw.sh && chmod 755 $R/access-fw.sh
cp $S/netprobe.sh $R/netprobe.sh && chmod 755 $R/netprobe.sh
cp $S/init/routermgmt /etc/init.d/routermgmt && chmod 755 /etc/init.d/routermgmt
cp $S/wan-watch.sh /usr/sbin/wan-watch.sh && chmod 755 /usr/sbin/wan-watch.sh
mkdir -p /etc/hotplug.d/iface $R/logs
cp $S/init/wan-watch /etc/init.d/wan-watch && chmod 755 /etc/init.d/wan-watch
cp $S/wan-watch.hotplug /etc/hotplug.d/iface/98-wan-watch && chmod 755 /etc/hotplug.d/iface/98-wan-watch
/etc/init.d/wan-watch enable
cp $S/init/mgmt-tunnel-v3 /etc/init.d/mgmt-tunnel && chmod 755 /etc/init.d/mgmt-tunnel
cp $S/mgmt-port-discovery.sh $R/mgmt-port-discovery.sh && chmod 755 $R/mgmt-port-discovery.sh
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
sh -n $R/routermgmt-agent.sh || { echo AGENT_PATCH_SYNTAX_FAIL; exit 1; }
cp $R/routermgmt-agent.sh /usr/bin/routermgmt-agent && chmod 755 /usr/bin/routermgmt-agent
grep -q mgmt-port-discovery /etc/crontabs/root 2>/dev/null || echo '* * * * * /etc/routermgmt/mgmt-port-discovery.sh #mgmt-port' >> /etc/crontabs/root
cp $S/setup-mgmt-tunnel.sh /usr/sbin/setup-mgmt-tunnel.sh && chmod 755 /usr/sbin/setup-mgmt-tunnel.sh
cp $S/tunnel.conf.example $R/tunnel.conf.example && chmod 600 $R/tunnel.conf.example
cp $S/mihomo-ipt.sh /etc/mihomo-ipt.sh && chmod 755 /etc/mihomo-ipt.sh
mkdir -p /etc/crontabs
cp $S/connlog.sh $R/connlog.sh && chmod 755 $R/connlog.sh
cp $S/onboot-hooks.sh $R/onboot-hooks.sh && chmod 755 $R/onboot-hooks.sh
sh -n $R/onboot-hooks.sh || { echo ONBOOTHOOKS_SYNTAX_FAIL; exit 1; }
grep -q routermgmt-netprobe /etc/crontabs/root 2>/dev/null || echo '*/5 * * * * /etc/routermgmt/netprobe.sh #routermgmt-netprobe' >> /etc/crontabs/root
grep -q routermgmt-connlog /etc/crontabs/root 2>/dev/null || echo '*/5 * * * * /etc/routermgmt/connlog.sh #routermgmt-connlog' >> /etc/crontabs/root
grep -q routermgmt-reboot /etc/crontabs/root 2>/dev/null || echo '0 0 * * * /sbin/reboot #routermgmt-reboot' >> /etc/crontabs/root
grep -q onboot-hooks /etc/rc.local || sed -i 's|^exit 0|/etc/routermgmt/onboot-hooks.sh\nexit 0|' /etc/rc.local
sed -i 's/respawn 3600 [0-9]* [0-9]*/respawn 3600 0 0/' /etc/init.d/routermgmt
iptables -D FORWARD -i br-lan -p udp -m udp --dport 443 -j REJECT --reject-with icmp-port-unreachable 2>/dev/null
iptables -I FORWARD 1 -i br-lan -p udp -m udp --dport 443 -j REJECT --reject-with icmp-port-unreachable
# 2026-10-09 增补：MAC 固定（出厂未烧录有线 MAC 场景；本机实测有厂 MAC，固定为舰队段统一管理）
cp -a /etc/config/network /root/network.bak-macpin-$(date +%Y%m%d-%H%M%S)
uci set network.wan.macaddr='__WAN_MAC__'
_i=0; while uci -q get network.@device[$_i] >/dev/null 2>&1; do
  if [ "$(uci -q get network.@device[$_i].name)" = br-lan ]; then
    uci set network.@device[$_i].macaddr='__BRLAN_MAC__'
  fi
  _i=$((_i+1))
done
uci commit network
echo MAC_PINNED=$(uci -q get network.wan.macaddr)
crontab -l 2>/dev/null | grep -q routermgmt-netprobe || { echo "NETPROBE_CRON_FAIL"; exit 1; }
/etc/routermgmt/netprobe.sh >/dev/null 2>&1; sleep 1
[ -s /etc/routermgmt/logs/netprobe.log ] && tail -n 2 /etc/routermgmt/logs/netprobe.log || { echo "NETPROBE_RUN_FAIL"; exit 1; }
/etc/init.d/cron restart >/dev/null 2>&1
uci -q delete network.wg0; uci -q commit network
mkdir -p /etc/mihomo
sh -n /usr/bin/routermgmt-agent && echo SYNTAX_OK
/etc/init.d/routermgmt enable && /etc/init.d/routermgmt restart >/dev/null 2>&1; sleep 3
pgrep -f routermgmt-agent >/dev/null && echo agent_running || echo agent_DEAD
'''.replace("__WAN_MAC__", wan_mac).replace("__BRLAN_MAC__", brlan_mac))
if "agent_running" not in out:
    print("  agent 启动失败:", out[-300:]); sys.exit(1)
print("  agent 已运行", [l for l in out.splitlines() if "MAC_PINNED" in l])

# 6) 隧道 + pin + 授权（DMIT 中心化分发：DMIT/TX/BWG）
out = run(c, "sh /usr/sbin/setup-mgmt-tunnel.sh 2>&1 | tail -4; dropbearkey -y -f /etc/routermgmt/id_dropbear_mgmt 2>/dev/null | grep ssh-rsa")
m = re.search(r"ssh-rsa [A-Za-z0-9+/=]+", out)
if m:
    line = (f'no-agent-forwarding,no-X11-forwarding,no-pty,'
            f'permitopen="10.10.0.1:8443",permitopen="127.0.0.1:8443",'
            f'permitlisten="127.0.0.1:{RSSH}" ' + m.group(0) + f" rtr-mgmt-tunnel-snR{N}")
    line_tb = (f'no-agent-forwarding,no-X11-forwarding,no-pty,'
               f'permitopen="10.10.0.1:8443",permitopen="127.0.0.1:8443" '
               + m.group(0) + f" rtr-mgmt-tunnel-snR{N}")
    open(REPO + rf"\backups\.snr{N}-ak-line.txt", "w", newline="\n").write(line + "\n" + line_tb + "\n")
    subprocess.run(['scp','-P','28722','-i','C:/Users/Administrator/.ssh/id_rsa_dmit','-o','IdentitiesOnly=yes',
        REPO + rf"\backups\.snr{N}-ak-line.txt",'root@179.255.137.64:/tmp/snr-ak'], capture_output=True)
    subprocess.run(VPS_SSH + [
        f'grep -q "snR{N}" /root/.ssh/authorized_keys || grep "snR{N}" /tmp/snr-ak | grep permitlisten >> /root/.ssh/authorized_keys; rm -f /tmp/snr-ak'], capture_output=True)
    tb_sh = (
        "set -e; printf '%s\\n' '" + line_tb + "' > /tmp/snr-ak-tb; "
        "K=/root/.ssh/id_ed25519_txbackup; O='-i $K -o BatchMode=yes -o StrictHostKeyChecking=no -o ConnectTimeout=10'; "
        "scp $O /tmp/snr-ak-tb ubuntu@43.134.46.44:/tmp/ >/dev/null 2>&1; "
        f"ssh $O ubuntu@43.134.46.44 'sudo grep -q \"snR{N}\" /root/.ssh/authorized_keys 2>/dev/null || cat /tmp/snr-ak-tb | sudo tee -a /root/.ssh/authorized_keys >/dev/null; rm -f /tmp/snr-ak-tb'; "
        "scp $O /tmp/snr-ak-tb root@74.82.203.175:/tmp/ >/dev/null 2>&1; "
        f"ssh $O root@74.82.203.175 'grep -q \"snR{N}\" /root/.ssh/authorized_keys 2>/dev/null || cat /tmp/snr-ak-tb >> /root/.ssh/authorized_keys; chmod 600 /root/.ssh/authorized_keys; rm -f /tmp/snr-ak-tb'; "
        "rm -f /tmp/snr-ak-tb; echo TB-AK-DONE"
    )
    r = subprocess.run(VPS_SSH + [tb_sh], capture_output=True, text=True, timeout=120)
    print("  TX/BWG 公钥分发(经DMIT):", "OK" if "TB-AK-DONE" in r.stdout else f"失败 {r.stdout[-80:]} {r.stderr[-80:]}")
run(c, f"""cat > /etc/routermgmt/tunnel.conf <<'CONF_EOF'
# mgmt-tunnel v3 配置 — flash-snr 最终形态（2026-10-02，管理面 DMIT 主台）
VPS_HOST=179.255.137.64
VPS_PORT=28722
MGMT_LOCAL_PORT=18443
MGMT_TARGET=127.0.0.1:8443
RSSH_REMOTE_PORT={RSSH}
RSSH_TARGET=192.168.132.1:22

# 备用端点 2（TX sshd 直连，本地 :18444）
VPS2_HOST=43.134.46.44
VPS2_PORT=28722

# 备用端点 3（BWG sshd 直连，本地 :18445）
VPS3_HOST=74.82.203.175
VPS3_PORT=28722

# 备用端点 4（DO 兜底，本地 :18446）
VPS4_HOST=159.223.36.119
VPS4_PORT=28722
CONF_EOF
chmod 600 /etc/routermgmt/tunnel.conf""")
run(c, "sh /usr/sbin/setup-mgmt-tunnel.sh pin-hostkey SHA256:kigD/OWVZ0B337cxRckXDeQDGB6EddSqd4fAOoW7yvU 2>&1 | tail -1")
out = run(c, f"/etc/init.d/mgmt-tunnel enable && /etc/init.d/mgmt-tunnel restart >/dev/null 2>&1; sleep 10; curl -sk -m 8 -o /dev/null -w 'health=%{{http_code}}\\n' https://127.0.0.1:18443/api/v1/health; echo instances=$(ps w | grep 1844 | grep -v grep | wc -l)")
print("  隧道:", out.strip())

# 7) mihomo（仓库直推）
print("  推 mihomo 57MB...", end=" ", flush=True)
push(c, MIHOMO_REPO + r"\mihomo-linux-arm64", "/usr/bin/mihomo", 755)
push(c, MIHOMO_REPO + r"\geosite.dat", "/etc/mihomo/GeoSite.dat", 644)
push(c, MIHOMO_REPO + r"\country.mmdb", "/etc/mihomo/Country.mmdb", 644)
print("完成")
cfg = open(REPO + r"\scripts\clash-config-router.yaml", encoding="utf-8").read()
m = re.search(r"proxies:\n(.*?)\nproxy-groups:", cfg, re.S)
nodes_yaml = m.group(1) if m else ""
m2 = re.search(r'name: "AUTO"\n.*?proxies:\n(.*?)\n', cfg, re.S)
members = "\n".join("      " + l.strip() for l in m2.group(1).strip().splitlines() if l.strip().startswith("- ")) if m2 else ""
try:
    import yaml
    d = yaml.safe_load(cfg)
    direct_rules = "\n".join(f'  - IP-CIDR,{p["server"]}/32,DIRECT,no-resolve' for p in d.get("proxies", []))
except Exception:
    direct_rules = ""
golden = open(REPO + r"\mgmt\backend\internal\clash\golden.yaml", encoding="utf-8").read()
golden = golden.replace("#{{NODE_PROXIES}}", nodes_yaml.rstrip())
golden = golden.replace("#{{GROUP_PROXY_MEMBERS}}", members).replace("#{{GROUP_AUTO_MEMBERS}}", members)
golden = golden.replace("#{{NODE_DIRECT_RULES}}", direct_rules)
_, o, e = c.exec_command("cat > /etc/mihomo/config.yaml && chmod 644 /etc/mihomo/config.yaml", timeout=60)
o.channel.sendall(golden.encode()); o.channel.shutdown_write(); o.read()
out = run(c, r'''
cat > /etc/init.d/mihomo <<'EOS'
#!/bin/sh /etc/rc.common
USE_PROCD=1
START=99
STOP=15
start_service() {
    procd_open_instance
    procd_set_param command /usr/bin/mihomo -d /etc/mihomo -f /etc/mihomo/config.yaml
    procd_set_param respawn 3600 5 0
    procd_set_param stdout 1
    procd_set_param stderr 1
    procd_set_param pidfile /var/run/mihomo.pid
    procd_close_instance
    ( i=0; while [ $i -lt 60 ]; do wget -T 2 -q -O /dev/null http://127.0.0.1:9090/version 2>/dev/null && break; sleep 1; i=$((i+1)); done; /etc/mihomo-ipt.sh ) &
}
reload_service() { stop; start; }
EOS
chmod 755 /etc/init.d/mihomo
/etc/init.d/mihomo enable
/etc/init.d/mihomo start
i=0; while [ $i -lt 25 ]; do curl -s -m 2 http://127.0.0.1:9090/version >/dev/null 2>&1 && break; sleep 2; i=$((i+1)); done
curl -s -m 3 http://127.0.0.1:9090/version
curl -s -m 12 -x http://127.0.0.1:7890 https://www.cloudflare.com/cdn-cgi/trace 2>/dev/null | awk -F= '/^ip=/{print " egress=" $2}'
/etc/mihomo-ipt.sh 2>&1 | tail -1
''')
print(f"  mihomo: {out.splitlines()[-3:]}")
c.close()

# 8) 发布双节点
for _n, _p in ((5, False), (3, False), (2, False), (4, True)):
    r = requests.post(M.BASE + "/api/v1/assignments/batch", headers=H,
                      json={"device_ids": [DEVID], "node_id": _n, "is_primary": _p,
                            "apply": _p}, verify=False, timeout=30)
tid = r.json()["items"][0]["task_id"]
_fpq = (f"UPDATE node_assignments SET failover_priority=0 WHERE device_id='{DEVID}' AND node_id=5; "
        f"UPDATE node_assignments SET failover_priority=1 WHERE device_id='{DEVID}' AND node_id=3; "
        f"UPDATE node_assignments SET failover_priority=2 WHERE device_id='{DEVID}' AND node_id=2")
_sqlcmd = f'docker exec routermgmt-db-1 sh -c "psql -U $POSTGRES_USER -d routermgmt -c \\"{_fpq}\\""'
subprocess.run(VPS_SSH + [_sqlcmd], capture_output=True, timeout=60)
requests.patch(M.BASE + f"/api/v1/devices/{DEVID}", headers=H,
               json={"traffic_alert_hourly_mb": 2048, "reboot_cron": "0 0 * * *"}, verify=False, timeout=15)
t = wait_final(tid)
print(f"  发布 v{t.get('result',{}).get('detail',{}).get('version','?')}: {t['status'] if t else 'TIMEOUT'}")
r = requests.post(M.BASE + f"/api/v1/devices/{DEVID}/ops", headers={**H, "Idempotency-Key": str(time.time())},
                  json={"type": "set_proxy_state", "params": {"enabled": True}}, verify=False, timeout=15)
t = wait_final(r.json()["task_id"], 120)
print(f"  set_proxy_state: {t['status'] if t else 'TIMEOUT'}")

# 9) WiFi 默认关闭 + MAC 采集 + 凭证档案
c = ssh(PW)
out = run(c, r'''
cd /etc/wireless/mediatek
for F in mt7981.dbdc.b0.dat mt7981.dbdc.b1.dat DBDC_card0.dat; do
  [ -f "$F" ] || continue
  sed -i "s/^SSID1=.*/SSID1=__NAME__/" "$F"
  [ "$F" = "DBDC_card0.dat" ] && sed -i "s/^SSID7=.*/SSID7=__NAME__-5G/" "$F"
  [ "$F" = "mt7981.dbdc.b1.dat" ] && sed -i "s/^SSID1=.*/SSID1=__NAME__-5G/" "$F"
  sed -i "s/^\(WPAPSK[0-9]*\)=.*/\1=[REDACTED]/" "$F"
  grep -q "^RadioOff=" "$F" && sed -i "s/^RadioOff=.*/RadioOff=1/" "$F" || sed -i "2i RadioOff=1" "$F"
done
uci set wireless.ra0.key='[REDACTED]'; uci set wireless.ra0.disabled='1'
uci set wireless.rax0.key='[REDACTED]'; uci set wireless.rax0.disabled='1'
uci set wireless.RADIO2G.disabled='1' 2>/dev/null; uci set wireless.RADIO5G.disabled='1' 2>/dev/null
uci commit wireless
sed -i '/# wifi-default-off/,$d' /etc/rc.local
printf '%s\n' \
'# wifi-boot-policy (2026-10-01 v3: wifi.enabled 标记=上次人工开启→开机主动 up' \
'# [带重试: MTK 驱动初始化射频要十几秒, 固定 sleep 不够]; 无标记→down=新设备默认关。' \
'# 旧版两宗罪: 把 wifi.disabled 当已启用读+只 skip-down, 开启一重启就被 RadioOff=1 关回)' \
'(sleep 8; _w=up; [ -f /etc/routermgmt/state/wifi.enabled ] || _w=down' \
' for _i in 1 2 3 4 5 6; do' \
'   ifconfig ra0 $_w 2>/dev/null; ifconfig rax0 $_w 2>/dev/null' \
'   [ "$_w" = "down" ] && break' \
'   ip link show ra0 2>/dev/null | grep -q "state UP" && break' \
'   sleep 5' \
' done) > /dev/null 2>&1 &' \
'# routermgmt-cron-restore (10-01: 厂商开机重写crontab, netprobe+discovery 两条开机重加;' \
'# discovery 条目丢了温备切换就只剩心跳失败触发一条路, 2026-10-01 R2 重启实测丢过)' \
'(sleep 10; grep -q routermgmt-netprobe /etc/crontabs/root 2>/dev/null || echo "*/5 * * * * /etc/routermgmt/netprobe.sh #routermgmt-netprobe" >> /etc/crontabs/root; grep -q mgmt-port-discovery /etc/crontabs/root 2>/dev/null || echo "* * * * * /etc/routermgmt/mgmt-port-discovery.sh #mgmt-port" >> /etc/crontabs/root; /etc/init.d/cron restart) > /dev/null 2>&1 &' \
'exit 0' >> /etc/rc.local
ifconfig ra0 down; ifconfig rax0 down
for I in wan br-lan ra0 rax0; do [ -d /sys/class/net/$I ] && echo "$I: $(cat /sys/class/net/$I/address)"; done
'''.replace("__NAME__", NAME))
c.close()
macs = dict(re.findall(r"(wan|br-lan|ra0|rax0): ([0-9a-f:]+)", out))
doc = f"""# {NAME} ({DEVID}) 凭证档案 — {time.strftime('%Y-%m-%d')}

| 通道 | 值 |
|---|---|
| SSH（LAN） | `ssh root@192.168.132.1` / `{PW}` |
| LuCI | `http://192.168.132.1` root / 同上 |
| 反向 SSH | `ssh -p 28722 -i ~/.ssh/id_rsa_dmit root@179.255.137.64` → `sshpass -p <root口令> ssh -p {RSSH} root@127.0.0.1`（DMIT 主台，2026-10-02 起） |
| WiFi（默认关闭） | SSID `{NAME}`，PSK `[REDACTED]`；开启：管理台「开WiFi」 |
| 管理台 | ID `{DEVID}`，RSSH `{RSSH}` |
| MAC 固定（uci，重启生效） | WAN=`{wan_mac}` br-lan=`{brlan_mac}`（2026-10-09 起标准步骤；出厂实测 MAC：wan={macs.get('wan','?')} ra0={macs.get('ra0','?')}） |

| 接口 | MAC（当前运行值） |
|---|---|
| WAN | `{macs.get('wan','?')}` |
| 2.4G | `{macs.get('ra0','?')}` |
| 5G | `{macs.get('rax0','?')}` |
"""
open(REPO + rf"\backups\SN-R{N}-凭证档案-{time.strftime('%Y%m%d')}.md", "w", encoding="utf-8").write(doc)
note = f"WAN MAC {macs.get('wan','?')} | 2.4G {macs.get('ra0','?')} | 5G {macs.get('rax0','?')} | WiFi默认关闭(密码[REDACTED]) | {time.strftime('%Y-%m-%d')} 刷机录入"
requests.patch(M.BASE + f"/api/v1/devices/{DEVID}", headers=H,
               json={"location_note": note, "mac_addr": macs.get("ra0","")}, verify=False, timeout=15)

# 10) 终验八关
c = ssh(PW)
out = run(c, r'''
echo "L=$(netstat -ltn | grep -cE '1844[3-6]')"
echo "H=$(curl -sk -m 6 -o /dev/null -w '%{http_code}' https://127.0.0.1:18443/api/v1/health)"
echo "E=$(curl -s -m 10 -x http://127.0.0.1:7890 https://api.ipify.org 2>/dev/null)"
echo "G=$(curl -s -o /dev/null -w '%{http_code}' -m 12 -x http://127.0.0.1:7890 https://www.google.com/generate_204)"
B64=$(printf %s __PW__ | openssl base64 -A | sed 's/=/%3D/g')
echo "W=$(curl -s -o /dev/null -w '%{http_code}' -m 8 -d "username=cm9vdA%3D%3D&password=$B64" http://192.168.132.1/cgi-bin/luci)"
echo "C=$(grep -c routermgmt /etc/crontabs/root)"
echo "R=$(grep -c onboot-hooks /etc/rc.local)"
echo "T=$(grep -c 'window.btoa(document' /usr/lib/lua/luci/view/admin_web/login.htm)/$(grep -c 'window.btoa(document' /usr/lib/lua/luci/view/admin_web/login_m.htm)"
'''.replace('__PW__', PW))
kv = dict(re.findall(r"^(\w)=(.*)$", out, re.M))
checks = [
    ("隧道监听>=3", int(kv.get('L', '0')) >= 3),
    ("API健康200", kv.get('H') == '200'),
    ("出口IP非空", len(kv.get('E', '')) >= 8),
    ("google 204", kv.get('G') == '204'),
    ("网页登录302", kv.get('W') == '302'),
    ("crontab>=4条", int(kv.get('C', '0')) >= 4),
    ("rc.local钩子", kv.get('R', '0') == '1'),
    ("btoa编码2/1", kv.get('T') == '2/1'),
]
failed = [n for n, ok in checks if not ok]
for n, ok in checks:
    print(f"    {'PASS' if ok else 'FAIL'} {n}")
print("出口IP:", kv.get('E',''))
c.close()
if failed:
    print(f"  终验未过: {failed}（不出厂，修复后重跑）"); sys.exit(2)
print(f"[{time.strftime('%H:%M:%S')}] ✅ {NAME} 刷机完成，终验 8/8 全过，凭证档案已写入")
