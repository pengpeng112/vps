#!/usr/bin/env python3
# 在 TX (ubuntu@43.134.46.44) 上执行：给 sing-box 增加 hysteria2 UDP/443 入站。
# 用法: sudo HY2_PW='<密码>' python3 tx_patch.py3 apply|rollback|status
# 设计: 只增不改现有 vless 入站；备份带时间戳；check 通过才 restart；失败自动回读备份。
import json, os, subprocess, sys, datetime

CFG = "/etc/sing-box/config.json"
HY2DIR = "/etc/sing-box/hy2"
TAG = "hy2-in-443"

def sh(cmd, **kw):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, **kw)

def backup():
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = f"/etc/sing-box/config.json.bak-{ts}"
    sh(f"cp {CFG} {dst}")
    print("backup:", dst)
    return dst

def gen_cert():
    os.makedirs(HY2DIR, exist_ok=True)
    if os.path.exists(f"{HY2DIR}/hy2.key"):
        print("cert exists, skip gen")
        return
    r = sh(f"openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes "
           f"-keyout {HY2DIR}/hy2.key -out {HY2DIR}/hy2.crt -days 825 "
           f"-subj '/CN=bing.com' 2>&1")
    print("cert gen rc=", r.returncode)

def apply():
    pw = os.environ.get("HY2_PW", "")
    if len(pw) < 16:
        sys.exit("HY2_PW missing/too short")
    bak = backup()
    gen_cert()
    cfg = json.load(open(CFG))
    if any(i.get("tag") == TAG for i in cfg.get("inbounds", [])):
        print("inbound already present, no change"); return
    cfg["inbounds"].append({
        "type": "hysteria2", "tag": TAG,
        "listen": "::", "listen_port": 443,
        "users": [{"password": pw}],
        "tls": {"enabled": True,
                "certificate_path": f"{HY2DIR}/hy2.crt",
                "key_path": f"{HY2DIR}/hy2.key"},
    })
    tmp = CFG + ".new"
    json.dump(cfg, open(tmp, "w"), indent=2, ensure_ascii=False)
    sh(f"mv {tmp} {CFG}")
    c = sh("sing-box check -C /etc/sing-box")
    if c.returncode != 0:
        sh(f"cp {bak} {CFG}")
        sys.exit("check failed, rolled back: " + c.stderr[:300])
    r = sh("systemctl restart sing-box && sleep 1 && systemctl is-active sing-box")
    if "active" not in r.stdout:
        sh(f"cp {bak} {CFG} && systemctl restart sing-box")
        sys.exit("restart failed, rolled back: " + r.stdout + r.stderr[:200])
    print("applied; udp listener:")
    print(sh("ss -ulpn | grep :443 || true").stdout)

def rollback():
    baks = sorted([f for f in os.listdir("/etc/sing-box") if f.startswith("config.json.bak-")])
    if not baks:
        sys.exit("no backup found")
    last = "/etc/sing-box/" + baks[-1]
    sh(f"cp {last} {CFG}")
    r = sh("systemctl restart sing-box && systemctl is-active sing-box")
    print("rolled back to", last, ":", r.stdout.strip())

def status():
    print(sh("systemctl is-active sing-box").stdout.strip())
    print(sh("ss -ulpn | grep :443 || echo no-udp-443").stdout.strip())
    cfg = json.load(open(CFG))
    print("inbounds:", [(i.get("type"), i.get("listen_port")) for i in cfg.get("inbounds", [])])

if __name__ == "__main__":
    {"apply": apply, "rollback": rollback, "status": status}[sys.argv[1]]()
