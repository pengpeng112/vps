#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""SN-R1-AX3000 同款固件刷机一键脚本（2026-09-28 固化）
用法：插好新设备（WAN 接雅典娜 LAN 或公司网、PC 网线接它 LAN 口）后运行：
  python flash-snr.py --num 4 --rssh 13224
流程：探测→telnet 登录→改密→预装清理→NTP→openssl3→管理台录入→agent→隧道→mihomo→发布→WiFi→凭证档案
2026-10-14 R11 复查补进：注释 rc.local 行首的 update_docker.sh；dat 改前放开写权限，并让 UCI 主 SSID 与出厂密码槽跟 dat 一致（开机 wifi 会把 UCI 写回 dat）；隧道起来后把本机排障口登记进 DMIT 探针名单。终验首轮与复测共用同一组关卡。
前置：F:\vps\backups\ 下有 .snrN-pw（自动生成）；管理台隧道已建（mgmt_api_test.ensure_tunnel）。
2026-10-29：新设备默认广州主端点，先验证受控凭据/已固定主机身份；旧 DMIT 拓扑须显式选 dmit-legacy。
本轮只完成该新增流程的静态检查与只读前置验证，没有授权重刷现有设备。
"""
import argparse, base64, hashlib, json, os, re, secrets, shlex, socket, string, subprocess, sys, time
import paramiko, requests
sys.path.insert(0, r"F:\vps\scripts")
import mgmt_api_test as M

HOST = "192.168.132.1"
REPO = r"F:\vps"
OPENSSL3 = REPO + r"\firmware\snr1-openssl3\usr"
MIHOMO_REPO = REPO + r"\firmware\openclash"
AGENT_DIR = REPO + r"\mgmt\agent"


def gz_bootstrap(authorization=None):
    """Verify the pinned domestic endpoint; append a new device key without replacing any key."""
    path = REPO + r"\backups\.gz-ops-credentials.json"
    known = REPO + r"\backups\maintenance-20261029\known_hosts"
    if not os.path.isfile(path) or not os.path.isfile(known):
        raise RuntimeError("广州受控凭据/已核验主机指纹缺失，禁止回退旧管理拓扑刷机")
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    gz = paramiko.SSHClient()
    gz.load_host_keys(known)
    gz.set_missing_host_key_policy(paramiko.RejectPolicy())
    gz.connect(cfg["GZ_HOST"], port=28722, username=cfg["GZ_USER"], password=cfg["GZ_PASS"],
               timeout=10, auth_timeout=10, allow_agent=False, look_for_keys=False)
    try:
        hostkey = gz.get_transport().get_remote_server_key()
        fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(hostkey.asbytes()).digest()).decode().rstrip("=")
        if authorization is not None:
            # Refuse a conflicting existing enrollment. Never rewrite authorized_keys.
            code = ("import sys,pathlib; line=sys.stdin.read().strip(); "
                    "p=pathlib.Path('/root/.ssh/authorized_keys'); "
                    "old=p.read_text() if p.exists() else ''; tag=line.rsplit(' ',1)[1]; "
                    "matches=[s for s in old.splitlines() if s.endswith(' '+tag)]; "
                    "assert not matches or matches==[line], 'conflicting enrollment'; "
                    "p.parent.mkdir(mode=448,exist_ok=True); "
                    "p.open('a').write(('' if not old or old.endswith('\\n') else '\\n')+line+'\\n') if not matches else None; "
                    "p.chmod(384); print('GZ_AUTH_VERIFIED')")
            i, o, e = gz.exec_command("sudo python3 -c " + shlex.quote(code), timeout=15)
            i.write(authorization + "\n"); i.flush(); i.channel.shutdown_write()
            result = o.read().decode()
            if o.channel.recv_exit_status() or "GZ_AUTH_VERIFIED" not in result:
                raise RuntimeError("广州设备公钥分发未通过，禁止继续切换管理主端点")
        return cfg["GZ_HOST"], fingerprint
    finally:
        gz.close()

# SN-R 系列统一 root 口令（2026-09-28 用户定稿；WiFi [REDACTED] 同为统一策略）
UNIFIED_PW = "[REDACTED]"

def md5_crypt(pw, salt):
    """glibc 兼容 $1$ MD5-crypt（纯 python，Windows 无 crypt 模块；openssl 双向量自校验）。"""
    import hashlib
    ITOA = "./0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    pwB = pw.encode("utf-8"); saltB = salt.encode("utf-8")
    alt = hashlib.md5(pwB + saltB + pwB).digest()
    ctx = hashlib.md5()
    ctx.update(pwB); ctx.update(b"$1$"); ctx.update(saltB)
    cnt = len(pwB)
    while cnt > 0:
        ctx.update(alt[:min(16, cnt)]); cnt -= 16
    cnt = len(pwB)
    while cnt > 0:
        ctx.update(b"\x00" if (cnt & 1) else pwB[:1])
        cnt >>= 1
    final = ctx.digest()
    for i in range(1000):
        c = hashlib.md5()
        if i % 2: c.update(pwB)
        else:     c.update(final)
        if i % 3: c.update(saltB)
        if i % 7: c.update(pwB)
        if i % 2: c.update(final)
        else:     c.update(pwB)
        final = c.digest()
    def to64(v, n):
        r = ""
        while n > 0:
            r += ITOA[v & 0x3f]; v >>= 6; n -= 1
        return r
    def grp(a, b, c):
        return to64((final[a] << 16) | (final[b] << 8) | final[c], 4)
    return "$1$" + salt + "$" + grp(0,6,12)+grp(1,7,13)+grp(2,8,14)+grp(3,9,15)+grp(4,10,5)+to64(final[11],2)

def gen_creds(tag):
    r = subprocess.run(["openssl", "passwd", "-1", "-salt", "snruni", UNIFIED_PW],
                       capture_output=True, text=True)
    return UNIFIED_PW, r.stdout.strip()

# ---- RSSH 端口自动分配（2026-09-30）----
# 规则：同设备（snR{N}）复刷→复用 authorized_keys 已分配端口（幂等）；
#       新设备→取"已分配端口与当前监听"的最大值+1（起点 13223，越界 13300 即停）。
VPS_SSH = ['ssh','-p','28722','-i','C:/Users/Administrator/.ssh/id_rsa_dmit','-o','IdentitiesOnly=yes','root@179.255.137.64']  # 2026-10-02 端口表随公钥迁 DMIT

def alloc_rssh(n):
    r = subprocess.run(VPS_SSH + ["grep -E 'rtr-mgmt-tunnel-snR%d$' /root/.ssh/authorized_keys 2>/dev/null | grep -oE 'permitlisten=\"127.0.0.1:[0-9]+\"' | grep -oE '[0-9]+\"' | tr -d '\"' | head -1" % n],
                       capture_output=True, text=True, timeout=30)
    mine = r.stdout.strip()
    if mine:
        print(f"  [rssh] snR{n} 已分配过端口 {mine}，复用（幂等复刷）")
        return int(mine)
    r = subprocess.run(VPS_SSH + ["(grep -oE 'permitlisten=\"127.0.0.1:[0-9]+\"' /root/.ssh/authorized_keys 2>/dev/null | grep -oE ':[0-9]+\"' | tr -d ':\"'; ss -tln 2>/dev/null | grep -oE '127.0.0.1:132[0-9][0-9]' | cut -d: -f2) | grep -E '^132[0-9]{2}$' | sort -n | tail -1"],
                       capture_output=True, text=True, timeout=30)
    try:
        mx = int(r.stdout.strip())
    except ValueError:
        mx = 13222
    nxt = max(mx + 1, 13223)
    if nxt >= 13300:
        sys.exit(f"  [rssh] 端口池将越界（{nxt}>=13300），人工核对 authorized_keys")
    busy = subprocess.run(VPS_SSH + [f'ss -tln | grep -q "127.0.0.1:{nxt} " && echo BUSY || echo FREE'],
                          capture_output=True, text=True, timeout=30).stdout.strip()
    if busy == 'BUSY':
        sys.exit(f"  [rssh] 端口 {nxt} 已被监听占用，人工核查")
    print(f"  [rssh] 自动分配新端口 {nxt}")
    return nxt

class Tel:
    def __init__(self):
        self.s = socket.socket(); self.s.settimeout(5)
        self.s.connect((HOST, 23)); self.buf = b""
        time.sleep(1.2)
        try: self.s.recv(4096)
        except: pass
    def send(self, line):
        self.s.sendall(line.encode()+b"\n"); time.sleep(0.4)
    def until(self, pat, timeout=6):
        t0 = time.time(); acc = b""
        while time.time()-t0 < timeout:
            try:
                d = self.s.recv(4096)
                if d: self.buf += d; acc += d
            except socket.timeout: pass
            if re.search(pat, (self.buf+acc).decode(errors="replace")): return acc.decode(errors="replace")
        return acc.decode(errors="replace")

def ssh(pw):
    c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=pw, timeout=10, allow_agent=False, look_for_keys=False)
    return c

def push(c, local, remote, mode=None):
    # multi-verify V4 修复：推送后 md5 对拍（SOP 自家标准；R7 实测通道写可被截断）
    data = open(local, "rb").read()
    want = hashlib.md5(data).hexdigest()
    _, o, e = c.exec_command(f"cat > '{remote}' && md5sum '{remote}'", timeout=600)
    o.channel.sendall(data); o.channel.shutdown_write()
    out = o.read().decode(errors="replace")
    got = out.split()[0] if out.split() else ""
    if got != want:
        print(f"  ❌ push md5 不符 {remote}: {got[:12]} != {want[:12]}（通道截断）——中止")
        sys.exit(4)
    if mode: c.exec_command(f"chmod {mode} '{remote}'")[1].read()

def run(c, script, timeout=240):
    _, o, e = c.exec_command(script, timeout=timeout)
    return (o.read()+e.read()).decode(errors="replace")

def _try_connect(addr, timeout=2):
    try:
        s = socket.create_connection(addr, timeout); s.close(); return True
    except OSError:
        return False

def main():
    global HOST
    ap = argparse.ArgumentParser()
    ap.add_argument("--num", required=True, type=int, help="设备序号（如 4 → SN-R4-AX3000）——multi-verify V1：type=int 修复字符串算术崩溃")
    ap.add_argument("--rssh", default="auto", help="反向 SSH 端口；默认 auto=自动分配（同设备复刷复用原端口）")
    ap.add_argument("--random-pw", action="store_true", help="口令用随机值（默认=舰队统一值，2026-10-08 起）")
    ap.add_argument("--host", default="192.168.132.1", help="设备地址（2026-10-13 批量并行刷机增补；可在任意网段）")
    ap.add_argument("--management-profile", choices=["gz", "dmit-legacy"], default="gz",
                    help="新设备默认广州主端点；dmit-legacy 仅用于明确的历史拓扑回退")
    args = ap.parse_args()
    if args.management_profile == "gz":
        mgmt_host, mgmt_fingerprint = gz_bootstrap()
    else:
        mgmt_host = "179.255.137.64"
        mgmt_fingerprint = "SHA256:kigD/OWVZ0B337cxRckXDeQDGB6EddSqd4fAOoW7yvU"
    N = args.num; NAME = f"SN-R{N}-AX3000"
    HOST = args.host
    RSSH = int(args.rssh) if str(args.rssh).isdigit() else alloc_rssh(int(N))
    print(f"[{time.strftime('%H:%M:%S')}] 开始刷机 {NAME} @ {HOST} (RSSH {RSSH})")

    # 0) 探测
    print(f"  可达: telnet={_try_connect((HOST,23))} ssh={_try_connect((HOST,22))}")
    # D5 后端身份预检前移（触碰设备之前；失败=设备保持出厂态可重刷）
    M.ensure_tunnel()
    _H0 = {"Authorization": "Bearer " + M.token()}
    _pf0 = requests.get(M.BASE + "/api/v1/devices/rtr-5e1a8d", headers=_H0, verify=False, timeout=10)
    if _pf0.status_code != 200:
        print(f"  后端身份预检失败（HTTP {_pf0.status_code}）：MGMT_API_BASE 非 DMIT——设备未做任何改动，中止")
        sys.exit(3)
    print("  后端身份预检通过（DMIT，零改动阶段）")
    t = Tel(); t.until(r"login:"); t.send("root"); t.until(r"assword:")
    t.send("abc@1234"); r = t.until(r"#", 5)
    if "#" not in r:
        print("telnet 登录失败"); sys.exit(1)
    print("  telnet 登录成功")

    # 1) 改密（2026-10-08：默认直接舰队统一值，消除"先随机后统一"真空期）
    if args.random_pw:
        PW, HASH = gen_creds(f"sn{N}")
    else:
        PW = UNIFIED_PW
        HASH = md5_crypt(PW, f"snr{N}uni")
    open(REPO + rf"\backups\.snr{N}-pw", "w").write(PW + "\n" + HASH + "\n"); os.chmod(os.path.join(REPO, "backups", f".snr{N}-pw"), 0o600)
    t.send(f"sed -i 's|^root:[^:]*:|root:{HASH}:|' /etc/shadow")
    t.until(r"#", 5); t.s.close()
    c = ssh(PW)
    print(f"  SSH 新口令通过 ({PW[:6]}...)")
    # 2026-10-23 增补（八）：早期批次固件（R1-R6 同源）网页登录 htmlauth 查的是 uci
    # system.@system[0].password 而非 shadow——不设此字段 LuCI 永远"密码错误"且反复输触发
    # 登录锁定（R6 实战定案）。新批次（R8+）查 shadow（关卡 W=302 已实测通过），本步对其无害。
    run(c, "uci set system.@system[0].password='" + PW + "'; uci set system.@system[0].logincount=0; "
           "uci set system.@system[0].login_time=0; uci commit system")

    # 2) 预装清理 + NTP
    out = run(c, r'''
for S in dockerd telnetd ksmbd miniupnpd start_autoupdate mtk_dut wsdd2; do
  /etc/init.d/$S stop >/dev/null 2>&1; /etc/init.d/$S disable >/dev/null 2>&1
done
# 2026-10-13 R7 彻查增补：厂商 PCDN/云上报/打洞/穿透/定时重启九件套（"听雨"收益程序全家）
for S in netbox_daemon.service zcon-edge-service zcon-ng start_client_manage start_get_versionInfo timeRestart mbox hole start_jcgqos; do
  /etc/init.d/$S stop >/dev/null 2>&1; /etc/init.d/$S disable >/dev/null 2>&1
done
killall watcher by_agent_pro net_qua_cli inst_width tcp_stun_ser netbox_daemon zcon-ng zcon-edge-arm64 rathole 2>/dev/null
sleep 2
killall -9 watcher by_agent_pro net_qua_cli inst_width tcp_stun_ser netbox_daemon zcon-ng zcon-edge-arm64 rathole 2>/dev/null
# 2026-10-13 深层封堵（R7 彻查二轮+审计三轮）：安装器本体+rc.local 重装钩子+数据盘+fstab 隔离
# 修正：install_mbox.sh 实际位于 {install_mbox.sh} 目录（rm /usr/bin 是错路径）；mbox_init.sh/mboxinit.sh 残留；init.d 脚本本体需删
killall -9 install_mbox.sh install_pdnterminal.sh 2>/dev/null
rm -rf '/usr/bin/{install_mbox.sh}' '/usr/bin/{install_pdnterminal.sh}' 2>/dev/null
rm -f /usr/bin/install_mbox.sh /usr/bin/install_pdnterminal.sh /usr/bin/mbox_init.sh /usr/bin/mboxinit.sh 2>/dev/null
rm -f /etc/init.d/netbox_daemon.service /etc/init.d/zcon-edge-service /etc/init.d/zcon-ng /etc/init.d/mbox /etc/init.d/hole /etc/init.d/start_client_manage /etc/init.d/start_get_versionInfo /etc/init.d/start_jcgqos /etc/init.d/timeRestart 2>/dev/null
# D6 R7 终态回灌（2026-10-14）：不合规二进制+厂商云端点+checkMem（与 R7 授权清理同版）
rm -f /usr/bin/rathole /usr/bin/zcon-ng /usr/bin/zcon-edge-arm64 2>/dev/null
rm -f /www/cgi-bin/cgi-exec /www/cgi-bin/cgi-upload /www/cgi-bin/cgi-download /www/cgi-bin/cgi-backup 2>/dev/null
sed -i '/factory checkMem/d' /etc/crontabs/root 2>/dev/null
cp -a /etc/rc.local /root/rc.local.bak-pcdn-$(date +%Y%m%d) 2>/dev/null
sed -i '/install_mbox/d; /install_pdnterminal/d; /mbox_init/d' /etc/rc.local
# 2026-10-14 R11：未注释的 update_docker.sh 在数据盘没挂上时会 mkfs.ext4 整分区，
# 把已经抹掉签名的 mmcblk0p6 重新格式化。只给行首的调用加注释，已注释行不动。
sed -i 's|^/usr/sbin/update_docker.sh|# disabled-pcdn: /usr/sbin/update_docker.sh|' /etc/rc.local
rm -rf /mnt/mmcblk0p6/by-agent /mnt/mmcblk0p6/by-daemon /mnt/mmcblk0p6/by-watcher /mnt/mmcblk0p6/initmindevice.sh /mnt/mmcblk0p6/docker /mnt/mmcblk0p6/data1 2>/dev/null
sync
# 2026-10-13 重启实战纠正×3（R7 五次迭代定稿）：uci fstab 三层全关也挡不住 hotplug.d/block/10-mount
# （按 UUID/备份超级块挂载，绕过 uci）。唯一根治=抹掉文件系统签名（blkid 不识别则永不自动挂载）。
uci set fstab.@mount[0].enabled='0' 2>/dev/null
uci set fstab.@global[0].anon_mount='0' 2>/dev/null
uci set fstab.@global[0].auto_mount='0' 2>/dev/null
uci commit fstab 2>/dev/null
umount /mnt/mmcblk0p6/docker 2>/dev/null; umount /mnt/mmcblk0p6 2>/dev/null || umount -l /mnt/mmcblk0p6 2>/dev/null
# V7：卸载确认成功才抹（写挂载中的块设备会白做且可能 IO 错）；768MB 覆盖 4K 块设备的全部标准备份超级块
if ! mount | grep -q mmcblk0p6; then dd if=/dev/zero of=/dev/mmcblk0p6 bs=1M count=768 2>/dev/null; sync; else echo "WARN: mmcblk0p6 仍挂载，跳过抹签名（人工处理）"; fi
# done 的 telnet 行去重防膨胀（复刷幂等）
awk '!/^# disabled-pcdn-telnet:/ || !seen[/^# disabled-pcdn-telnet: / $2]++' /etc/init.d/done > /tmp/done.new && mv /tmp/done.new /etc/init.d/done
sed -i 's|^\(.*telnet.*\)$|# disabled-pcdn-telnet: \1|' /etc/init.d/done 2>/dev/null
for P in dockerd telnetd ksmbd ksmbd.mounted miniupnpd mtk_dut; do killall $P 2>/dev/null; done
rm -rf /etc/m-box /usr/m-box
# 网页登录三件套（2026-10-08 定稿；旧"置 disabled"是三台网页全坏的根因）
uci set system.@system[0].username=root
uci set system.@system[0].password='__UCIPW__'
uci set system.@system[0].superpass=disabled
uci commit system
D=/usr/lib/lua/luci/view/admin_web
sed -i '/getElementById("password").value="<%/,+1d' $D/login.htm 2>/dev/null
sed -i 's/value="<%=password%>"/value=""/' $D/login_m.htm 2>/dev/null
sed -i '/var admin=window.btoa("<%=password/d' $D/login.htm 2>/dev/null
grep -q 'password").value = window.btoa' $D/login.htm || sed -i 's|\tdocument.getElementById("formid").submit();|\tdocument.getElementById("username").value = window.btoa(document.getElementById("username").value);\n\tdocument.getElementById("password").value = window.btoa(document.getElementById("password").value);\n\tdocument.getElementById("formid").submit();|' $D/login.htm
grep -q 'password").value = window.btoa' $D/login_m.htm || sed -i 's|document.getElementById("formid").submit();|document.getElementById("password").value = window.btoa(document.getElementById("password").value);\n\tdocument.getElementById("formid").submit();|' $D/login_m.htm
[ -f /etc/firstLogin ] || echo 0 > /etc/firstLogin
echo "web3: btoa=$(grep -c 'window.btoa(document' $D/login.htm)/$(grep -c 'window.btoa(document' $D/login_m.htm) firstLogin=$(cat /etc/firstLogin)"
uci -q delete system.ntp.server 2>/dev/null
uci add_list system.ntp.server='192.168.2.1'
uci add_list system.ntp.server='120.25.115.20'
uci add_list system.ntp.server='203.107.6.112'
uci -q set system.ntp.enable_server='0'
uci commit system
/etc/init.d/sysntpd restart >/dev/null 2>&1
sleep 4
date
'''.replace('__UCIPW__', PW))
    print("  预装清理+NTP 完成")

    # 3) openssl3
    push(c, OPENSSL3 + r"\bin\openssl", "/usr/bin/openssl.new", 755)
    push(c, OPENSSL3 + r"\lib\libcrypto.so.3", "/usr/lib/libcrypto.so.3", 644)
    push(c, OPENSSL3 + r"\lib\libssl.so.3", "/usr/lib/libssl.so.3", 644)
    run(c, "mv -f /usr/bin/openssl.new /usr/bin/openssl && openssl version | head -1")
    print("  openssl3 全局替换完成")

    # 4) 管理台录入
    M.ensure_tunnel()
    H = {"Authorization": "Bearer " + M.token()}
    # multi-verify V2 修复：写前身份预检——BASE 必须是 DMIT（查 DMIT 独有设备 rtr-5e1a8d 雅典娜），
    # 不匹配立即中止且【零写入】（旧闸门在 POST 之后，劫持场景 DO 已被写入）
    _pf = requests.get(M.BASE + "/api/v1/devices/rtr-5e1a8d", headers=H, verify=False, timeout=10)
    if _pf.status_code != 200:
        print(f"  ❌ 后端身份预检失败（HTTP {_pf.status_code}）：MGMT_API_BASE 不是 DMIT——中止，未发生任何写入")
        sys.exit(3)
    print("  后端身份预检通过（DMIT）")
    # D8：录入串行锁（后台 wg_ip 分配无锁，并行 POST 撞唯一约束）+ 5xx 重试
    _lk = os.path.join(REPO, "backups", ".enroll.lock")
    for _ in range(120):
        try:
            _fd = os.open(_lk, os.O_CREAT | os.O_EXCL | os.O_WRONLY); os.close(_fd); break
        except FileExistsError:
            time.sleep(2)
    else:
        print("  录入锁等待超时"); sys.exit(5)
    try:
        for _att in range(3):
            r = requests.post(M.BASE + "/api/v1/devices", headers=H,
                              json={"name": NAME, "hw_model": f"SN-R{N}-AX3000-MT7981"}, verify=False, timeout=15)
            if r.status_code < 500: break
            print(f"  录入 5xx 重试 {_att+1}: {r.status_code}"); time.sleep(5)
    finally:
        try: os.remove(_lk)
        except OSError: pass
    if r.status_code >= 300:
        print(f"  ❌ 录入失败 HTTP {r.status_code}: {r.text[:120]}"); sys.exit(5)
    dev = r.json()
    open(REPO + rf"\backups\.snr{N}-enroll.json", "w").write(json.dumps(dev, ensure_ascii=False))
    # 防劫持二道闸（V2 修复后为兜底）：录入后在 DMIT 库复核记录（写前预检为主闸）
    _sql = "SELECT count(*) FROM devices WHERE id = '" + dev["id"] + "'"
    _chk = subprocess.run(VPS_SSH + ["docker exec routermgmt-db-1 psql -U postgres -d routermgmt -t -c \"" + _sql + "\""],
                          capture_output=True, text=True, timeout=30)
    if _chk.stdout.strip() != "1":
        print(f"  ❌ 录入库复核失败：DMIT 库无 {dev['id']}——中止（设备侧已发生写入，按检查点续跑处理）")
        sys.exit(3)
    print(f"  录入库复核通过（DMIT 确认 {dev['id']}）")
    r = requests.post(M.BASE + "/api/v1/devices/enroll", json={"enroll_code": dev["enroll_code"]}, verify=False, timeout=15)
    if r.status_code >= 300:
        print(f"  ❌ enroll 失败 HTTP {r.status_code}: {r.text[:120]}"); sys.exit(5)
    pkg = r.json(); pkg["mgmt_url"] = "https://127.0.0.1:18443"
    open(REPO + rf"\backups\pkg-snr{N}.json", "w").write(json.dumps(pkg, ensure_ascii=False, indent=1))
    os.chmod(REPO + rf"\backups\pkg-snr{N}.json", 0o600)
    DEVID = dev["id"]
    print(f"  管理台录入 {DEVID}")

    # 5) agent 文件推送 + install + 手工补装
    c.exec_command("mkdir -p /tmp/stage/handlers /tmp/stage/init")[1].read()
    files = [
        (AGENT_DIR + r"\routermgmt-agent.sh", "/tmp/stage/routermgmt-agent.sh", 755),
        (AGENT_DIR + r"\handlers\switch_node.sh", "/tmp/stage/handlers/switch_node.sh", 644),
        (AGENT_DIR + r"\access-fw.sh", "/tmp/stage/access-fw.sh", 755),
        (AGENT_DIR + r"\netprobe.sh", "/tmp/stage/netprobe.sh", 755),
        (AGENT_DIR + r"\init\routermgmt", "/tmp/stage/init/routermgmt", 755),
        # 2026-10-01 温备就绪形态：v2 多端点隧道（TX/BWG sshd 直连）+ 端口自动选路
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
        # 2026-10-24 进标准：双看门狗（隧道防半开挂死[R5 42h 实证]；WAN 默认 log 模式观察）
        (AGENT_DIR + r"\tunnel-watchdog.sh", "/tmp/stage/tunnel-watchdog.sh", 755),
        (AGENT_DIR + r"\wan-watchdog.sh", "/tmp/stage/wan-watchdog.sh", 755),
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
# 2026-10-01 温备就绪形态：v2 init + 端口选路 + agent 补丁 + discovery cron
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
# V15：补丁必须真实打上（awk 锚点漂移会静默落空）
grep -q mgmt_active_port $R/routermgmt-agent.sh || { echo AGENT_PATCH_MISS; exit 1; }
cp $R/routermgmt-agent.sh /usr/bin/routermgmt-agent && chmod 755 /usr/bin/routermgmt-agent
grep -q mgmt-port-discovery /etc/crontabs/root 2>/dev/null || echo '* * * * * /etc/routermgmt/mgmt-port-discovery.sh #mgmt-port' >> /etc/crontabs/root
cp $S/setup-mgmt-tunnel.sh /usr/sbin/setup-mgmt-tunnel.sh && chmod 755 /usr/sbin/setup-mgmt-tunnel.sh
cp $S/tunnel.conf.example $R/tunnel.conf.example && chmod 600 $R/tunnel.conf.example
cp $S/mihomo-ipt.sh /etc/mihomo-ipt.sh && chmod 755 /etc/mihomo-ipt.sh
# 2026-10-28 fw3 竞态根除（R1 实证：开机 fw3 重载冲掉 PREROUTING 的 MIHOMO 跳转=国内通国外断）：
# 挂成防火墙官方 include，fw3 每次重载自动重跑规则脚本；discovery 的 mihomo-guard 为第二道保险
uci add firewall include >/dev/null
uci set firewall.@include[-1].type=script
uci set firewall.@include[-1].path=/etc/mihomo-ipt.sh
uci set firewall.@include[-1].reload=1
uci commit firewall
mkdir -p /etc/crontabs
cp $S/connlog.sh $R/connlog.sh && chmod 755 $R/connlog.sh
cp $S/onboot-hooks.sh $R/onboot-hooks.sh && chmod 755 $R/onboot-hooks.sh
# 2026-10-24 进标准：双看门狗落地（挂靠 discovery 每分钟；wan 默认 log 模式，站点按需置 heal）
cp $S/tunnel-watchdog.sh $R/tunnel-watchdog.sh && chmod 755 $R/tunnel-watchdog.sh
cp $S/wan-watchdog.sh $R/wan-watchdog.sh && chmod 755 $R/wan-watchdog.sh
echo log > $R/wan-watchdog.mode
sh -n $R/tunnel-watchdog.sh || { echo TUNNELWD_SYNTAX_FAIL; exit 1; }
sh -n $R/wan-watchdog.sh || { echo WANWD_SYNTAX_FAIL; exit 1; }
sh -n $R/onboot-hooks.sh || { echo ONBOOTHOOKS_SYNTAX_FAIL; exit 1; }
grep -q routermgmt-netprobe /etc/crontabs/root 2>/dev/null || echo '*/5 * * * * /etc/routermgmt/netprobe.sh #routermgmt-netprobe' >> /etc/crontabs/root
grep -q routermgmt-connlog /etc/crontabs/root 2>/dev/null || echo '*/5 * * * * /etc/routermgmt/connlog.sh #routermgmt-connlog' >> /etc/crontabs/root
grep -q routermgmt-reboot /etc/crontabs/root 2>/dev/null || echo '0 0 * * * /sbin/reboot #routermgmt-reboot' >> /etc/crontabs/root
grep -q onboot-hooks /etc/rc.local || sed -i 's|^exit 0|/etc/routermgmt/onboot-hooks.sh\nexit 0|' /etc/rc.local
sed -i 's/respawn 3600 [0-9]* [0-9]*/respawn 3600 0 0/' /etc/init.d/routermgmt
iptables -D FORWARD -i br-lan -p udp -m udp --dport 443 -j REJECT --reject-with icmp-port-unreachable 2>/dev/null
iptables -I FORWARD 1 -i br-lan -p udp -m udp --dport 443 -j REJECT --reject-with icmp-port-unreachable
# 2026-10-09 增补：出厂未烧录有线 MAC，每次重启随机生成（WAN IP 漂移会破坏光猫侧绑定/映射）。
# uci 固定后下次重启生效；刷机现场不做即时网络重启（会断开刷机会话）。WiFi 口 MAC 天然固定无需处理。
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
# 2026-09-30：安装后强验证（cron 条目存在 + 首轮采样落日志），防 R3/R4 批次漏装复发
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
        print("  agent 启动失败:", out[-200:]); sys.exit(1)
    print("  agent 已运行")

    # 6) 隧道 + pin + 授权（2026-10-01 起：v2 温备就绪形态，公钥分发 DO+TX+BWG 三台）
    out = run(c, "sh /usr/sbin/setup-mgmt-tunnel.sh 2>&1 | tail -4; dropbearkey -y -f /etc/routermgmt/id_dropbear_mgmt 2>/dev/null | grep ssh-rsa")
    m = re.search(r"ssh-rsa [A-Za-z0-9+/=]+", out)
    if not m:
        print("  ❌ 设备公钥提取失败（setup-mgmt-tunnel/dropbearkey 输出异常）——中止，R8 实战该分支静默跳过致 TX/BWG 未分发")
        sys.exit(6)
    if m:
        # DO 条目：双目标 permitopen（重复选项格式，严禁逗号列表）+ permitlisten(-R 排障口)
        line = (f'no-agent-forwarding,no-X11-forwarding,no-pty,'
                f'permitopen="10.10.0.1:8443",permitopen="127.0.0.1:8443",'
                f'permitlisten="127.0.0.1:{RSSH}" ' + m.group(0) + f" rtr-mgmt-tunnel-snR{N}")
        # TX/BWG 条目：仅 -L 备用端点，无 permitlisten
        line_tb = (f'no-agent-forwarding,no-X11-forwarding,no-pty,'
                   f'permitopen="10.10.0.1:8443",permitopen="127.0.0.1:8443" '
                   + m.group(0) + f" rtr-mgmt-tunnel-snR{N}")
        if args.management_profile == "gz":
            gz_bootstrap(line)
            print("  广州管理公钥已校验（未替换现有密钥）")
        open(REPO + rf"\backups\.snr{N}-ak-line.txt", "w", newline="\n").write(line + "\n" + line_tb + "\n")
        # 2026-10-02：分发链 DMIT 中心化（PC 直连 DMIT；DO 不再分发新设备公钥——v3 中 DO
        # 仅兜底 -L，新设备无 DO 公钥时该端点自动不可用，与 R4 现状一致）
        subprocess.run(['scp','-P','28722','-i','C:/Users/Administrator/.ssh/id_rsa_dmit','-o','IdentitiesOnly=yes',
            REPO + rf"\backups\.snr{N}-ak-line.txt",f'root@179.255.137.64:/tmp/snr-ak-{N}'], capture_output=True)
        subprocess.run(['ssh','-p','28722','-i','C:/Users/Administrator/.ssh/id_rsa_dmit','-o','IdentitiesOnly=yes','root@179.255.137.64',
            f'grep -q "snR{N}$" /root/.ssh/authorized_keys || grep "snR{N}$" /tmp/snr-ak-{N} | grep permitlisten >> /root/.ssh/authorized_keys; rm -f /tmp/snr-ak-{N}'], capture_output=True)
        # TX（root 侧经 ubuntu sudo）、BWG（root 直达）、DMIT（root 直达，2026-10-02 起）分发，幂等
        line_dmit = (f'no-agent-forwarding,no-X11-forwarding,no-pty,'
                     f'permitopen="127.0.0.1:8443" '
                     + m.group(0) + f" rtr-mgmt-tunnel-snR{N}")
        # tb_sh 在 DMIT 上执行：DMIT→TX（ubuntu sudo）、DMIT→BWG（root），均用 DMIT 侧 txbackup 密钥
        # 2026-10-14 根治（R8/R9 双实证踩坑）：旧链三缺陷——①scp 两行缺 f 前缀，远端落盘文件名是字面
        # "{N}"，ssh 侧 cat 读的是代入值→读空、authorized_keys 永远收不到；②set -e 遇 scp 抖动整链中止；
        # ③OK 判定只看 TB-AK-DONE 回声，①②全败也照印 OK。现：逐台独立 3 次重试 + grep -c 双向校验，不过即中止。
        # 2026-10-14 multi-review r2-修（codex#25）：grep "snR{N}" 前缀互撞——snR1 会命中 snR10 行，
        # 幂等守卫误跳过/校验虚高。全部加 $ 尾锚（authorized_keys 条目注释即行尾）。
        _ak_tmp = f"/tmp/snr-ak-tb-{N}"
        _lit_tmp = "/tmp/snr-ak-tb-{N}"  # 旧 bug 远端遗留的字面文件名，顺手清理
        tb_sh = (
            f"printf '%s\\n' '{line_tb}' > {_ak_tmp}; "
            'K=/root/.ssh/id_ed25519_txbackup; O="-i $K -o BatchMode=yes -o StrictHostKeyChecking=no -o ConnectTimeout=10"; '
            f'for i in 1 2 3; do scp $O {_ak_tmp} ubuntu@43.134.46.44:/tmp/ >/dev/null 2>&1 && '
            f'ssh $O ubuntu@43.134.46.44 \'sudo grep -q "snR{N}$" /root/.ssh/authorized_keys 2>/dev/null || cat {_ak_tmp} | sudo tee -a /root/.ssh/authorized_keys >/dev/null; rm -f "{_lit_tmp}"\' && break; sleep 5; done; '
            f'for i in 1 2 3; do scp $O {_ak_tmp} root@74.82.193.113:/tmp/ >/dev/null 2>&1 && '
            f'ssh $O root@74.82.193.113 \'grep -q "snR{N}$" /root/.ssh/authorized_keys 2>/dev/null || cat {_ak_tmp} >> /root/.ssh/authorized_keys; chmod 600 /root/.ssh/authorized_keys; rm -f "{_lit_tmp}"\' && break; sleep 5; done; '
            f'TX_N=$(ssh $O ubuntu@43.134.46.44 \'sudo grep -c "snR{N}$" /root/.ssh/authorized_keys\' 2>/dev/null); '
            f'BWG_N=$(ssh $O root@74.82.193.113 \'grep -c "snR{N}$" /root/.ssh/authorized_keys\' 2>/dev/null); '
            # 2026-10-26 节点7（66clouds CN2 GIA，管理第五端点）：-L-only 权限，端口 28722，密钥 id_ed25519_666
            f'K7=/root/.ssh/id_ed25519_666; O7="-i $K7 -o BatchMode=yes -o StrictHostKeyChecking=no -o ConnectTimeout=10"; '
            f'ssh $O7 root@154.36.137.54 \'grep -q "snR{N}$" /root/.ssh/authorized_keys 2>/dev/null || cat {_ak_tmp} >> /root/.ssh/authorized_keys\' 2>/dev/null; '
            f'N7_N=$(ssh $O7 root@154.36.137.54 \'grep -c "snR{N}$" /root/.ssh/authorized_keys\' 2>/dev/null); '
            f"rm -f {_ak_tmp}; echo \"TX=$TX_N BWG=$BWG_N N7=$N7_N\""
        )
        r = subprocess.run(['ssh','-p','28722','-i','C:/Users/Administrator/.ssh/id_rsa_dmit','-o','IdentitiesOnly=yes','root@179.255.137.64', tb_sh],
                           capture_output=True, text=True, timeout=300)
        _tbok = re.search(r"TX=[1-9]\d* BWG=[1-9]\d* N7=[1-9]\d*", r.stdout or "")
        print("  TX/BWG/GIA 公钥分发(经DMIT):", "OK 三向校验通过" if _tbok else f"失败 {r.stdout[-100:]} {r.stderr[-100:]}")
        if not _tbok:
            print("  ❌ TX/BWG/GIA 温备公钥未落地（重试后校验仍不过）——中止，勿带缺口出厂；恢复后续跑")
            sys.exit(7)
    # v3 tunnel.conf：四端点（DMIT 主 → TX/BWG 备 → DO 兜底）+ MGMT_TARGET=127.0.0.1:8443
    run(c, f"""cat > /etc/routermgmt/tunnel.conf <<'CONF_EOF'
