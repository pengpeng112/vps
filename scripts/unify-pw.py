# SN-R 系列批量改密工具（统一口令 [REDACTED]）
# 用法：接入一台已刷机设备（PC 网线接其 LAN 口）后运行：
#   python unify-pw.py
# 它会：用旧口令（自动尝试各台历史口令）登录 → 改为统一口令 → 验证 → 更新对应凭证档案
import subprocess, paramiko, re, sys, glob, time

UNIFIED = "[REDACTED]"
HOST = "192.168.132.1"
# 各台历史口令（从凭证文件自动读取 + 兜底列表）
def old_passwords():
    pws = []
    for f in [r"F:\vps\backups\.new-snr-pw", r"F:\vps\backups\.snr3-pw", r"F:\vps\backups\.snr4-pw"]:
        try: pws.append(open(f).read().splitlines()[0])
        except: pass
    return pws

def main():
    r = subprocess.run(["openssl", "passwd", "-1", "-salt", "snruni", UNIFIED], capture_output=True, text=True)
    h = r.stdout.strip()
    for pw in old_passwords() + [UNIFIED]:
        try:
            c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            c.connect(HOST, username="root", password=pw, timeout=6, allow_agent=False, look_for_keys=False)
        except Exception:
            continue
        _, o, _ = c.exec_command("grep DEVICE_ID /etc/routermgmt/agent.conf", timeout=10)
        devid = (o.read().decode(errors="replace").strip().split("=") or ["?"])[-1]
        c.exec_command(f"sed -i 's|^root:[^:]*:|root:{h}:|' /etc/shadow", timeout=10)[1].read()
        c.close()
        time.sleep(1)
        # 验证
        c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        c.connect(HOST, username="root", password=UNIFIED, timeout=6, allow_agent=False, look_for_keys=False)
        _, o, _ = c.exec_command("echo OK", timeout=10)
        assert "OK" in o.read().decode(errors="replace")
        c.close()
        print(f"✅ {devid} 已统一口令（旧口令 {pw[:4]}... 作废）")
        print("提示：请把对应凭证档案的 SSH 行更新为统一口令")
        return
    print("❌ 所有已知口令均被拒——该设备可能未刷机或口令未知")

if __name__ == "__main__":
    main()
