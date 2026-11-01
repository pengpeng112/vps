#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""三台路由器的分节点网站探测。不切换 NODE-PICK，不改绑定。

每轮记录：
  - 到 LAX / 东京 / 腾讯 / DO 的 ICMP 丢包和 RTT
  - 每个代理节点对指定网站的 delay（mihomo 逐节点探测，业务出口不变）
  - 当前正在使用的出口上，一次 2MB 下载速度

结果写到 logs/node-probe/（已 gitignore）。口令只从 backups/.snrN-pw 第一行读取。
"""
import csv, json, os, re, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import quote

import paramiko

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGDIR = os.path.join(REPO, "logs", "node-probe")
MGMT = ("179.255.137.64", 28722)
MGMT_KEY = os.path.expanduser("~/.ssh/id_rsa_dmit")
SAFE = re.compile(r"^[A-Za-z0-9._-]+$")

DEVICES = [
    ("R2", "rtr-a9bcba", 13223, ".snr2-pw"),
    ("R3", "rtr-38aa54", 13227, ".snr3-pw"),
    ("R4", "rtr-cd473f", 13224, ".snr4-pw"),
]
NODES = [
    "BWG-VLESS-JPTY-01",
    "LAX-VLESS-US-01",
    "TX-HY2-SG-01",
    "VPS-TX-SG-02",
]
SITES = [
    ("google", "https://www.google.com/generate_204"),
    ("youtube", "https://www.youtube.com/generate_204"),
    ("youtube_video", "https://www.youtube.com/watch?v=jNQXAC9IVRw"),
    ("twitter", "https://x.com/"),
    ("twitter_post", "https://x.com/jack/status/20"),
    ("facebook", "https://www.facebook.com/"),
    ("facebook_page", "https://www.facebook.com/facebook"),
    ("nychinese", "https://www.dadi360.com/"),
]
ICMP = [
    ("LAX", "179.255.137.64"),
    ("BWG", "74.82.203.175"),
    ("TX", "43.134.46.44"),
    ("DO", "159.223.36.119"),
]


def pw_of(filename):
    path = os.path.join(REPO, "backups", filename)
    with open(path, encoding="utf-8") as f:
        line = f.read().splitlines()[0].strip()
    if not line:
        raise RuntimeError("empty password file")
    return line


def remote_script():
    lines = [
        "echo ID=$(grep '^DEVICE_ID=' /etc/routermgmt/agent.conf | head -n 1)",
        "echo NOW=$(curl -fsS -m 4 http://127.0.0.1:9090/proxies/NODE-PICK 2>/dev/null | sed -n 's/.*\"now\":\"\\([^\"]*\\)\".*/\\1/p')",
        "echo EXIT=$(curl -s -m 15 -x http://127.0.0.1:7890 https://api.ipify.org 2>/dev/null | tr -d '\\r\\n')",
        "echo NAMES=$(curl -fsS -m 5 http://127.0.0.1:9090/proxies 2>/dev/null | grep -o '\"name\":\"[^\"]*\"' | sed 's/\"name\":\"//;s/\"//' | tr '\\n' ',')",
    ]
    for name, ip in ICMP:
        lines.append(
            "out=$(ping -c 10 -q -w 14 %s 2>/dev/null | tr '\\n' ' '); echo \"ICMP %s $out\""
            % (ip, name)
        )
    for node in NODES:
        if not SAFE.match(node):
            raise RuntimeError(node)
        for key, url in SITES:
            if not SAFE.match(key):
                raise RuntimeError(key)
            enc = quote(url, safe="")
            lines.append(
                "r=$(curl -s -m 12 \"http://127.0.0.1:9090/proxies/%s/delay?url=%s&timeout=10000\" 2>/dev/null); "
                "d=$(printf '%%s' \"$r\" | grep -o '\"delay\":[0-9]*' | head -n 1 | cut -d: -f2); "
                "[ -n \"$d\" ] || d=FAIL; echo \"SITE %s %s $d\""
                % (node, enc, node, key)
            )
    lines.append(
        "sp=$(curl -s -m 30 -o /dev/null -x http://127.0.0.1:7890 "
        "-w '%{speed_download} %{http_code} %{time_total}' "
        "'https://speed.cloudflare.com/__down?bytes=2097152' 2>/dev/null); "
        "echo \"SPEED $sp\""
    )
    return "\n".join(lines) + "\n"


def run_device(label, expect_id, port, pwfile):
    started = time.time()
    pw = pw_of(pwfile)
    script = remote_script()
    remote_path = "/tmp/node-site-%s.sh" % label
    wrapped = (
        "umask 077; PF=$(mktemp); IFS= read -r _pw; printf '%%s\\n' \"$_pw\" > \"$PF\"; "
        "sshpass -f \"$PF\" ssh -p %d -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
        "-o PreferredAuthentications=password -o PubkeyAuthentication=no -o ConnectTimeout=15 "
        "root@127.0.0.1 'sh -s' < %s; "
        "rc=$?; rm -f \"$PF\" %s; echo RC=$rc"
        % (port, remote_path, remote_path)
    )
    mg = paramiko.SSHClient()
    mg.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    mg.connect(
        MGMT[0], port=MGMT[1], username="root", key_filename=MGMT_KEY,
        look_for_keys=False, allow_agent=False, timeout=25, auth_timeout=25,
        banner_timeout=25,
    )
    try:
        with mg.open_sftp() as sftp:
            with sftp.file(remote_path, "w") as fh:
                fh.write(script)
        stdin, stdout, stderr = mg.exec_command(wrapped, timeout=420)
        stdin.write(pw + "\n")
        stdin.flush()
        stdin.channel.shutdown_write()
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
    finally:
        mg.close()
    parsed = parse_out(out)
    parsed["label"] = label
    parsed["expect_id"] = expect_id
    parsed["seconds"] = round(time.time() - started, 1)
    parsed["stderr_tail"] = err[-300:]
    if expect_id not in parsed.get("id", ""):
        parsed["id_ok"] = False
    else:
        parsed["id_ok"] = True
    return parsed


def parse_out(text):
    row = {"raw_tail": "\n".join(text.splitlines()[-8:]), "sites": {}, "icmp": {}, "names": ""}
    for line in text.splitlines():
        if line.startswith("ID="):
            row["id"] = line[3:]
        elif line.startswith("NOW="):
            row["now"] = line[4:]
        elif line.startswith("EXIT="):
            row["exit"] = line[5:]
        elif line.startswith("NAMES="):
            row["names"] = line[6:]
        elif line.startswith("ICMP "):
            parts = line.split()
            name = parts[1] if len(parts) > 1 else "?"
            loss = next((p for p in parts if p.endswith("%")), "?")
            rtt = "?"
            for p in parts:
                if "/" in p and p[0].isdigit():
                    bits = p.split("/")
                    if len(bits) >= 2:
                        rtt = bits[1]
            row["icmp"][name] = {"loss": loss, "rtt_ms": rtt, "line": " ".join(parts[2:])[:180]}
        elif line.startswith("SITE "):
            p = line.split()
            if len(p) >= 4:
                row["sites"][(p[1], p[2])] = p[3]
        elif line.startswith("SPEED "):
            p = line.split()
            row["speed_Bps"] = p[1] if len(p) > 1 else ""
            row["speed_code"] = p[2] if len(p) > 2 else ""
            row["speed_s"] = p[3] if len(p) > 3 else ""
        elif line.startswith("RC="):
            row["rc"] = line[3:]
    return row


def mbps(bps):
    try:
        return round(float(bps) * 8 / 1_000_000, 2)
    except (TypeError, ValueError):
        return None


def write_outputs(stamp, results):
    os.makedirs(os.path.join(LOGDIR, "runs"), exist_ok=True)
    payload = {"stamp": stamp, "devices": []}
    for r in results:
        item = dict(r)
        item["sites"] = {f"{a}|{b}": c for (a, b), c in r.get("sites", {}).items()}
        payload["devices"].append(item)
    run_path = os.path.join(LOGDIR, "runs", stamp + ".json")
    with open(run_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    csv_path = os.path.join(LOGDIR, "history.csv")
    new = not os.path.exists(csv_path)
    with open(csv_path, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["stamp", "router", "kind", "name", "detail", "value", "extra"])
        for r in results:
            lab = r["label"]
            w.writerow([stamp, lab, "exit", r.get("now", ""), r.get("exit", ""), "", r.get("id", "")])
            for name, info in r.get("icmp", {}).items():
                w.writerow([stamp, lab, "icmp", name, info.get("loss", ""), info.get("rtt_ms", ""), ""])
            for (node, site), delay in sorted(r.get("sites", {}).items()):
                w.writerow([stamp, lab, "site", node, site, delay, ""])
            w.writerow([stamp, lab, "speed", r.get("now", ""), r.get("speed_code", ""), r.get("speed_Bps", ""), mbps(r.get("speed_Bps"))])
    lines = [
        "# 节点网站探测 %s" % stamp,
        "",
        "延迟单位是毫秒，来自各节点自己的探测，没有切换正在使用的出口。",
        "丢包是路由器到该服务器地址的 ICMP。速度是当前出口上的一次 2MB 下载，单位 Mbps。",
        "FAIL 表示 10 秒内没有拿到延迟数字。",
        "",
    ]
    for r in results:
        lines.append("## %s  当前节点 %s  出口 %s" % (r["label"], r.get("now", "?"), r.get("exit", "?")))
        lines.append("")
        lines.append("| 服务器 | 丢包 | RTT ms |")
        lines.append("|---|---|---|")
        for name, _ip in ICMP:
            info = r.get("icmp", {}).get(name, {})
            lines.append("| %s | %s | %s |" % (name, info.get("loss", "未测"), info.get("rtt_ms", "")))
        lines.append("")
        header = "| 网站 | " + " | ".join(n for n in NODES) + " |"
        lines.append(header)
        lines.append("|---|" + "---|" * len(NODES))
        for key, _url in SITES:
            cells = [str(r.get("sites", {}).get((n, key), "未测")) for n in NODES]
            lines.append("| %s | %s |" % (key, " | ".join(cells)))
        lines.append("")
        lines.append("当前出口速度：%s Mbps（HTTP %s，用时 %s 秒）" % (
            mbps(r.get("speed_Bps")), r.get("speed_code", "?"), r.get("speed_s", "?")))
        if not r.get("id_ok", False):
            lines.append("设备身份与预期不符：%s" % r.get("id", ""))
        lines.append("")
    latest = os.path.join(LOGDIR, "latest.md")
    with open(latest, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return latest, run_path


def main():
    os.makedirs(LOGDIR, exist_ok=True)
    lock = os.path.join(LOGDIR, ".lock")
    if os.path.exists(lock):
        age = time.time() - os.path.getmtime(lock)
        if age < 3600:
            print("skip: previous run still marked running")
            return 0
    open(lock, "w").close()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    results = []
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            futs = {
                pool.submit(run_device, lab, did, port, pw): lab
                for lab, did, port, pw in DEVICES
            }
            for fut in as_completed(futs):
                lab = futs[fut]
                try:
                    results.append(fut.result())
                    print(lab, "ok")
                except Exception as e:
                    results.append({
                        "label": lab, "id_ok": False, "sites": {}, "icmp": {},
                        "now": "", "exit": "", "error": type(e).__name__ + ": " + str(e)[:200],
                    })
                    print(lab, "ERR", type(e).__name__)
        results.sort(key=lambda r: r["label"])
        latest, run_path = write_outputs(stamp, results)
        print(latest)
        print(run_path)
    finally:
        try:
            os.remove(lock)
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