# mgmt-tunnel v3 配置 — flash-snr 最终形态（2026-10-02，管理面 DMIT 主台）
VPS_HOST={mgmt_host}
VPS_PORT=28722
MGMT_LOCAL_PORT=18443
MGMT_TARGET=127.0.0.1:8443
RSSH_REMOTE_PORT={RSSH}
RSSH_TARGET=192.168.132.1:22

# 备用端点 2（TX sshd 直连，本地 :18444）
VPS2_HOST=43.134.46.44
VPS2_PORT=28722

# 备用端点 3（BWG sshd 直连，本地 :18445）
VPS3_HOST=74.82.193.113
VPS3_PORT=28722

# 备用端点 4（DO 兜底，本地 :18446）
VPS4_HOST=159.223.36.119
VPS4_PORT=28722

# 备用端点 5（2026-10-26：66clouds CN2 GIA 中继，本地 :18447）
VPS5_HOST=154.36.137.54
VPS5_PORT=28722
CONF_EOF
chmod 600 /etc/routermgmt/tunnel.conf""")
    # pin 主端点 DMIT 的 hostkey（ED25519）；DO 兜底端点由 v3 init 的 -y 自动接受
    _pin = run(c, f"TUNNEL_VPS_HOST={mgmt_host} TUNNEL_VPS_PORT=28722 sh /usr/sbin/setup-mgmt-tunnel.sh pin-hostkey {mgmt_fingerprint} 2>&1")
    if "已固化 VPS host key" not in _pin:
        sys.exit("主端点 host key 固化未通过，停止；保留检查点，不启动新隧道")
    run(c, f"/etc/init.d/mgmt-tunnel enable && /etc/init.d/mgmt-tunnel restart >/dev/null 2>&1; sleep 10; curl -sk -m 8 -o /dev/null -w 'health=%{{http_code}}\\n' https://127.0.0.1:18443/api/v1/health; echo instances=$(ps w | grep 1844 | grep -v grep | wc -l)")
    print("  隧道已启动（v3.1 五端点：DMIT 主 + TX/BWG/DO/66GIA 备）")
    # 2026-10-14 R11：探针名单停在旧设备时，新排障口 remote_ssh_alive 永远为空，断了也不告警。
    _pline = f"{RSSH} {DEVID}"
    if not re.fullmatch(r"132\d{2} rtr-[0-9a-f]+", _pline):
        print(f"  探针行形状异常，中止: {_pline!r}")
        sys.exit(7)
    _pcmd = (
        "f=/etc/mgmt-rssh-probe.conf; touch \"$f\"; "
        f"grep -qxF '{_pline}' \"$f\" || echo '{_pline}' >> \"$f\"; "
        f"grep -cxF '{_pline}' \"$f\""
    )
    _rp = subprocess.run(VPS_SSH + [_pcmd], capture_output=True, text=True, timeout=30)
    _probe_ok = (_rp.stdout or "").strip() == "1"
    print("  探针名单:", "已登记 " + _pline if _probe_ok else f"失败 stdout={(_rp.stdout or '').strip()[:40]} stderr={(_rp.stderr or '').strip()[:80]}")
    if not _probe_ok:
        sys.exit(7)

    # 7) mihomo（仓库直推）
    print("  推 mihomo 57MB...", end=" ", flush=True)
    push(c, MIHOMO_REPO + r"\mihomo-linux-arm64", "/usr/bin/mihomo", 755)
    push(c, MIHOMO_REPO + r"\geosite.dat", "/etc/mihomo/GeoSite.dat", 644)
    push(c, MIHOMO_REPO + r"\country.mmdb", "/etc/mihomo/Country.mmdb", 644)
    print("完成")
    # 配置（从 SN-R3 历史配置改 device_id）
    cfg = open(REPO + r"\scripts\clash-config-router.yaml", encoding="utf-8").read()
    m = re.search(r"proxies:\n(.*?)\nproxy-groups:", cfg, re.S)
    nodes_yaml = m.group(1) if m else ""
    m2 = re.search(r'name: "AUTO"\n.*?proxies:\n(.*?)\n', cfg, re.S)
    members = "\n".join("      " + l.strip() for l in m2.group(1).strip().splitlines() if l.strip().startswith("- ")) if m2 else ""
    try:
        import yaml
        d = yaml.safe_load(cfg)
        direct_rules = "\n".join(f'  - IP-CIDR,{p["server"]}/32,DIRECT,no-resolve' for p in d.get("proxies", []))
    except:
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
    ( i=0; while [ $i -lt 60 ]; do wget -T 2 -q -O /dev/null http://127.0.0.1:9090/version 2>/dev/null && break; sleep 1; i=$((i+1)); done; /etc/mihomo-ipt.sh
      # REJECT-SELFHEAL 2026-10-13: mihomo-ipt 插完 DROP 后 REJECT 压回第 1，杜绝重载挤位（R7 四次重启实锤竞态）
      iptables -D FORWARD -i br-lan -p udp -m udp --dport 443 -j REJECT --reject-with icmp-port-unreachable 2>/dev/null
      iptables -I FORWARD 1 -i br-lan -p udp -m udp --dport 443 -j REJECT --reject-with icmp-port-unreachable ) &
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
# V8：显式调用 mihomo-ipt 后立即自压 REJECT 回第 1（否则 Q 关必假 FAIL）
iptables -D FORWARD -i br-lan -p udp -m udp --dport 443 -j REJECT --reject-with icmp-port-unreachable 2>/dev/null
iptables -I FORWARD 1 -i br-lan -p udp -m udp --dport 443 -j REJECT --reject-with icmp-port-unreachable
''')
    print(f"  mihomo: {out.splitlines()[-3:]}")
    c.close()

    # 8) 发布双节点
    def wait_final(tid, timeout=240):
        t0 = time.time()
        while time.time() - t0 < timeout:
            rr = requests.get(M.BASE + "/api/v1/tasks?page_size=100", headers=H, verify=False, timeout=15).json()
            t = next((x for x in (rr.get("items") or []) if x["id"] == tid), None)
            if t and t["status"] in ("ok","failed","rolled_back","expired","timeout"): return t
            time.sleep(10)
        return None
    # 2026-10-08：绑定现役标准（BWG 主 + LAX + TX-hy2 + TX-vless；不绑 DO）
    for _n, _p in ((5, False), (3, False), (2, False), (4, True)):
        r = requests.post(M.BASE + "/api/v1/assignments/batch", headers=H,
                          json={"device_ids": [DEVID], "node_id": _n, "is_primary": _p,
                                "apply": _p}, verify=False, timeout=30)
    tid = r.json()["items"][0]["task_id"]
    _fpq = (f"UPDATE node_assignments SET failover_priority=0 WHERE device_id='{DEVID}' AND node_id=5; "
            f"UPDATE node_assignments SET failover_priority=1 WHERE device_id='{DEVID}' AND node_id=3; "
            f"UPDATE node_assignments SET failover_priority=2 WHERE device_id='{DEVID}' AND node_id=2")
    # 2026-10-14 根治：$POSTGRES_USER 嵌套转义实测静默失败（R8/R9 双实证靠手动救）——-U postgres 直连+结果校验
    # 2026-10-14 multi-review r2-修（codex#26/kimi/grok）："UPDATE 0" 也含 UPDATE 子串=假成功——
    # 收紧为 UPDATE 1 ×3 计数，且失败不再只告警，_fp8_ok 进入终验关卡（带缺口不放行）。
    _sqlcmd = "docker exec routermgmt-db-1 psql -U postgres -d routermgmt -c \"" + _fpq + "\""
    _r8 = subprocess.run(['ssh', '-p', '28722', '-i', 'C:/Users/Administrator/.ssh/id_rsa_dmit', '-o', 'IdentitiesOnly=yes',
                    'root@179.255.137.64', _sqlcmd], capture_output=True, text=True, timeout=60)
    _fp8_ok = (_r8.stdout or "").count("UPDATE 1") >= 3
    if not _fp8_ok:
        print(f"  ⚠️ failover SQL 未全量生效(UPDATE 1×3): stdout={_r8.stdout.strip()[:60]} stderr={_r8.stderr.strip()[:60]}")
    requests.patch(M.BASE + f"/api/v1/devices/{DEVID}", headers=H,
                   json={"traffic_alert_hourly_mb": 2048, "reboot_cron": "0 0 * * *"}, verify=False, timeout=15)
    t = wait_final(tid)
    print(f"  发布 v{(t or {}).get('result',{}).get('detail',{}).get('version','?')}: {t['status'] if t else 'TIMEOUT'}")
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
  # DBDC_card0.dat 曾被驱动收成 000，先放开属主写，否则 sed 改不到
  chmod u+rw "$F" 2>/dev/null || true
  # 2026-10-08：保 WPAPSK 键名序号（旧法丢序号）；DBDC 的 5G 主 SSID 在 SSID7
  sed -i "s/^SSID1=.*/SSID1=__NAME__/" "$F"
  [ "$F" = "DBDC_card0.dat" ] && sed -i "s/^SSID7=.*/SSID7=__NAME__-5G/" "$F"
  [ "$F" = "mt7981.dbdc.b1.dat" ] && sed -i "s/^SSID1=.*/SSID1=__NAME__-5G/" "$F"
  sed -i "s/^\(WPAPSK[0-9]*\)=.*/\1=[REDACTED]/" "$F"
  grep -q "^RadioOff=" "$F" && sed -i "s/^RadioOff=.*/RadioOff=1/" "$F" || sed -i "2i RadioOff=1" "$F"
done
# 2026-10-14 R11：/sbin/wifi 开机把 UCI 的 ssid/key 写回 dat。
# 只改 dat 时，重启后主 SSID 变回出厂名；mesh/guest 的 key 仍是 12345678 时，对应槽也被写回。
uci set wireless.ra0.ssid='__NAME__'
uci set wireless.rax0.ssid='__NAME__-5G'
uci set wireless.ra0.key='[REDACTED]'; uci set wireless.ra0.disabled='1'
uci set wireless.rax0.key='[REDACTED]'; uci set wireless.rax0.disabled='1'
uci set wireless.RADIO2G.disabled='1' 2>/dev/null; uci set wireless.RADIO5G.disabled='1' 2>/dev/null
for s in $(uci show wireless 2>/dev/null | sed -n 's/^wireless\.\([A-Za-z0-9_]*\)\.key=.*/\1/p'); do
  k=$(uci -q get wireless.$s.key || true)
  if [ "$k" = "12345678" ]; then uci set wireless.$s.key='[REDACTED]'; echo "UCI_KEY_FIXED $s"; fi
done
uci commit wireless
sed -i '/# wifi-default-off/,$d' /etc/rc.local
# V14：删除既有 exit 0，否则追加的 WiFi 开机策略落其后变死代码（R7 实锤）
sed -i '/^exit 0$/d' /etc/rc.local
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
'(sleep 10; grep -q routermgmt-netprobe /etc/crontabs/root 2>/dev/null || echo "*/5 * * * * /etc/routermgmt/netprobe.sh #routermgmt-netprobe" >> /etc/crontabs/root; grep -q routermgmt-connlog /etc/crontabs/root 2>/dev/null || echo "*/5 * * * * /etc/routermgmt/connlog.sh #routermgmt-connlog" >> /etc/crontabs/root; grep -q mgmt-port-discovery /etc/crontabs/root 2>/dev/null || echo "* * * * * /etc/routermgmt/mgmt-port-discovery.sh #mgmt-port" >> /etc/crontabs/root; /etc/init.d/cron restart) > /dev/null 2>&1 &' \
'exit 0' >> /etc/rc.local
ifconfig ra0 down; ifconfig rax0 down
for I in wan br-lan ra0 rax0; do [ -d /sys/class/net/$I ] && echo "$I: $(cat /sys/class/net/$I/address)"; done
'''.replace("__NAME__", NAME))
    c.close()
    macs = dict(re.findall(r"(wan|br-lan|ra0|rax0): ([0-9a-f:]+)", out))
    # 2026-10-13 审计增补：MAC 指纹台账（防误刷登记+回归识别）
    requests.patch(M.BASE + f"/api/v1/devices/{DEVID}", headers=H,
                   json={"mac_addr": macs.get("ra0","")}, verify=False, timeout=15)
    doc = f"""# {NAME} ({DEVID}) 凭证档案 — {time.strftime('%Y-%m-%d')}

