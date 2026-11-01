#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SN-R 批量并行刷机驱动（2026-10-13 固化）
用法：
  python flash-batch.py            # 扫描两网段的出厂态设备并全部并行刷机
  python flash-batch.py --detect   # 只探测不刷机
  python flash-batch.py --auto     # cron 无人值守：无设备则静默退出
发现条件：telnet(23) 开放 + root/abc@1234 可登 + MAC 不在现役清单（防误刷现役设备）。
并行：每台一线程跑 flash-snr.py --host X --num N --rssh R（端口由本驱动预分配防竞争）。
"""
import argparse, json, os, re, socket, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor

REPO = r"F:\vps"
LOCK = REPO + r"\backups\.flash-batch.lock"
SUBNETS = ["192.168.132", "192.168.2"]
FACTORY_PW = "abc@1234"
# 现役/已知设备 MAC（含 WiFi 与 pin 段）——扫描命中即排除，绝不误刷
KNOWN_MAC_PREFIXES = ["a4:d8:ca:14:", "76:a7:c8:e0:4f:"]  # 兜底前缀（真实比对用精确集合）
_EXACT_MACS = set()

def fleet_exact_macs():
    try:
        r = subprocess.run(['ssh','-p','28722','-i','C:/Users/Administrator/.ssh/id_rsa_dmit','-o','IdentitiesOnly=yes',
                            'root@179.255.137.64',
                            'docker exec routermgmt-db-1 psql -U postgres -d routermgmt -t -c "SELECT lower(mac_addr) FROM devices WHERE mac_addr IS NOT NULL"'],
                           capture_output=True, text=True, timeout=30)
        return {x.strip() for x in r.stdout.split() if ":" in x}
    except Exception:
        print("平台 MAC 拉取异常，返回空集（主循环将中止）")
        return set()

def sweep(subnet, port=23, timeout=1):
    """banner 即时校验版（2026-10-14 multi-review）：本机代理 TUN 会假接受任意 SYN 再中止
    （WinError 10053 特征）——真 telnet 服务秒回 banner，假命中无数据，据此过滤。"""
    hits = []
    def probe(ip):
        try:
            s = socket.create_connection((ip, port), timeout)
            try:
                s.settimeout(1.2)
                data = s.recv(64)
                if data:
                    hits.append(ip)
            except OSError:
                pass
            finally:
                s.close()
        except OSError:
            pass
    with ThreadPoolExecutor(max_workers=64) as ex:
        list(ex.map(probe, [f"{subnet}.{i}" for i in range(2, 255)]))
    return sorted(hits)


def telnet_fingerprint(ip):
    """出厂态验证+取指纹；返回 (is_factory, info)"""
    try:
        s = socket.socket(); s.settimeout(5)
        s.connect((ip, 23)); time.sleep(1.2)
        try: s.recv(4096)
        except OSError: pass
        def until(p, t=5):
            t0 = time.time(); acc = b""
            while time.time() - t0 < t:
                try:
                    d = s.recv(4096)
                    if d: acc += d
                except socket.timeout: pass
                if re.search(p, acc.decode(errors="replace")): return acc.decode(errors="replace")
            return acc.decode(errors="replace")
        until(r"login:"); s.sendall(b"root\n"); time.sleep(0.4); until(r"assword:")
        s.sendall(FACTORY_PW.encode() + b"\n")
        r = until(r"#", 5)
        if "#" not in r:
            s.close(); return False, "telnet登录失败(非出厂口令)"
        def cmd(c):
            s.sendall(c.encode() + b"\n"); time.sleep(0.6)
            return until(r"#", 4)
        macs = cmd("for I in wan ra0; do [ -d /sys/class/net/$I ] && echo $I $(cat /sys/class/net/$I/address); done")
        model = cmd("cat /tmp/sysinfo/model 2>/dev/null | head -1")
        s.close()
        m = dict(re.findall(r"(wan|ra0)\s+([0-9a-f:]+)", macs))
        m = {k.lower(): v.lower() for k, v in m.items()}
        _ml = [l for l in (model or "").splitlines() if ("MT7981" in l or "AX3000" in l or "MediaTek" in l)]
        _model = _ml[0].strip() if _ml else "?"
        info = f"model={_model} wan={m.get('wan','?')} ra0={m.get('ra0','?')}"
        if "MT7981" not in _model and "AX3000" not in _model:
            return False, f"型号不符({_model})——排除（仅刷 JCG AX3000/MT7981）"
        if not m.get("wan"):
            return False, "未取到 WAN MAC——排除（指纹不完整不刷）"
        if _EXACT_MACS:
            if m.get("wan") in _EXACT_MACS or m.get("ra0") in _EXACT_MACS:
                return False, "现役精确MAC匹配——排除 " + info
        else:
            for pfx in KNOWN_MAC_PREFIXES:
                if (m.get("wan") or "").startswith(pfx) or (m.get("ra0") or "").startswith(pfx):
                    return False, f"现役前缀MAC匹配({pfx})——排除 " + info
        return True, info
    except Exception as e:
        return False, f"探测异常: {e}"

def next_ports(n, start=None):
    """从 DMIT authorized_keys+监听取当前最大 132xx，预分配 n 个连续端口"""
    r = subprocess.run(['ssh','-p','28722','-i','C:/Users/Administrator/.ssh/id_rsa_dmit','-o','IdentitiesOnly=yes',
                        'root@179.255.137.64',
                        "(grep -oE 'permitlisten=\"127.0.0.1:132[0-9]{2}\"' /root/.ssh/authorized_keys; ss -tln | grep -oE '127.0.0.1:132[0-9]{2}') | grep -oE '132[0-9]{2}' | sort -n | tail -1"],
                       capture_output=True, text=True, timeout=30)
    if r.returncode != 0 or not r.stdout.strip():
        print(f"端口分配查询失败（rc={r.returncode}）——中止而非猜测（V9）"); sys.exit(6)
    mx = int(r.stdout.strip())
    base = max(mx + 1, int(start) if start else 13230)
    _ports = [base + i for i in range(n)]
    if _ports[-1] >= 13300:
        print(f"端口池越界（{_ports[-1]}>=13300）——中止（V9）"); sys.exit(6)
    return _ports

def next_nums(n):
    """平台现有 SN-R* 最大序号 +1 起取 n 个"""
    r = subprocess.run(['ssh','-p','28722','-i','C:/Users/Administrator/.ssh/id_rsa_dmit','-o','IdentitiesOnly=yes',
                        'root@179.255.137.64',
                        'docker exec routermgmt-db-1 psql -U postgres -d routermgmt -t -c "SELECT name FROM devices WHERE name LIKE \'SN-R%\'"'],
                       capture_output=True, text=True, timeout=30)
    if r.returncode != 0 or not r.stdout.strip():
        print(f"编号分配查询失败（rc={r.returncode}）——中止而非猜测（V9）"); sys.exit(6)
    nums = [int(mm.group(1)) for mm in re.finditer(r"SN-R(\d+)", r.stdout)]
    if not nums:
        print("平台无 SN-R 记录（异常态）——中止（V9）"); sys.exit(6)
    base = max(nums) + 1
    return list(range(base, base + n))

ATTEMPTS = os.path.join(REPO, "backups", ".flash-attempts.json")

def load_attempts():
    try: return json.load(open(ATTEMPTS, encoding="utf-8"))
    except Exception: return {}

def save_attempts(d):
    json.dump(d, open(ATTEMPTS, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

def probe_halfdone(ips):
    import paramiko
    half = []
    for ip in ips:
        try:
            c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            c.connect(ip, username="root", password="[REDACTED]", timeout=6, allow_agent=False, look_for_keys=False)
            _, o, _ = c.exec_command("ls /etc/routermgmt/agent.conf /etc/routermgmt/tunnel.conf 2>/dev/null | wc -l", timeout=10)
            n = o.read().decode().strip()
            _, o2, _ = c.exec_command("grep -c DEVICE_ID /etc/routermgmt/agent.conf 2>/dev/null", timeout=10)
            has_id = o2.read().decode().strip()
            c.close()
            if n != "2" or has_id != "1":
                half.append((ip, f"栈不完整(conf={n}/身份={has_id})"))
        except Exception:
            pass
    return half

def flash_one(ip, num, rssh, log):
    try:
        with open(log, "w", encoding="utf-8") as f:
            p = subprocess.run([sys.executable, "-u", os.path.join(REPO, "scripts", "flash-snr.py"),
                                "--num", str(num), "--rssh", str(rssh), "--host", ip],
                               stdout=f, stderr=subprocess.STDOUT, timeout=1800, cwd=os.path.join(REPO, "scripts"))
        out = open(log, encoding="utf-8", errors="replace").read()
        tail = [l for l in out.splitlines() if l.strip()][-3:]
        return p.returncode == 0, num, ip, rssh, " | ".join(tail)
    except subprocess.TimeoutExpired:
        return False, num, ip, rssh, f"超时(30分钟) 日志{log}"
    except Exception as e:
        return False, num, ip, rssh, f"异常 {type(e).__name__}: {e}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--detect", action="store_true")
    ap.add_argument("--auto", action="store_true", help="无人值守：无设备静默退出")
    ap.add_argument("--max", type=int, default=4, help="单轮最多刷机台数")
    ap.add_argument("--skip-reboot-verify", action="store_true", help="跳过刷机后的受控重启复验（默认做）")
    args = ap.parse_args()

    # 进程锁（V16：O_CREAT|O_EXCL 原子互斥；90 分钟遗留兜底）
    try:
        _fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(_fd, str(time.time()).encode()); os.close(_fd)
    except FileExistsError:
        age = time.time() - os.path.getmtime(LOCK)
        if age < 5400:
            print(f"上一轮刷机仍在进行（锁 {int(age/60)} 分钟前），本轮退出")
            return
        print(f"⚠️ 检测到遗留刷机锁（{int(age/60)} 分钟）——上轮可能挂起，自动解锁续扫（详见 backups/.flash-N*.log）")
        os.remove(LOCK)
        _fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(_fd, str(time.time()).encode()); os.close(_fd)

    global _EXACT_MACS
    _EXACT_MACS = fleet_exact_macs()
    if not _EXACT_MACS:
        print("❌ 现役精确 MAC 集合为空（平台查询失败）——中止自动刷机（D9）")
        if args.auto: return
        sys.exit(6)
    print(f"现役精确 MAC 集合: {len(_EXACT_MACS)} 条")
    print(f"[{time.strftime('%F %T')}] 扫描出厂态设备（{', '.join(SUBNETS)}.0/24，banner 校验过滤本机 TUN 假阳性）...")
    found = []
    try:
        for sn in SUBNETS:
            for ip in sweep(sn):
                isf, info = telnet_fingerprint(ip)
                print(f"  {ip}: {'✓出厂态' if isf else '跳过'} — {info}")
                if isf: found.append(ip)
    finally:
        if not found or args.detect:
            os.remove(LOCK)
    if not found:
        print("未发现出厂态设备" + ("（auto 静默退出）" if args.auto else "——请确认设备已上电且 WAN/LAN 已接入两网段之一"))
        return
    if args.detect:
        print(f"共 {len(found)} 台待刷: {found}")
        return
    found = found[:args.max]
    ports = next_ports(len(found))
    nums = next_nums(len(found))
    # F4：历史半成品探测（失败机 telnet 已关不会被扫描覆盖——显著上报防静默丢失）
    _att = load_attempts()
    _hist = [ip for ip, v in _att.items() if v.get("status") == "incomplete"]
    if _hist:
        for ip, why in probe_halfdone(_hist):
            print(f"⚠️ 半成品待续（需按 SOP §五 检查点续跑）: {ip} — {why}")
    print(f"刷机分配: {list(zip(found, nums, ports))}（并行）")
    for ip, num, prt in zip(found, nums, ports):
        _att[ip] = {"num": num, "rssh": prt, "status": "incomplete"}
    save_attempts(_att)
    jobs = [(ip, nums[i], ports[i], REPO + rf"\backups\.flash-N{nums[i]}.log") for i, ip in enumerate(found)]
    results = []
    with ThreadPoolExecutor(max_workers=len(jobs)) as ex:
        futs = [ex.submit(flash_one, *j) for j in jobs]
        for f in futs:
            results.append(f.result())
    _att = load_attempts()
    for ok, num, ip, rssh, tail in results:
        if ok and ip in _att: _att[ip]["status"] = "done"
    save_attempts(_att)
    okdevs = [(num, ip, rssh) for ok, num, ip, rssh, _ in results if ok]
    f11_fail = []
    if okdevs and not args.detect and not args.skip_reboot_verify:
        print(f"受控重启复验（{len(okdevs)} 台）…")
        import paramiko, time as _t
        for num, ip, rssh in okdevs:
            try:
                c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                c.connect(ip, username="root", password="[REDACTED]", timeout=8, allow_agent=False, look_for_keys=False)
                c.exec_command("nohup sh -c 'sleep 2; reboot' >/dev/null 2>&1 &"); c.close()
                print(f"  SN-R{num} 重启指令已发"); _t.sleep(20)
            except Exception as e:
                print(f"  SN-R{num} 重启指令失败: {e}")
        print("  等 180 秒回线复验…"); _t.sleep(180)
        for num, ip, rssh in okdevs:
            try:
                # 2026-10-14 multi-review r2-修（codex#22）：①ssh 补 -p 28722（原走 22，入口不一致）；
                # ②设备命令在 DMIT 层以单引号包裹，内部模式一律双引号——原 grep -E '…' 的内层单引号
                # 会在 DMIT 层提前闭合引号、| 落到外层被解释。③解析正则 \w→\w+（多字符键 X/XL/RC/CG）。
                inner = ("umask 077; printf '%s' '[REDACTED]' > /tmp/.p; sshpass -f /tmp/.p ssh -p " + str(rssh) +
                         " -o StrictHostKeyChecking=no -o ConnectTimeout=8 root@127.0.0.1 '" +
                         "echo T=$(ps w | grep id_dropbear_mgmt | grep -v grep | wc -l);" +
                         "echo H=$(curl -sk -m 6 -o /dev/null -w %{http_code} https://127.0.0.1:18443/api/v1/health);" +
                         "echo Q=$(iptables -S FORWARD | grep 443 | head -1 | grep -c REJECT);" +
                         "echo M=$(cat /sys/class/net/wan/address);" +
                         "echo C=$(grep -c routermgmt /etc/crontabs/root);" +
                         "echo P=$(ps w | grep -E \"by_agen[t]|zco[n]|rathol[e]\" | grep -v grep | wc -l);" +
                         "echo X=$(grep -c \"^exit 0\" /etc/rc.local);" +
                         "echo XL=$(tail -1 /etc/rc.local | grep -c \"exit 0\");" +
                         "echo RC=$(grep -c routermgmt-reboot /etc/crontabs/root);" +
                         "echo CG=$(ls /www/cgi-bin/ 2>/dev/null | grep -cv luci); rm -f /tmp/.p'")
                r2 = subprocess.run(["ssh","-p","28722","-i","C:/Users/Administrator/.ssh/id_rsa_dmit","-o","IdentitiesOnly=yes",
                                     "root@179.255.137.64", inner], capture_output=True, text=True, timeout=60)
                kv = dict(__import__("re").findall(r"(\w+)=(.*)", r2.stdout))
                mac_e = f"76:a7:c8:e0:4f:{0x20+num:02x}"
                good = (kv.get("T")=="5" and kv.get("H")=="200" and kv.get("Q")=="1"
                        and kv.get("M")==mac_e and kv.get("C")=="4" and kv.get("P")=="0"
                        and kv.get("X")=="1" and kv.get("XL")=="1" and kv.get("RC")=="1" and kv.get("CG")=="0")
                print(f"  {'PASS' if good else 'FAIL'} SN-R{num} 复验: 隧道{kv.get('T')} 健康{kv.get('H')} QUIC{kv.get('Q')} MAC={kv.get('M')} cron{kv.get('C')} PCDN{kv.get('P')} exit0={kv.get('X')}/{kv.get('XL')} 重启cron={kv.get('RC')} cgi余={kv.get('CG')}")
                if not good: f11_fail.append(num)
            except Exception as e:
                print(f"  FAIL SN-R{num} 复验异常: {e}"); f11_fail.append(num)
    elif okdevs and not args.detect:
        print("  ⚠️ 跳过受控重启复验（--skip-reboot_verify）——按 r2-修口径此时仅为『配置完成』，不算交付")
    print(f"\n===== 批量结果 =====")
    for ok, num, ip, rssh, tail in results:
        print(f"  {'✅' if ok else '❌'} SN-R{num} @ {ip} (RSSH {rssh}): {tail}")
    _fails = sum(0 if ok else 1 for ok, *_ in results)
    # 2026-10-14 multi-review r2-修（codex#21）：重启复验失败必须阻断成功——原仅打印 FAIL，
    # 最终退出码只看刷机阶段，"重启后失联/PCDN 复活"也能报成功。
    if f11_fail:
        print(f"  ❌ 受控重启复验未过: {f11_fail}")
        _fails += len(f11_fail)
    try: os.remove(LOCK)
    except OSError: pass
    if _fails: sys.exit(1)  # V16：有失败非零退出

if __name__ == "__main__":
    main()
