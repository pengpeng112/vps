#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SN-R7 收尾 v2：身份 rtr-37cf72 已在 DMIT 录好（pkg-snr7.json）。B)覆写conf→C)发布→D)WiFi→E)八关。
传输全部用命令内嵌 base64（openssl 解码），规避 paramiko 通道写。"""
import base64, json, os, subprocess, sys, time
import paramiko, requests, urllib3
urllib3.disable_warnings()
os.environ["MGMT_API_BASE"] = "https://127.0.0.1:8444"
sys.path.insert(0, r"F:\vps\scripts")
import mgmt_api_test as M

N = 7; NAME = "SN-R7-AX3000"; RSSH = 13229; PW = "[REDACTED]"
HOST = "192.168.132.1"; REPO = r"F:\vps"
DEVID = json.load(open(REPO + r"\backups\pkg-snr7.json"))["device_id"]
pkg = json.load(open(REPO + r"\backups\pkg-snr7.json"))
VPS_SSH = ['ssh','-p','28722','-i','C:/Users/Administrator/.ssh/id_rsa_dmit','-o','IdentitiesOnly=yes','root@179.255.137.64']

def log(m): print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)
def ssh():
    for i in range(3):
        try:
            c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            c.connect(HOST, username="root", password=PW, timeout=10, allow_agent=False, look_for_keys=False)
            return c
        except Exception as e:
            log(f"  ssh 重试 {i+1}: {e}"); time.sleep(3)
    raise SystemExit("SSH 连不上设备")
def run(c, s, timeout=120):
    _, o, e = c.exec_command(s, timeout=timeout)
    return (o.read()+e.read()).decode(errors="replace")
def put_file(c, data: bytes, dst, mode):
    b = base64.b64encode(data).decode()
    out = run(c, f"echo {b} | openssl base64 -d -A > {dst} && chmod {mode} {dst} && md5sum {dst}")
    return out.split()[0]
def wait_final(tid, timeout=240):
    t0 = time.time()
    while time.time() - t0 < timeout:
        rr = requests.get(M.BASE + "/api/v1/tasks?size=30", headers=H, verify=False, timeout=15).json()
        t = next((x for x in (rr.get("items") or []) if x["id"] == tid), None)
        if t and t["status"] in ("ok","failed","rolled_back","expired","timeout"): return t
        time.sleep(10)
    return None

M.ensure_tunnel(); H = {"Authorization": "Bearer " + M.token()}
log(f"目标 {DEVID}")

c = ssh()
# B) 覆写 agent.conf / sign_pub → 重启 → 等心跳
conf = (f"DEVICE_ID={pkg['device_id']}\nHMAC_KEY={pkg['hmac_key']}\n"
        "MGMT_URL=https://127.0.0.1:18443\nSUB_URL=" + pkg.get('sub_url','') + "\nSIGN_PUB=installed\n")
import hashlib
m1 = put_file(c, conf.encode(), "/etc/routermgmt/agent.conf", 600)
m2 = put_file(c, (pkg["sign_public_key"] + "\n").encode(), "/etc/routermgmt/sign_pub.pem", 644)
ok = m1 == hashlib.md5(conf.encode()).hexdigest() and m2 == hashlib.md5((pkg["sign_public_key"]+"\n").encode()).hexdigest()
log(f"B) conf/pem 覆写 md5 {'OK' if ok else 'FAIL ' + m1 + ' ' + m2}")
run(c, "/etc/init.d/routermgmt restart; sleep 5")
hb = ""
for i in range(15):
    hb = run(c, "grep 'heartbeat ok' /etc/routermgmt/logs/syslog 2>/dev/null | tail -1")
    if "ok" in hb and time.strftime("%H") in hb: break
    time.sleep(10)
log(f"B) 心跳: {hb[-70:] or '150秒未出现'}")
rr = requests.get(M.BASE + f"/api/v1/devices/{DEVID}", headers=H, verify=False, timeout=10).json()
log(f"B) 平台心跳: {rr.get('last_heartbeat_at','无')}")

# C) 发布
for _n, _p in ((5, False), (3, False), (2, False), (4, True)):
    r = requests.post(M.BASE + "/api/v1/assignments/batch", headers=H,
                      json={"device_ids": [DEVID], "node_id": _n, "is_primary": _p, "apply": _p}, verify=False, timeout=30)
tid = r.json()["items"][0]["task_id"]
_fpq = (f"UPDATE node_assignments SET failover_priority=0 WHERE device_id='{DEVID}' AND node_id=5; "
        f"UPDATE node_assignments SET failover_priority=1 WHERE device_id='{DEVID}' AND node_id=3; "
        f"UPDATE node_assignments SET failover_priority=2 WHERE device_id='{DEVID}' AND node_id=2")
subprocess.run(VPS_SSH + [f'docker exec routermgmt-db-1 sh -c "psql -U $POSTGRES_USER -d routermgmt -c \\"{_fpq}\\""'], capture_output=True, timeout=60)
requests.patch(M.BASE + f"/api/v1/devices/{DEVID}", headers=H,
               json={"traffic_alert_hourly_mb": 2048, "reboot_cron": "0 0 * * *"}, verify=False, timeout=15)
t = wait_final(tid)
log(f"C) 发布: {t['status'] if t else 'TIMEOUT'} v{t.get('result',{}).get('detail',{}).get('version','?') if t else ''}")
r = requests.post(M.BASE + f"/api/v1/devices/{DEVID}/ops", headers={**H, "Idempotency-Key": str(time.time())},
                  json={"type": "set_proxy_state", "params": {"enabled": True}}, verify=False, timeout=15)
t = wait_final(r.json()["task_id"], 120)
log(f"C) set_proxy_state: {t['status'] if t else 'TIMEOUT'}")
eg = run(c, "sleep 6; curl -s -m 10 -x http://127.0.0.1:7890 https://api.ipify.org 2>/dev/null")
log(f"C) 出口IP: {eg}（期望 74.82.203.175）")

# D) WiFi + 档案
out = run(c, open(REPO + r"\scripts\.r7-wifi.sh", encoding="utf-8").read() if os.path.exists(REPO + r"\scripts\.r7-wifi.sh") else "")
if not out:
    wifi_sh = r'''
cd /etc/wireless/mediatek
for F in mt7981.dbdc.b0.dat mt7981.dbdc.b1.dat DBDC_card0.dat; do
  [ -f "$F" ] || continue
  sed -i "s/^SSID1=.*/SSID1=SN-R7-AX3000/" "$F"
  [ "$F" = "DBDC_card0.dat" ] && sed -i "s/^SSID7=.*/SSID7=SN-R7-AX3000-5G/" "$F"
  [ "$F" = "mt7981.dbdc.b1.dat" ] && sed -i "s/^SSID1=.*/SSID1=SN-R7-AX3000-5G/" "$F"
  sed -i "s/^\(WPAPSK[0-9]*\)=.*/\1=[REDACTED]/" "$F"
  grep -q "^RadioOff=" "$F" && sed -i "s/^RadioOff=.*/RadioOff=1/" "$F" || sed -i "2i RadioOff=1" "$F"
done
uci set wireless.ra0.key='[REDACTED]'; uci set wireless.ra0.disabled='1'
uci set wireless.rax0.key='[REDACTED]'; uci set wireless.rax0.disabled='1'
uci set wireless.RADIO2G.disabled='1' 2>/dev/null; uci set wireless.RADIO5G.disabled='1' 2>/dev/null
uci commit wireless
sed -i '/# wifi-default-off/,$d' /etc/rc.local
printf '%s\n' \
'# wifi-boot-policy (2026-10-01 v3)' \
'(sleep 8; _w=up; [ -f /etc/routermgmt/state/wifi.enabled ] || _w=down' \
' for _i in 1 2 3 4 5 6; do' \
'   ifconfig ra0 $_w 2>/dev/null; ifconfig rax0 $_w 2>/dev/null' \
'   [ "$_w" = "down" ] && break' \
'   ip link show ra0 2>/dev/null | grep -q "state UP" && break' \
'   sleep 5' \
' done) > /dev/null 2>&1 &' \
'# routermgmt-cron-restore' \
'(sleep 10; grep -q routermgmt-netprobe /etc/crontabs/root 2>/dev/null || echo "*/5 * * * * /etc/routermgmt/netprobe.sh #routermgmt-netprobe" >> /etc/crontabs/root; grep -q mgmt-port-discovery /etc/crontabs/root 2>/dev/null || echo "* * * * * /etc/routermgmt/mgmt-port-discovery.sh #mgmt-port" >> /etc/crontabs/root; /etc/init.d/cron restart) > /dev/null 2>&1 &' \
'exit 0' >> /etc/rc.local
ifconfig ra0 down; ifconfig rax0 down
for I in wan br-lan ra0 rax0; do [ -d /sys/class/net/$I ] && echo "$I: $(cat /sys/class/net/$I/address)"; done
'''
    out = run(c, wifi_sh)
macs = dict(re.findall(r"(wan|br-lan|ra0|rax0): ([0-9a-f:]+)", out))
doc = f"""# {NAME} ({DEVID}) 凭证档案 — {time.strftime('%Y-%m-%d')}