| 通道 | 值 |
|---|---|
| SSH（LAN） | `ssh root@192.168.132.1` / `{PW}` |
| LuCI | `http://192.168.132.1` root / 同上 |
| 反向 SSH | `ssh -p 28722 -i ~/.ssh/id_rsa_dmit root@179.255.137.64` → `sshpass -p <root口令> ssh -p {RSSH} root@127.0.0.1`（DMIT 主台，2026-10-02 起） |
| WiFi（默认关闭） | SSID `{NAME}`，PSK `[REDACTED]`；开启：管理台「开WiFi」 |
| 管理台 | ID `{DEVID}`，RSSH `{RSSH}` |
| MAC 固定（uci，重启生效） | WAN=`{wan_mac}` br-lan=`{brlan_mac}`（标准步骤；出厂实测 wan={macs.get('wan','?')} ra0={macs.get('ra0','?')}） |

| 接口 | MAC |
|---|---|
| WAN | `{macs.get('wan','?')}` |
| 2.4G | `{macs.get('ra0','?')}` |
| 5G | `{macs.get('rax0','?')}` |
"""
    open(REPO + rf"\backups\SN-R{N}-凭证档案-{time.strftime('%Y%m%d')}.md", "w", encoding="utf-8").write(doc)
    note = f"WAN MAC {macs.get('wan','?')} | 2.4G {macs.get('ra0','?')} | 5G {macs.get('rax0','?')} | WiFi默认关闭(密码[REDACTED]) | {time.strftime('%Y-%m-%d')} 刷机录入"
    requests.patch(M.BASE + f"/api/v1/devices/{DEVID}", headers=H,
                   json={"location_note": note, "mac_addr": macs.get("ra0","")}, verify=False, timeout=15)
    # 2026-10-13 审计增补：指纹写入凭证总览（防误刷登记+回归识别基线）
    with open(REPO + r"\backups\SN-R系列凭证总览.md", "a", encoding="utf-8") as _f:
        _f.write(f"\n- {time.strftime('%Y-%m-%d')} 指纹登记 {NAME}（{DEVID}）：wan={macs.get('wan','?')} ra0={macs.get('ra0','?')} rax0={macs.get('rax0','?')}（刷机脚本自动写入）\n")
    # 10) 终验关卡（2026-10-08：R1/R2/R4 事故沉淀；任一 FAIL 非零退出，禁止半成品出厂）
    c = ssh(PW)
    out = run(c, r'''
echo "L=$(netstat -ltn | grep -cE '1844[3-6]')"
echo "H=$(curl -sk -m 6 -o /dev/null -w '%{http_code}' https://127.0.0.1:18443/api/v1/health)"
echo "E=$(curl -s -m 10 -x http://127.0.0.1:7890 https://api.ipify.org 2>/dev/null)"
echo "G=$(curl -s -o /dev/null -w '%{http_code}' -m 12 -x http://127.0.0.1:7890 https://www.google.com/generate_204)"
B64=$(printf %s __PW__ | openssl base64 -A | sed 's/+/%2B/g; s;/;%2F;g; s/=/%3D/g')
echo "W=$(curl -s -o /dev/null -w '%{http_code}' -m 8 -d "username=cm9vdA%3D%3D&password=$B64" http://192.168.132.1/cgi-bin/luci)"
echo "C=$(grep -c routermgmt /etc/crontabs/root)"
echo "R=$(grep -c onboot-hooks /etc/rc.local)"
echo "T=$(grep -c 'window.btoa(document' /usr/lib/lua/luci/view/admin_web/login.htm)/$(grep -c 'window.btoa(document' /usr/lib/lua/luci/view/admin_web/login_m.htm)"
'''.replace('__PW__', PW))
    kv = dict(re.findall(r"^(\w)=(.*)$", out, re.M))
    ext = run(c, r'''
echo "P=$(ps w | grep -E 'by_agen[t]|netbox_daemo[n]|zco[n]|rathol[e]|watche[r]|net_qua_cl[i]|inst_widt[h]|tcp_stun_se[r]|install_pdnter[m]|install_mbo[x]' | grep -v grep | wc -l)"
echo "P2=$(netstat -ltn | grep -cE '5100[2]|982[1]|980[0]|6002[2]')"
echo "P3=$(ls /etc/rc.d/S* 2>/dev/null | grep -cE 'netbo[x]|zco[n]|client_manag[e]|versionInf[o]|timeRestar[t]|mbo[x]|hol[e]|jcgqo[s]|autoupdat[e]')"
echo "P4=$(grep -cE 'install_mbo[x]|install_pdnter[m]|mbox_ini[t]' /etc/rc.local)"
echo "P5=$(ls /usr/bin/rathole /usr/bin/zcon-ng /usr/bin/zcon-edge-arm64 '/usr/bin/{install_mbox.sh}' '/usr/bin/{install_pdnterminal.sh}' /www/cgi-bin/cgi-exec /www/cgi-bin/cgi-upload /www/cgi-bin/cgi-download /www/cgi-bin/cgi-backup 2>/dev/null | wc -l)"
echo "P6=$(grep -c 'factory checkMem' /etc/crontabs/root)"
echo "X=$(grep -c '^exit 0' /etc/rc.local)"
echo "XL=$(tail -1 /etc/rc.local | grep -c 'exit 0')"
echo "RC=$(grep -c '#routermgmt-reboot' /etc/crontabs/root)"
echo "CG=$(ls /www/cgi-bin/ 2>/dev/null | grep -cv luci)"
echo "Q=$(iptables -S FORWARD | grep 443 | head -1 | grep -c REJECT)"
echo "B=$(logread 2>/dev/null | grep -c 'heartbeat ok')"
echo "DK=$(grep -c '^/usr/sbin/update_docker' /etc/rc.local)"
echo "US=$(uci -q get wireless.ra0.ssid)/$(uci -q get wireless.rax0.ssid)"
echo "DS=$(grep '^SSID1=' /etc/wireless/mediatek/mt7981.dbdc.b0.dat)"
echo "D5=$(grep '^SSID1=' /etc/wireless/mediatek/mt7981.dbdc.b1.dat)"
echo "D7=$(grep '^SSID7=' /etc/wireless/mediatek/DBDC_card0.dat)"
echo "FP=$(grep -h '=12345678$' /etc/wireless/mediatek/mt7981.dbdc.b0.dat /etc/wireless/mediatek/mt7981.dbdc.b1.dat /etc/wireless/mediatek/DBDC_card0.dat 2>/dev/null | wc -l | tr -d ' ')"
echo "TD=$([ -x /etc/routermgmt/tunnel-watchdog.sh ] && echo 1)"
echo "WD=$(cat /etc/routermgmt/wan-watchdog.mode 2>/dev/null)"
''')
    # 2026-10-14 multi-review r2-修：单字符 \w 吃不到 P2-P5 多字符键——首测必假失败、
    # 白等 60 秒消抖（codex#23/grok#18 双独立发现并复现）。R9 的"15/15"实际走的是复测路径。
    kv.update(dict(re.findall(r"^(\w+)=(.*)$", ext, re.M)))
    # 2026-10-14 R11：首轮通过时不再跳过 checkMem/exit0/每日重启/cgi，
    # 并挡住 update_docker 开机行、出厂 SSID 回写、出厂密码槽。
    def _gates(kv):
        def _n(key):
            try:
                return int(str(kv.get(key, "0")).strip() or "0")
            except ValueError:
                return 0
        return [
            ("隧道监听>=3", _n("L") >= 3),
            ("API健康200", kv.get("H") == "200"),
            ("出口IP非空", len(kv.get("E", "")) >= 8),
            ("google 204", kv.get("G") == "204"),
            ("网页登录302", kv.get("W") == "302"),
            ("crontab>=4条", _n("C") >= 4),
            ("rc.local钩子", kv.get("R") == "1"),
            ("btoa编码2/1", kv.get("T") == "2/1"),
            ("PCDN残留=0", kv.get("P") == "0"),
            ("异常端口=0", kv.get("P2") == "0"),
            ("S自启残留=0", kv.get("P3") == "0"),
            ("rc钩子残留=0", kv.get("P4") == "0"),
            ("不合规文件=0", kv.get("P5") == "0"),
            ("checkMem已清", kv.get("P6") == "0"),
            ("exit0唯一且末尾", kv.get("X") == "1" and kv.get("XL") == "1"),
            ("每日重启cron在位", kv.get("RC") == "1"),
            ("cgi-bin仅luci", kv.get("CG") == "0"),
            ("update_docker已注释", kv.get("DK") == "0"),
            ("UCI主SSID", kv.get("US") == f"{NAME}/{NAME}-5G"),
            ("dat主SSID", kv.get("DS") == f"SSID1={NAME}" and kv.get("D5") == f"SSID1={NAME}-5G" and kv.get("D7") == f"SSID7={NAME}-5G"),
            ("出厂密码槽=0", kv.get("FP") == "0"),
            ("QUIC顺序REJECT第一", kv.get("Q") == "1"),
            ("心跳已产生", _n("B") >= 1),
            ("隧道看门狗在位", kv.get("TD") == "1"),
            ("WAN看门狗log模式", kv.get("WD") == "log"),
        ]
    checks = _gates(kv)
    failed = [n for n, ok in checks if not ok]
    if failed:
        # 2026-10-14 消抖（R8/R9 实证）：六面关卡对厂商固件收尾期的瞬态过敏——60 秒后重测一次
        print(f"    首测未过 {failed}，60 秒后复测（消抖）…")
        time.sleep(60)
        out = run(c, r'''
echo "L=$(netstat -ltn | grep -cE '1844[3-6]')"
echo "H=$(curl -sk -m 6 -o /dev/null -w '%{http_code}' https://127.0.0.1:18443/api/v1/health)"
echo "E=$(curl -s -m 10 -x http://127.0.0.1:7890 https://api.ipify.org 2>/dev/null)"
echo "G=$(curl -s -o /dev/null -w '%{http_code}' -m 12 -x http://127.0.0.1:7890 https://www.google.com/generate_204)"
B64=$(printf %s __PW__ | openssl base64 -A | sed 's/+/%2B/g; s;/;%2F;g; s/=/%3D/g')
echo "W=$(curl -s -o /dev/null -w '%{http_code}' -m 8 -d "username=cm9vdA%3D%3D&password=$B64" http://192.168.132.1/cgi-bin/luci)"
echo "C=$(grep -c routermgmt /etc/crontabs/root)"
echo "R=$(grep -c onboot-hooks /etc/rc.local)"
echo "T=$(grep -c 'window.btoa(document' /usr/lib/lua/luci/view/admin_web/login.htm)/$(grep -c 'window.btoa(document' /usr/lib/lua/luci/view/admin_web/login_m.htm)"
echo "P=$(ps w | grep -E 'by_agen[t]|netbox_daemo[n]|zco[n]|rathol[e]|watche[r]|net_qua_cl[i]|inst_widt[h]|tcp_stun_se[r]|install_pdnter[m]|install_mbo[x]' | grep -v grep | wc -l)"
echo "P2=$(netstat -ltn | grep -cE '5100[2]|982[1]|980[0]|6002[2]')"
echo "P3=$(ls /etc/rc.d/S* 2>/dev/null | grep -cE 'netbo[x]|zco[n]|client_manag[e]|versionInf[o]|timeRestar[t]|mbo[x]|hol[e]|jcgqo[s]|autoupdat[e]')"
echo "P4=$(grep -cE 'install_mbo[x]|install_pdnter[m]|mbox_ini[t]' /etc/rc.local)"
echo "P5=$(ls /usr/bin/rathole /usr/bin/zcon-ng /usr/bin/zcon-edge-arm64 '/usr/bin/{install_mbox.sh}' '/usr/bin/{install_pdnterminal.sh}' /www/cgi-bin/cgi-exec /www/cgi-bin/cgi-upload /www/cgi-bin/cgi-download /www/cgi-bin/cgi-backup 2>/dev/null | wc -l)"
echo "P6=$(grep -c 'factory checkMem' /etc/crontabs/root)"
echo "X=$(grep -c '^exit 0' /etc/rc.local)"
echo "XL=$(tail -1 /etc/rc.local | grep -c 'exit 0')"
echo "RC=$(grep -c '#routermgmt-reboot' /etc/crontabs/root)"
echo "CG=$(ls /www/cgi-bin/ 2>/dev/null | grep -cv luci)"
echo "Q=$(iptables -S FORWARD | grep 443 | head -1 | grep -c REJECT)"
echo "B=$(logread 2>/dev/null | grep -c 'heartbeat ok')"
echo "DK=$(grep -c '^/usr/sbin/update_docker' /etc/rc.local)"
echo "US=$(uci -q get wireless.ra0.ssid)/$(uci -q get wireless.rax0.ssid)"
echo "DS=$(grep '^SSID1=' /etc/wireless/mediatek/mt7981.dbdc.b0.dat)"
echo "D5=$(grep '^SSID1=' /etc/wireless/mediatek/mt7981.dbdc.b1.dat)"
echo "D7=$(grep '^SSID7=' /etc/wireless/mediatek/DBDC_card0.dat)"
echo "FP=$(grep -h '=12345678$' /etc/wireless/mediatek/mt7981.dbdc.b0.dat /etc/wireless/mediatek/mt7981.dbdc.b1.dat /etc/wireless/mediatek/DBDC_card0.dat 2>/dev/null | wc -l | tr -d ' ')"
'''.replace('__PW__', PW))
        kv = dict(re.findall(r"^(\w+)=(.*)$", out, re.M))
        checks = _gates(kv)
        failed = [n for n, ok in checks if not ok]
    for n, ok in checks:
        print(f"    {'PASS' if ok else 'FAIL'} {n}")

    # 11) 平台下发验证（2026-10-13 新增：切节点往返=远程管理端到端实测）
    def _switch_verify(node_id, expect_ip, label):
        rr = requests.post(M.BASE + "/api/v1/assignments/batch", headers=H,
                           json={"device_ids": [DEVID], "node_id": node_id, "is_primary": True, "apply": True}, verify=False, timeout=30)
        _t = wait_final(rr.json()["items"][0]["task_id"])
        time.sleep(6)
        _eg = run(c, "curl -s -m 10 -x http://127.0.0.1:7890 https://api.ipify.org 2>/dev/null").strip()
        ok = (_t and _t["status"] == "ok" and _eg == expect_ip)
        print(f"    {'PASS' if ok else 'FAIL'} 平台下发-{label}(任务{_t['status'] if _t else '超时'}/出口{_eg})")
        return ok
    d1 = _switch_verify(5, "179.255.137.64", "切LAX")
    d2 = _switch_verify(4, "74.82.193.113", "回东京")
    # 2026-10-14 实战补丁：assignments/batch 会清零 failover_priority——下发测试后复原舰队标准序
    _fpq2 = ("UPDATE node_assignments SET failover_priority=0 WHERE device_id='" + DEVID + "' AND node_id=5; "
             "UPDATE node_assignments SET failover_priority=1 WHERE device_id='" + DEVID + "' AND node_id=3; "
             "UPDATE node_assignments SET failover_priority=2 WHERE device_id='" + DEVID + "' AND node_id=2;")
    _r11 = subprocess.run(VPS_SSH + ["docker exec routermgmt-db-1 psql -U postgres -d routermgmt -c \"" + _fpq2 + "\""],
                          capture_output=True, text=True, timeout=60)
    _fp11_ok = (_r11.stdout or "").count("UPDATE 1") >= 3
    if not _fp11_ok:
        print(f"  ⚠️ 复原 SQL 未全量生效(UPDATE 1×3): stdout={_r11.stdout.strip()[:60]} stderr={_r11.stderr.strip()[:60]}")
    # D13：QUIC 终态=读回验证（先读，乱则修，再读回必须 PASS）
    _q1 = run(c, "iptables -S FORWARD | grep 443 | head -1")
    if "REJECT" not in _q1:
        run(c, "iptables -D FORWARD -i br-lan -p udp -m udp --dport 443 -j REJECT --reject-with icmp-port-unreachable 2>/dev/null; iptables -I FORWARD 1 -i br-lan -p udp -m udp --dport 443 -j REJECT --reject-with icmp-port-unreachable")
        _q1 = run(c, "iptables -S FORWARD | grep 443 | head -1")
    d3 = "REJECT" in _q1
    print(f"    {'PASS' if d3 else 'FAIL'} QUIC终态读回")
    c.close()
    # 2026-10-14 multi-review r2-修（codex#21/29 同款原则）：failover 双 SQL 与关卡同权重——
    # 带缺口不放行；成功文案不再硬编码关数（旧"11关"已漂移两次）。
    if failed or not (d1 and d2) or not d3 or not (_fp8_ok and _fp11_ok):
        print(f"  终验未过: {failed + (['平台下发'] if not (d1 and d2) else []) + (['failover序'] if not (_fp8_ok and _fp11_ok) else [])}")
        sys.exit(2)
    print(f"[{time.strftime('%H:%M:%S')}] ✅ {NAME} 刷机完成，终验 {len(checks)} 关全过+下发双验+QUIC读回+failover双SQL 通过，凭证档案已写入")

if __name__ == "__main__":
    main()
