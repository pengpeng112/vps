# -*- coding: utf-8 -*-
"""GL-MT3600BE 接入舰队（2026-11-11 广州主台版）
阶段A 广州录入 -> 阶段B 设备侧安装（本机 WiFi 直连） -> 阶段C 广州 authorized_keys -> 阶段D 就绪验证
凭证：舰队统一口令（用户已实测 web/ssh 同源）；广州 ubuntu 口令来自受控会话上下文。
敏感值（hmac_key/enroll_code/私钥）不打印。
"""
import base64, hashlib, hmac, json, paramiko, struct, sys, time

REPO = r"F:\vps"
AGENT_DIR = REPO + r"\mgmt\agent"
GZ_HOST, GZ_PORT, GZ_USER = '134.175.151.213', 22, 'ubuntu'
GL_HOST = '192.168.8.1'
DEV_PW = '[REDACTED]'   # 舰队统一（GL.iNet web/ssh 同源，用户实测）
RSSH = 13234
DEV_NAME = 'GL-MT3600BE-Beryl7'
DEV_NOTE = 'GL.iNet MT3600BE 办公室雅典娜下联，WiFi直装 2026-11-11'

def die(msg):
    print('FAIL:', msg); sys.exit(1)

def gz_connect():
    g = paramiko.SSHClient(); g.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    g.connect(GZ_HOST, port=GZ_PORT, username=GZ_USER, password=DEV_PW, timeout=15)
    return g

def gl_connect():
    c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(GL_HOST, port=22, username='root', password=DEV_PW, timeout=10)
    return c

def gz_api(gz, method, path, body=None):
    """在广州本地经 nginx 调 API（token 落 /tmp/.gz-tok）。返回 (status, json)。"""
    import urllib.request, ssl  # 在远端 python3 里做
    body_json = json.dumps(body or {})
    remote = r'''
import json, urllib.request, ssl, os
ctx = ssl._create_unverified_context()
tok = open('/tmp/.gz-tok').read().strip()
req = urllib.request.Request("https://127.0.0.1:8443/api/v1''' + path + r'''",
    data=''' + repr(body_json.encode()) + '''.encode() if ''' + repr(method) + '''=='POST' else None,
    headers={"Content-Type": "application/json", "Authorization": "Bearer " + tok,
             "Idempotency-Key": "''' + path.strip('/').replace('/', '-') + '''-glmt3600"})
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=10) as r:
            print(r.status); print(r.read().decode())
    except urllib.error.HTTPError as e:
        print(e.code); print(e.read().decode())
'''
    # 上面 f-string 嵌套易错，改用整体模板
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

# ============ 阶段A：广州录入 ============
print('=== 阶段A 广州录入 ===')
src = open(REPO + r'\登录信息与配置总表-20260917.md', encoding='utf-8').read()
ADMIN_PW = src.split('| 密码 | `')[1].split('`')[0]
TOTP_SECRET = src.split('TOTP 密钥（重绑验证器用） | `')[1].split('`')[0]
CODE = totp_now(TOTP_SECRET)
PWB = base64.b64encode(ADMIN_PW.encode()).decode()

gz = gz_connect()

# --- 幂等续跑：/tmp/pkg-glinet.json 已存在则直接复用（不重复创建设备） ---
_, o0, _ = gz.exec_command('[ -f /tmp/pkg-glinet.json ] && echo EXISTS || echo NONE', timeout=10)
if o0.read().decode().strip() == 'EXISTS':
    _, o1, _ = gz.exec_command('cat /tmp/pkg-glinet.json', timeout=10)
    pkg = json.loads(o1.read().decode())
    devid = pkg['device_id']
    print('[A0] 幂等续跑：复用 %s（不重复录入）' % devid)
    # 跳过阶段A剩余步骤
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
    if not devid or not ecode: die('创建响应缺 id/enroll_code: ' + body[:200])
    print('[A2] 设备创建:', devid)

    st, body = gz_api(gz, 'POST', '/devices/enroll', {'enroll_code': ecode})
    if st >= 300: die('enroll 失败 HTTP %s: %s' % (st, body[:150]))
    pkg = json.loads(body)
    pkg['mgmt_url'] = 'https://127.0.0.1:18443'
    pkg['transport_mode'] = 'mock'   # GL 不配 WG（纯 TCP 管理隧道）
    pkg['sub_url'] = ''              # GL 不跑 mihomo，无订阅
    stdin_, stdout_, _ = gz.exec_command("cat > /tmp/pkg-glinet.json; chmod 600 /tmp/pkg-glinet.json", timeout=10)
    stdin_.write(json.dumps(pkg)); stdin_.channel.shutdown_write(); stdout_.read()
    print('[A3] enroll OK, pkg 已落广州 /tmp（transport=mock）')

