# -*- coding: utf-8 -*-
"""GL.iNet 设备接入舰队（通用版，2026-11-13 标准化）
用法：
  python glinet-onboard.py --name <设备名> --rssh <反向口> --host <设备IP> [--note <备注>] [--skip-gia]

参数：
  --name    设备名（如 GL-MT3600BE-02）
  --rssh    反向口（13234-13300，自动占用检测）
  --host    设备 IP（默认 192.168.8.1，GL 出厂 LAN）
  --note    位置备注（可选）
  --skip-gia 跳过 GIA 备用排障口（默认加）

前置：PC 连 GL WiFi 或网线直插 LAN；广州 ubuntu SSH 可达；TOTP 从台账读。
幂等：广州 /tmp/pkg-<name>.json 存在即复用身份，可断点续跑。
"""
import argparse, base64, hashlib, hmac, json, os, paramiko, re, struct, sys, time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENT_DIR = os.path.join(REPO, 'mgmt', 'agent')
GZ_HOST, GZ_PORT, GZ_USER = '134.175.151.213', 22, 'ubuntu'
DEV_PW = '[REDACTED]'

def die(msg):
    print('FAIL:', msg); sys.exit(1)

def gz_connect():
    g = paramiko.SSHClient(); g.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    g.connect(GZ_HOST, port=GZ_PORT, username=GZ_USER, password=DEV_PW, timeout=15)
    return g

def gl_connect(host):
    c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, port=22, username='root', password=DEV_PW, timeout=10)
    return c

def gz_api(gz, method, path, body=None):
    remote = """
import json, urllib.request, ssl
ctx = ssl._create_unverified_context()
tok = open('/tmp/.gz-tok').read().strip()
method = %r
path = %r
body = %r
req = urllib.request.Request("https://127.0.0.1:8443/api/v1" + path,
    data=json.dumps(body).encode() if method == 'POST' else None,
    headers={"Content-Type": "application/json", "Authorization": "Bearer " + tok})
try:
    with urllib.request.urlopen(req, context=ctx, timeout=10) as r:
        print(r.status); print(r.read().decode())
except urllib.error.HTTPError as e:
    print(e.code); print(e.read().decode())
""" % (method, path, body)
    _, o, e = gz.exec_command("python3 - <<'PYK'\n" + remote + "\nPYK", timeout=30)
    out = o.read().decode().strip()
    lines = out.split('\n', 1)
    return int(lines[0]), lines[1] if len(lines) > 1 else ''

