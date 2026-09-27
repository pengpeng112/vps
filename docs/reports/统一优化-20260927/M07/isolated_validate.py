"""M07 隧道降权模式隔离验证（本地 Docker openssh 容器 m07-sshd，非生产）。
模式：authorized_keys restrict,port-forwarding,permitlisten=本机口,permitopen=目标 + shell=/sbin/nologin + ssh -N
正/负向：允许口 -R 可建；未授权口 -R 被拒；shell 拒；permitopen 未授权 -L 拒。
前置：%TEMP%/m07-ssh 已有 t/t.pub；容器 m07-sshd 在 127.0.0.1:22222。
"""
import os, subprocess, tempfile, time

TMP = os.path.join(tempfile.gettempdir(), 'm07-ssh')

def sh(cmd, timeout=60):
    return subprocess.run(cmd, shell=True, capture_output=True, timeout=timeout,
                          encoding='utf-8', errors='replace')

def docker_exec(c, timeout=30):
    return subprocess.run(['docker', 'exec', 'm07-sshd', 'sh', '-c', c],
                          capture_output=True, timeout=timeout,
                          encoding='utf-8', errors='replace')

PUB = open(os.path.join(TMP, 't.pub')).read().strip()
AK = 'restrict,port-forwarding,permitlisten="127.0.0.1:18222",permitopen="127.0.0.1:9999" ' + PUB + '\n'
akf = os.path.join(tempfile.gettempdir(), 'm07_ak')
open(akf, 'w', newline='\n').write(AK)
r = sh(f'docker cp "{akf}" m07-sshd:/home/rtr-tunnel/.ssh/authorized_keys')
assert r.returncode == 0, r.stderr
r = docker_exec('chown rtr-tunnel:rtr-tunnel /home/rtr-tunnel/.ssh/authorized_keys && chmod 600 /home/rtr-tunnel/.ssh/authorized_keys && echo AK_SET')
assert 'AK_SET' in r.stdout, r.stderr
print('authorized_keys 安装完成（restrict/permitlisten:18222/permitopen:9999）')

KEY = os.path.join(TMP, 't').replace('\\', '/')
SSH_BASE = (f'ssh -i "{KEY}" -p 22222 -o StrictHostKeyChecking=no '
            '-o UserKnownHostsFile=/dev/null -o BatchMode=yes -o ConnectTimeout=5 rtr-tunnel@127.0.0.1')

def try_forward(extra, name):
    p = subprocess.Popen(f'{SSH_BASE} -N -o ExitOnForwardFailure=yes {extra}', shell=True,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         encoding='utf-8', errors='replace')
    time.sleep(3)
    alive = p.poll() is None
    err = ''
    if not alive:
        lines = (p.stderr.read() or '').strip().splitlines()
        err = lines[-1][:110] if lines else ''
    else:
        p.terminate(); p.wait(5)
    print(f'[{name}] alive={alive} {err}')
    return alive

ok1 = try_forward('-R 127.0.0.1:18222:127.0.0.1:22', '正向 R:18222(permitlisten 允许)')
assert ok1, '允许口失败'
ok2 = try_forward('-R 127.0.0.1:13222:127.0.0.1:22', '负向 R:13222(未授权监听口)')
assert not ok2, '未授权监听口未被拒绝！'
r3 = sh(f'{SSH_BASE} whoami')
print(f'[负向 shell] rc={r3.returncode} {(r3.stderr or r3.stdout).strip()[:90]}')
assert r3.returncode != 0, 'shell 未被拒绝！'
# 容器内 9999 起监听用于穿透验证
docker_exec('nc -l -p 9999 > /tmp/marker 2>/dev/null &', timeout=10)
import socket
def through(local_port, name):
    try:
        s = socket.create_connection(('127.0.0.1', local_port), timeout=6)
        s.settimeout(6)
        s.sendall(b'ping\n')
        data = s.recv(32)
        s.close()
        print(f'[{name}] 穿透成功 data={data!r}')
        return True
    except Exception as e:
        print(f'[{name}] 穿透失败 {type(e).__name__}')
        return False
# permitopen 允许口：-L 建立后应能穿透到容器 9999
p1 = subprocess.Popen(f'{SSH_BASE} -N -L 127.0.0.1:18888:127.0.0.1:9999', shell=True,
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(3)
ok4 = through(18888, '正向 L 穿透 ->9999(允许)')
p1.terminate(); p1.wait(5)
# permitopen 未授权口：本地监听在，但穿隧道被 sshd 拒绝
p2 = subprocess.Popen(f'{SSH_BASE} -N -L 127.0.0.1:18889:127.0.0.1:8443', shell=True,
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(3)
ok5 = through(18889, '负向 L 穿透 ->8443(未授权)')
p2.terminate(); p2.wait(5)
assert ok4, '允许口穿透失败'
assert not ok5, 'permitopen 未生效（未授权口穿透成功）！'
print('PASS: restrict/permitlisten/permitopen/nologin/-N 模式全部符合预期')