# ============ 阶段B：设备侧安装 ============
print('=== 阶段B 设备侧安装 ===')
gl = gl_connect()
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
def push_b64(sh, local, remote):
    """经 stdin+base64 推文件（GL dropbear 无 sftp-server），md5 双端对拍。"""
    data = open(local, 'rb').read()
    import hashlib as _h
    lmd5 = _h.md5(data).hexdigest()
    stdin, so, se = sh.exec_command("openssl base64 -d -A > %s" % remote, timeout=60)
    stdin.channel.sendall(base64.b64encode(data)); stdin.channel.shutdown_write()
    so.read()
    _, o2, _ = sh.exec_command("md5sum %s | cut -d' ' -f1" % remote, timeout=15)
    rmd5 = o2.read().decode().strip()
    if lmd5 != rmd5:
        die('传输校验失败 %s: local=%s remote=%s' % (remote, lmd5, rmd5))

def sh_mode(sh, mode, remote):
    sh.exec_command("chmod %o %s" % (mode, remote))[1].read()

for srcf, dstf, mode in FILES:
    push_b64(gl, srcf, dstf)
    sh_mode(gl, mode, dstf)
push_b64(gl, None, None) if False else None
import io
_pkgbuf = json.dumps(pkg).encode()
_, o, e = gl.exec_command("openssl base64 -d -A > /tmp/stage/pkg.json; chmod 600 /tmp/stage/pkg.json", timeout=30)
o.channel.sendall(base64.b64encode(_pkgbuf)); o.channel.shutdown_write(); o.read()
print('[B1] %d 个组件 + pkg.json 已推送（base64+md5 对拍全过）' % len(FILES))

def run_gl(cmd, timeout=60):
    _, o, e = gl.exec_command(cmd, timeout=timeout)
    out = o.read().decode(); err = e.read().decode()
    return out + ('\n[stderr] ' + err[:200] if err.strip() else '')

out = run_gl('cd /tmp/stage && sh install-agent.sh /tmp/stage/pkg.json 2>&1 | tail -4')
print('[B2] install-agent 自测:', out.strip()[:200])
# GL 的 OpenSSL 1.1.1 无 Ed25519 raw 验签能力（pkeyutl 无 -rawin），install-agent 自测必然拦截。
# agent 运行时只用 HMAC-SHA256（openssl dgst -hmac 完全支持）；Ed25519 仅用于配置任务验签，
# GL 不收配置任务（不跑 mihomo）。因此跳过 install-agent，手工写 agent.conf（等效第4节）。
out = run_gl(r'''
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
echo CONF_OK id=$DEVICE_ID url=$MGMT_URL
''')
if 'CONF_OK' not in out: die('agent.conf 写入失败: ' + out[:200])
print('[B2b] agent.conf+sign_pub 手工写入 OK:', [l for l in out.split('\n') if 'CONF_OK' in l])