def totp_now(secret_b32):
    key = base64.b32decode(secret_b32 + '=' * ((8 - len(secret_b32) % 8) % 8), casefold=True)
    d = hmac.new(key, struct.pack('>Q', int(time.time()) // 30), hashlib.sha1).digest()
    off = d[-1] & 0x0F
    return '%06d' % ((struct.unpack('>I', d[off:off+4])[0] & 0x7FFFFFFF) % 1000000)

def push_b64(sh, local, remote):
    data = open(local, 'rb').read()
    lmd5 = hashlib.md5(data).hexdigest()
    stdin, so, se = sh.exec_command("openssl base64 -d -A > %s" % remote, timeout=60)
    stdin.channel.sendall(base64.b64encode(data)); stdin.channel.shutdown_write()
    so.read()
    _, o2, _ = sh.exec_command("md5sum %s | cut -d' ' -f1" % remote, timeout=15)
    rmd5 = o2.read().decode().strip()
    if lmd5 != rmd5:
        die('传输校验失败 %s' % remote)

def run_gl(sh, cmd, timeout=60):
    _, o, e = sh.exec_command(cmd, timeout=timeout)
    return o.read().decode() + ('\n[stderr] ' + e.read().decode()[:200] if e.read() else '')

# ============ 参数 ============
parser = argparse.ArgumentParser(description='GL.iNet 设备接入舰队')
parser.add_argument('--name', required=True, help='设备名（如 GL-MT3600BE-02）')
parser.add_argument('--rssh', type=int, required=True, help='反向口（13234-13300）')
parser.add_argument('--host', default='192.168.8.1', help='设备 IP（默认 192.168.8.1）')
parser.add_argument('--note', default='', help='位置备注')
parser.add_argument('--skip-gia', action='store_true', help='跳过 GIA 备用排障口')
args = parser.parse_args()

if not (13234 <= args.rssh < 13300):
    die('反向口必须在 13234-13299 之间')

DEV_NAME = args.name
DEV_NOTE = args.note or ('GL.iNet %s WiFi直装 %s' % (args.name, time.strftime('%Y-%m-%d')))
RSSH = args.rssh
GL_HOST = args.host
PKG_NAME = 'pkg-%s.json' % DEV_NAME.replace(' ', '_')

# ============ 阶段A：广州录入 ============
print('=== 阶段A 广州录入 ===')
src = open(os.path.join(REPO, '登录信息与配置总表-20260917.md'), encoding='utf-8').read()
ADMIN_PW = src.split('| 密码 | `')[1].split('`')[0]
TOTP_SECRET = src.split('TOTP 密钥（重绑验证器用） | `')[1].split('`')[0]
CODE = totp_now(TOTP_SECRET)
PWB = base64.b64encode(ADMIN_PW.encode()).decode()

gz = gz_connect()

# 幂等检查
_, o0, _ = gz.exec_command('[ -f /tmp/%s ] && echo EXISTS || echo NONE' % PKG_NAME, timeout=10)
if o0.read().decode().strip() == 'EXISTS':
    _, o1, _ = gz.exec_command('cat /tmp/%s' % PKG_NAME, timeout=10)
    pkg = json.loads(o1.read().decode())
    devid = pkg['device_id']
    print('[A0] 幂等续跑：复用 %s' % devid)
    _SKIP_A = True
else:
    _SKIP_A = False

login_js = """
import base64, json, urllib.request, ssl
ctx = ssl._create_unverified_context()
pw = base64.b64decode('%s').decode()
def post(path, obj):
    req = urllib.request.Request('https://127.0.0.1:8443/api/v1' + path,
        data=json.dumps(obj).encode(), headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=5) as r: return json.load(r)
    except urllib.error.HTTPError as e: return {'_http': e.code, 'msg': e.read().decode()[:120]}
r1 = post('/auth/login', {'username': 'admin', 'password': pw})
ticket = r1.get('ticket') or r1.get('data', {}).get('ticket', '')
if not ticket: print('LOGIN_FAIL'); raise SystemExit
r2 = post('/auth/totp', {'ticket': ticket, 'code': '%s'})
tok = r2.get('token') or r2.get('data', {}).get('token', '')
if not tok: print('TOTP_FAIL'); raise SystemExit
open('/tmp/.gz-tok', 'w').write(tok)
print('TOKEN_OK')
""" % (PWB, CODE)
_, o, _ = gz.exec_command("python3 - <<'PYK'\n" + login_js + "\nPYK", timeout=30)
if 'TOKEN_OK' not in o.read().decode():
    die('广州 TOTP 登录失败')
print('[A1] 登录 OK')

if not _SKIP_A:
    st, body = gz_api(gz, 'POST', '/devices', {'name': DEV_NAME, 'location_note': DEV_NOTE})
    if st >= 300: die('设备创建失败 HTTP %s: %s' % (st, body[:150]))
    dev = json.loads(body)
    devid = dev.get('id'); ecode = dev.get('enroll_code')
    if not devid or not ecode: die('创建响应缺 id/enroll_code')
    print('[A2] 设备创建:', devid)

    st, body = gz_api(gz, 'POST', '/devices/enroll', {'enroll_code': ecode})
    if st >= 300: die('enroll 失败 HTTP %s: %s' % (st, body[:150]))
    pkg = json.loads(body)
    pkg['mgmt_url'] = 'https://127.0.0.1:18443'
    pkg['transport_mode'] = 'mock'
    pkg['sub_url'] = ''
    stdin_, stdout_, _ = gz.exec_command("cat > /tmp/%s; chmod 600 /tmp/%s" % (PKG_NAME, PKG_NAME), timeout=10)
    stdin_.write(json.dumps(pkg)); stdin_.channel.shutdown_write(); stdout_.read()
    print('[A3] enroll OK')

# ============ 阶段B：设备侧安装 ============
print('=== 阶段B 设备侧安装 ===')
gl = gl_connect(GL_HOST)
gl.exec_command('mkdir -p /tmp/stage/handlers /tmp/stage/init /etc/routermgmt/handlers /etc/routermgmt/logs /etc/routermgmt/state')[1].read()

FILES = [
    (AGENT_DIR + r"\routermgmt-agent.sh",      "/tmp/stage/routermgmt-agent.sh",      0o755),
    (AGENT_DIR + r"\handlers\switch_node.sh",  "/tmp/stage/handlers/switch_node.sh",  0o644),
    (AGENT_DIR + r"\access-fw.sh",             "/tmp/stage/access-fw.sh",             0o755),
    (AGENT_DIR + r"\netprobe.sh",              "/tmp/stage/netprobe.sh",              0o755),
    (AGENT_DIR + r"\connlog.sh",               "/tmp/stage/connlog.sh",               0o755),
    (AGENT_DIR + r"\init\routermgmt",          "/tmp/stage/init/routermgmt",          0o755),
    (AGENT_DIR + r"\init\mgmt-tunnel-v3",      "/tmp/stage/init/mgmt-tunnel-v3",      0o755),
    (AGENT_DIR + r"\init\wan-watch",           "/tmp/stage/init/wan-watch",           0o755),
    (AGENT_DIR + r"\wan-watch.sh",             "/tmp/stage/wan-watch.sh",             0o755),
    (AGENT_DIR + r"\wan-watch.hotplug",        "/tmp/stage/wan-watch.hotplug",        0o755),
    (AGENT_DIR + r"\setup-mgmt-tunnel.sh",     "/tmp/stage/setup-mgmt-tunnel.sh",     0o755),
    (AGENT_DIR + r"\tunnel.conf.example",      "/tmp/stage/tunnel.conf.example",      0o600),
    (AGENT_DIR + r"\install-agent.sh",         "/tmp/stage/install-agent.sh",         0o755),
    (AGENT_DIR + r"\mgmt-port-discovery.sh",   "/tmp/stage/mgmt-port-discovery.sh",   0o755),
    (AGENT_DIR + r"\tunnel-watchdog.sh",       "/tmp/stage/tunnel-watchdog.sh",       0o755),
    (AGENT_DIR + r"\self-heal.sh",             "/tmp/stage/self-heal.sh",             0o755),
]
for srcf, dstf, mode in FILES:
    push_b64(gl, srcf, dstf)
    gl.exec_command("chmod %o %s" % (mode, dstf))[1].read()

# pkg.json 推送
_pkgbuf = json.dumps(pkg).encode()
stdin, so, se = gl.exec_command("openssl base64 -d -A > /tmp/stage/pkg.json; chmod 600 /tmp/stage/pkg.json", timeout=30)
stdin.channel.sendall(base64.b64encode(_pkgbuf)); stdin.channel.shutdown_write(); so.read()
print('[B1] %d 个组件 + pkg.json 已推送' % len(FILES))

# install-agent 自测（GL OpenSSL 1.1.1 无 Ed25519 raw，会 fail——跳过）
out = run_gl(gl, 'cd /tmp/stage && sh install-agent.sh /tmp/stage/pkg.json 2>&1 | tail -2')
print('[B2] install-agent 自测:', out.strip()[:150])

# 手工写 agent.conf（等效 install 第4节）
out = run_gl(gl, r'''
R=/etc/routermgmt; PKG=/tmp/stage/pkg.json
DEVICE_ID=$(jsonfilter -s "$(cat $PKG)" -e '$.device_id')
HMAC_KEY=$(jsonfilter -s "$(cat $PKG)" -e '$.hmac_key')
MGMT_URL=$(jsonfilter -s "$(cat $PKG)" -e '$.mgmt_url')
SIGN_PUB=$(jsonfilter -s "$(cat $PKG)" -e '$.sign_public_key')
[ -n "$DEVICE_ID" ] && [ -n "$HMAC_KEY" ] && [ -n "$MGMT_URL" ] && [ -n "$SIGN_PUB" ] || { echo CONF_FIELDS_FAIL; exit 1; }
cat > $R/agent.conf <<EOF
DEVICE_ID=$DEVICE_ID
HMAC_KEY=$HMAC_KEY
MGMT_URL=$MGMT_URL
SUB_URL=
SIGN_PUB=installed
EOF
chmod 600 $R/agent.conf
printf '%s\n' "$SIGN_PUB" > $R/sign_pub.pem && chmod 644 $R/sign_pub.pem
echo CONF_OK id=$DEVICE_ID
''')
if 'CONF_OK' not in out: die('agent.conf 写入失败: ' + out[:200])
print('[B2b] agent.conf OK')

# 手工补装
out = run_gl(gl, r'''
R=/etc/routermgmt; S=/tmp/stage
cp $S/routermgmt-agent.sh /usr/bin/routermgmt-agent && chmod 755 /usr/bin/routermgmt-agent
cp $S/routermgmt-agent.sh $R/routermgmt-agent.sh && chmod 700 $R/routermgmt-agent.sh
cp $S/handlers/switch_node.sh $R/handlers/switch_node.sh && chmod 600 $R/handlers/switch_node.sh
cp $S/access-fw.sh $R/access-fw.sh && chmod 755 $R/access-fw.sh
cp $S/netprobe.sh $R/netprobe.sh && chmod 755 $R/netprobe.sh
cp $S/connlog.sh $R/connlog.sh && chmod 755 $R/connlog.sh
cp $S/init/routermgmt /etc/init.d/routermgmt && chmod 755 /etc/init.d/routermgmt
cp $S/init/mgmt-tunnel-v3 /etc/init.d/mgmt-tunnel && chmod 755 /etc/init.d/mgmt-tunnel
cp $S/mgmt-port-discovery.sh $R/mgmt-port-discovery.sh && chmod 755 $R/mgmt-port-discovery.sh
cp $S/tunnel-watchdog.sh $R/tunnel-watchdog.sh && chmod 755 $R/tunnel-watchdog.sh
cp $S/self-heal.sh $R/self-heal.sh && chmod 755 $R/self-heal.sh
cp $S/wan-watch.sh /usr/sbin/wan-watch.sh && chmod 755 /usr/sbin/wan-watch.sh
mkdir -p /etc/hotplug.d/iface
cp $S/init/wan-watch /etc/init.d/wan-watch && chmod 755 /etc/init.d/wan-watch
cp $S/wan-watch.hotplug /etc/hotplug.d/iface/98-wan-watch && chmod 755 /etc/hotplug.d/iface/98-wan-watch
/etc/init.d/wan-watch enable
cp $S/setup-mgmt-tunnel.sh /usr/sbin/setup-mgmt-tunnel.sh && chmod 755 /usr/sbin/setup-mgmt-tunnel.sh
echo HAND_INSTALL_OK
''')
if 'HAND_INSTALL_OK' not in out: die('手工补装失败')
print('[B3] 手工补装 OK')

# RSSH 密钥
out = run_gl(gl, '[ -f /etc/routermgmt/id_dropbear_mgmt ] || dropbearkey -t rsa -s 2048 -f /etc/routermgmt/id_dropbear_mgmt >/dev/null 2>&1; chmod 600 /etc/routermgmt/id_dropbear_mgmt; dropbearkey -y -f /etc/routermgmt/id_dropbear_mgmt 2>/dev/null | grep ssh-rsa')
publine = [l for l in out.strip().split('\n') if 'ssh-rsa' in l]
if not publine: die('密钥生成失败')
DEV_PUB = publine[-1].strip()
print('[B4] 密钥 OK')

# tunnel.conf（VPS_PORT 键名）
tunnel_conf = """VPS_HOST=134.175.151.213
VPS_PORT=28722
MGMT_LOCAL_PORT=18443
MGMT_TARGET=127.0.0.1:8443
RSSH_REMOTE_PORT=%d
RSSH_TARGET=127.0.0.1:22

VPS2_HOST=43.134.46.44
VPS2_PORT=28722

VPS3_HOST=74.82.203.175
VPS3_PORT=28722

VPS5_HOST=154.36.139.155
VPS5_PORT=28722
""" % RSSH
_, o, _ = gl.exec_command("cat > /etc/routermgmt/tunnel.conf && chmod 600 /etc/routermgmt/tunnel.conf <<'CONF'\n" + tunnel_conf + "CONF\n", timeout=15)
o.read()
print('[B5] tunnel.conf OK')

# mgmt_active_port 双补丁
out = run_gl(gl, r'''
R=/etc/routermgmt
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
grep -q mgmt_active_port $R/routermgmt-agent.sh && grep -q "mgmt-port-discovery.sh >/dev/null" $R/routermgmt-agent.sh && echo PATCH_OK || echo PATCH_MISS
sh -n $R/routermgmt-agent.sh && echo AGENT_SYNTAX_OK || echo AGENT_SYNTAX_FAIL
cp $R/routermgmt-agent.sh /usr/bin/routermgmt-agent && chmod 755 /usr/bin/routermgmt-agent
echo log > $R/wan-watchdog.mode
''')
print('[B6] agent补丁:', 'PATCH_OK' in out and 'OK' or out[:200])

# cron 五条
out = run_gl(gl, r'''
C=/etc/crontabs/root
grep -q self-heal $C 2>/dev/null || echo '* * * * * /etc/routermgmt/self-heal.sh #self-heal' >> $C
grep -q mgmt-port-discovery $C 2>/dev/null || echo '* * * * * /etc/routermgmt/mgmt-port-discovery.sh #mgmt-port' >> $C
grep -q routermgmt-netprobe $C 2>/dev/null || echo '*/5 * * * * /etc/routermgmt/netprobe.sh #routermgmt-netprobe' >> $C
grep -q routermgmt-connlog $C 2>/dev/null || echo '*/5 * * * * /etc/routermgmt/connlog.sh #routermgmt-connlog' >> $C
grep -q routermgmt-reboot $C 2>/dev/null || echo '0 0 * * * /sbin/reboot #routermgmt-reboot' >> $C
/etc/init.d/cron restart 2>/dev/null || /etc/init.d/crond restart
echo CRON_OK $(crontab -l | wc -l)
''')
print('[B7]', out.strip()[:80])

# 语法+enable
out = run_gl(gl, r'''
sh -n /usr/bin/routermgmt-agent && sh -n /etc/init.d/mgmt-tunnel && sh -n /etc/init.d/routermgmt && sh -n /etc/routermgmt/self-heal.sh && echo SYNTAX_OK
grep -h respawn /etc/init.d/routermgmt /etc/init.d/mgmt-tunnel
/etc/init.d/routermgmt enable && /etc/init.d/mgmt-tunnel enable && echo ENABLE_OK
''')
if 'SYNTAX_OK' not in out or 'ENABLE_OK' not in out: die('语法/enable 失败')
print('[B8] 语法+enable OK')
gl.close()

# ============ 阶段C：广州+GIA authorized_keys ============
print('=== 阶段C 公钥登记 ===')
keyopts = 'no-agent-forwarding,no-X11-forwarding,no-pty,permitopen="10.10.0.1:8443",permitopen="127.0.0.1:8443",permitlisten="127.0.0.1:%d"' % RSSH
full_line = keyopts + ' ' + DEV_PUB + ' rtr-mgmt-tunnel-' + DEV_NAME.lower().replace(' ', '-')

_, o, _ = gz.exec_command('sudo grep -q "rtr-mgmt-tunnel-%s" /root/.ssh/authorized_keys && echo EXISTS || echo NONE' % DEV_NAME.lower().replace(' ', '-'), timeout=15)
if o.read().decode().strip() == 'EXISTS':
    print('[C] 条目已存在，跳过')
else:
    stdin_, stdout_, _ = gz.exec_command('sudo tee -a /root/.ssh/authorized_keys > /dev/null && echo KEY_ADDED', timeout=15)
    stdin_.write(full_line + '\n'); stdin_.channel.shutdown_write()
    if 'KEY_ADDED' not in stdout_.read().decode(): die('authorized_keys 追加失败')
    print('[C] 公钥已追加')

if not args.skip_gia:
    _, o, _ = gz.exec_command("sshpass -p '57b9bafce096f06cb7603e87' ssh -o StrictHostKeyChecking=no root@154.36.139.155 \"grep -q '%s' /root/.ssh/authorized_keys && echo EXISTS || echo NONE\"" % DEV_NAME.lower().replace(' ', '-'), timeout=45)
    if o.read().decode().strip() == 'EXISTS':
        print('[C-GIA] 条目已存在')
    else:
        stdin_, stdout_, _ = gz.exec_command("sshpass -p '57b9bafce096f06cb7603e87' ssh -o StrictHostKeyChecking=no root@154.36.139.155 \"cat >> /root/.ssh/authorized_keys && echo GIA_ADDED\"", timeout=45)
        stdin_.write(full_line + '\n'); stdin_.channel.shutdown_write()
        if 'GIA_ADDED' not in stdout_.read().decode(): print('[C-GIA] 追加失败（可后续补）')
        else: print('[C-GIA] 公钥已追加')

# ============ 阶段D：就绪验证 ============
print('=== 阶段D 就绪验证 ===')
_, o, _ = gz.exec_command(r'''
sudo grep -c "rtr-mgmt-tunnel-%s" /root/.ssh/authorized_keys
sudo grep "rtr-mgmt-tunnel-%s" /root/.ssh/authorized_keys | grep -oE 'permitlisten="[^"]*"'
''' % (DEV_NAME.lower().replace(' ', '-'), DEV_NAME.lower().replace(' ', '-')), timeout=15)
print('[D1] 广州侧:', o.read().decode().split())

gl2 = gl_connect(GL_HOST)
_, o, _ = gl2.exec_command(r'''
echo device_id=$(grep ^DEVICE_ID /etc/routermgmt/agent.conf | cut -d= -f2)
echo tunnel_host=$(grep ^VPS_HOST /etc/routermgmt/tunnel.conf | cut -d= -f2)
echo rssh=$(grep ^RSSH_REMOTE_PORT /etc/routermgmt/tunnel.conf | cut -d= -f2)
echo services=$(ls /etc/rc.d/ 2>/dev/null | grep -cE 'routermgmt|mgmt-tunnel')
echo cron_count=$(crontab -l | wc -l)
''', timeout=20)
print('[D2] 设备侧:'); print(o.read().decode())
gl2.close()
gz.close()

print('=== 完成 ===')
print('DEVICE_ID=%s RSSH=%d' % (devid, RSSH))