| 通道 | 值 |
|---|---|
| SSH（LAN） | `ssh root@192.168.132.1` / `{PW}` |
| LuCI | `http://192.168.132.1` root / 同上 |
| 反向 SSH | `ssh -p 28722 -i id_rsa_dmit root@179.255.137.64` → `sshpass -p <root口令> ssh -p {RSSH} root@127.0.0.1` |
| WiFi（默认关闭） | SSID `{NAME}`，PSK `[REDACTED]`；开启：管理台「开WiFi」 |
| 管理台 | ID `{DEVID}`，RSSH `{RSSH}` |
| MAC 固定（uci，重启生效） | WAN=`76:a7:c8:e0:4f:27` br-lan=`76:a7:c8:e0:4f:37`（出厂实测 wan={macs.get('wan','?')} ra0={macs.get('ra0','?')}） |
| 备注 | 2026-10-13 刷机；首次录入误落 DO 旧库(rtr-204b63 弃用)，DMIT 正式身份 {DEVID} |

| 接口 | MAC（出厂运行值） |
|---|---|
| WAN | `{macs.get('wan','?')}` |
| 2.4G | `{macs.get('ra0','?')}` |
| 5G | `{macs.get('rax0','?')}` |
"""
open(REPO + rf"\backups\SN-R{N}-凭证档案-{time.strftime('%Y%m%d')}.md", "w", encoding="utf-8").write(doc)
requests.patch(M.BASE + f"/api/v1/devices/{DEVID}", headers=H,
               json={"location_note": f"WAN MAC {macs.get('wan','?')} | 2.4G {macs.get('ra0','?')} | 5G {macs.get('rax0','?')} | WiFi默认关闭(密码[REDACTED]) | {time.strftime('%Y-%m-%d')} 刷机录入",
                      "mac_addr": macs.get("ra0","")}, verify=False, timeout=15)
log(f"D) WiFi+档案完成 macs={macs}")

# E) 八关
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
    ("隧道监听>=3", int(kv.get('L','0')) >= 3),
    ("API健康200", kv.get('H') == '200'),
    ("出口IP=东京", kv.get('E','') == '74.82.203.175'),
    ("google 204", kv.get('G') == '204'),
    ("网页登录302", kv.get('W') == '302'),
    ("crontab>=4条", int(kv.get('C','0')) >= 4),
    ("rc.local钩子", kv.get('R','0') == '1'),
    ("btoa编码2/1", kv.get('T') == '2/1'),
]
for n, ok in checks: log(f"    {'PASS' if ok else 'FAIL'} {n}")
c.close()
failed = [n for n, ok in checks if not ok]
if failed: log(f"E) 终验未过: {failed}"); sys.exit(2)
log(f"E) ✅ {NAME} ({DEVID}) 刷机完成，八关全过")