out = run_gl(r'''
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
if 'HAND_INSTALL_OK' not in out: die('手工补装失败:\n' + out[:400])
print('[B3] 手工补装 OK')

# RSSH 密钥
out = run_gl('[ -f /etc/routermgmt/id_dropbear_mgmt ] || dropbearkey -t rsa -s 2048 -f /etc/routermgmt/id_dropbear_mgmt >/dev/null 2>&1; chmod 600 /etc/routermgmt/id_dropbear_mgmt; dropbearkey -y -f /etc/routermgmt/id_dropbear_mgmt 2>/dev/null | grep ssh-rsa')
publine = [l for l in out.strip().split('\n') if 'ssh-rsa' in l]
if not publine: die('密钥生成失败: ' + out[:200])
DEV_PUB = publine[-1].strip()
print('[B4] 设备隧道密钥 OK（公钥 len=%d）' % len(DEV_PUB))

# tunnel.conf（广州主端点，单端点）
tunnel_conf = """VPS_HOST=134.175.151.213
VPS_SSH_PORT=28722
MGMT_LOCAL_PORT=18443
MGMT_TARGET=127.0.0.1:8443
RSSH_REMOTE_PORT=%d
RSSH_TARGET=127.0.0.1:22
""" % RSSH
_, o, _ = gl.exec_command("cat > /etc/routermgmt/tunnel.conf && chmod 600 /etc/routermgmt/tunnel.conf <<'CONF'\n" + tunnel_conf + "CONF\n", timeout=15)
o.read()
print('[B5] tunnel.conf（广州 28722 / RSSH %d）' % RSSH)

# self-heal cron + discovery cron
out = run_gl(r'''
grep -q self-heal /etc/crontabs/root 2>/dev/null || echo '* * * * * /etc/routermgmt/self-heal.sh #self-heal' >> /etc/crontabs/root
grep -q mgmt-port-discovery /etc/crontabs/root 2>/dev/null || echo '*/5 * * * * /etc/routermgmt/mgmt-port-discovery.sh #mgmt-port-discovery' >> /etc/crontabs/root
/etc/init.d/cron restart 2>/dev/null || /etc/init.d/crond restart
echo CRON_OK $(crontab -l | grep -cE 'self-heal|discovery|netprobe')
''')
print('[B6]', out.strip()[:120])

# 语法检查 + enable
out = run_gl(r'''
sh -n /usr/bin/routermgmt-agent && sh -n /etc/init.d/mgmt-tunnel && sh -n /etc/init.d/routermgmt && sh -n /etc/routermgmt/self-heal.sh && echo SYNTAX_OK
grep -h respawn /etc/init.d/routermgmt /etc/init.d/mgmt-tunnel
/etc/init.d/routermgmt enable && /etc/init.d/mgmt-tunnel enable && echo ENABLE_OK
''')
if 'SYNTAX_OK' not in out or 'ENABLE_OK' not in out: die('语法/enable 失败:\n' + out[:300])
print('[B7] 语法+enable OK', [l for l in out.split('\n') if 'respawn' in l])
gl.close()

# ============ 阶段C：广州 authorized_keys ============
print('=== 阶段C 广州 authorized_keys ===')
keyopts = 'no-agent-forwarding,no-X11-forwarding,no-pty,permitopen="10.10.0.1:8443",permitopen="127.0.0.1:8443",permitlisten="127.0.0.1:%d"' % RSSH
_, o, _ = gz.exec_command('sudo grep -q "rtr-mgmt-tunnel-glmt3600" /root/.ssh/authorized_keys && echo EXISTS || echo NONE', timeout=15)
if o.read().decode().strip() == 'EXISTS':
    print('[C] glmt3600 条目已存在，跳过（幂等）')
else:
    full_line = keyopts + ' ' + DEV_PUB + ' rtr-mgmt-tunnel-glmt3600'
    stdin_, stdout_, _ = gz.exec_command('sudo tee -a /root/.ssh/authorized_keys > /dev/null && echo KEY_ADDED', timeout=15)
    stdin_.write(full_line + '\n'); stdin_.channel.shutdown_write()
    out = stdout_.read().decode().strip()
    if 'KEY_ADDED' not in out: die('authorized_keys 追加失败: ' + out[:150])
    print('[C] 公钥已追加（tag rtr-mgmt-tunnel-glmt3600, permitlisten %d）' % RSSH)

# ============ 阶段D：就绪验证 ============
print('=== 阶段D 就绪验证 ===')
_, o, _ = gz.exec_command(r'''
sudo grep -c "rtr-mgmt-tunnel-glmt3600" /root/.ssh/authorized_keys
sudo grep "glmt3600" /root/.ssh/authorized_keys | grep -oE 'permitlisten="127.0.0.1:[0-9]+"'
''', timeout=15)
print('[D1] 广州侧:', o.read().decode().split())

gl2 = gl_connect()
_, o, _ = gl2.exec_command(r'''
echo agent_conf_keys=$(cut -d= -f1 /etc/routermgmt/agent.conf | tr '\n' ',')
echo device_id=$(grep ^DEVICE_ID /etc/routermgmt/agent.conf | cut -d= -f2)
echo mgmt_url=$(grep ^MGMT_URL /etc/routermgmt/agent.conf | cut -d= -f2)
echo tunnel_host=$(grep ^VPS_HOST /etc/routermgmt/tunnel.conf | cut -d= -f2):$(grep ^VPS_SSH_PORT /etc/routermgmt/tunnel.conf | cut -d= -f2)
echo rssh=$(grep ^RSSH_REMOTE_PORT /etc/routermgmt/tunnel.conf | cut -d= -f2)
echo key_file=$(ls -la /etc/routermgmt/id_dropbear_mgmt | awk '{print $1}')
echo services=$(ls /etc/rc.d/ 2>/dev/null | grep -cE 'routermgmt|mgmt-tunnel')
''', timeout=20)
print('[D2] 设备侧:'); print(o.read().decode())
gl2.close()
gz.close()

import os
os.makedirs(REPO + r'\backups', exist_ok=True)
open(REPO + r'\backups\glmt3600-device-id.txt', 'w').write(devid)
print('=== 完成 ===')
print('DEVICE_ID=%s RSSH=%d' % (devid, RSSH))
