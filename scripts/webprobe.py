#!/usr/bin/env python3
"""webprobe.py — 境外网站体验巡检（每 2 小时，本机执行，走完整真实路径）
每站测 3 次：HTTP 状态、总耗时、连接耗时、TLS 耗时、首字节耗时、（HTTPS 目标）TLS1.3 协商。
输出 CSV 到 F:\\vps\\webprobe\\webprobe.csv，一行一站：
  ts,site,ok_rate(0-1),avg_total_s,min_total_s,max_total_s,avg_connect_s,avg_tls_s,avg_ttfb_s,tls13_ok
 Lines append; rotation: >20000 行时裁掉前 10000 行。
"""
import csv, os, subprocess, time, datetime, socket

OUT_DIR = r"F:\vps\webprobe"
OUT = os.path.join(OUT_DIR, "webprobe-v2.csv")   # v2：含 verdict 判定列；旧 webprobe.csv 为历史保留

SITES = [
    # (名称, URL)  ——均为 HTTPS，走系统默认路径（= 经当前网关路由器透明代理按分流规则出去）
    # 2026-09-27 M04：补 gstatic（M02 修复验证）与 baidu（F3 公司上联 POP 黑洞对照）
    ("gstatic",      "https://www.gstatic.com/generate_204"),
    ("google_search", "https://www.google.com/generate_204"),
    ("baidu",        "https://www.baidu.com/"),
    ("youtube",       "https://www.youtube.com/generate_204"),
    ("twitter_x",     "https://x.com/robots.txt"),
    ("facebook",      "https://www.facebook.com/robots.txt"),
    ("instagram",     "https://www.instagram.com/robots.txt"),
    ("reddit",        "https://www.reddit.com/robots.txt"),
    ("bbc_news",      "https://www.bbc.com/robots.txt"),
    ("reuters",       "https://www.reuters.com/robots.txt"),
    ("wikipedia",     "https://www.wikipedia.org/robots.txt"),
    ("twitch",        "https://www.twitch.tv/robots.txt"),
    ("github",        "https://github.com/robots.txt"),
    # 2026-09-29：全类别扩充（视频/消息/新闻），本机已改挂雅典娜路径
    ("netflix",       "https://www.netflix.com/robots.txt"),
    ("telegram",      "https://telegram.org/robots.txt"),
    ("whatsapp",      "https://www.whatsapp.com/robots.txt"),
    ("discord",       "https://discord.com/robots.txt"),
    ("cnn",           "https://www.cnn.com/robots.txt"),
]

def curl_once(url):
    """返回 (http_code, total, connect, tls, ttfb) 秒；异常返回 (000, 各 999)"""
    fmt = "%{http_code} %{time_total} %{time_connect} %{time_appconnect} %{time_starttransfer}"
    r = subprocess.run(["curl", "-o", "NUL", "-s", "-m", "12", "-w", fmt, url],
                       capture_output=True, text=True, timeout=20)
    try:
        parts = r.stdout.split()
        code = parts[0]
        t_total, t_conn, t_tls, t_ttfb = (float(x) for x in parts[1:5])
        return code, t_total, t_conn, t_tls, t_ttfb
    except Exception:
        return "000", 999.0, 999.0, 999.0, 999.0

def fail_kind(r):
    """对 000 结果做失败类型归因：dns/tcp/tls/http"""
    code, total, conn, tls, ttfb = r
    if total < 1.0:
        return "dns"      # 秒败：域名解析/本地错误
    if conn == 0.0 and tls == 0.0:
        return "tcp"      # TCP 都建不起来：线路丢包或被阻断
    if tls == 0.0:
        return "tls"      # TCP 通但 TLS 握手失败：中间盒/代理协议层问题
    return "http"

def probe_site(name, url):
    results = [curl_once(url) for _ in range(3)]
    codes = [r[0] for r in results]
    ok = sum(1 for c in codes if c not in ("000",))
    ok_codes = [c for c in codes if c not in ("000",)]
    def avg(i):
        vals = [r[i] for r in results if r[0] != "000"]
        return round(sum(vals) / len(vals), 3) if vals else ""
    # 综合判定：超时(含类型)/慢/不稳定(抖动)/正常
    if ok < 3:
        kinds = {}
        for r in results:
            if r[0] == "000":
                k = fail_kind(r)
                kinds[k] = kinds.get(k, 0) + 1
        verdict = "timeout:" + "+".join(f"{k}x{n}" for k, n in sorted(kinds.items()))
    else:
        totals = [r[1] for r in results]
        a = sum(totals) / 3
        jitter = max(totals) - min(totals)
        if a > 3:
            verdict = "slow"
        elif jitter > 1.5:
            verdict = "jitter"
        else:
            verdict = "ok"
    return {
        "ts": datetime.datetime.now().strftime("%F %T"),
        "site": name,
        "ok_rate": f"{ok}/{len(results)}",
        "avg_total_s": avg(1), "min_total_s": min((r[1] for r in results if r[0] != "000"), default=""),
        "max_total_s": max((r[1] for r in results if r[0] != "000"), default=""),
        "avg_connect_s": avg(2), "avg_tls_s": avg(3), "avg_ttfb_s": avg(4),
        "codes": "|".join(codes),
        "verdict": verdict,
    }

def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    rows = [probe_site(n, u) for n, u in SITES]
    header_needed = not os.path.exists(OUT)
    with open(OUT, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        if header_needed:
            w.writeheader()
        w.writerows(rows)
    # 每轮一行汇总（人类可读，便于快速翻阅波动）
    okn = sum(1 for r in rows if r["ok_rate"] == "3/3")
    scored = [r for r in rows if r["avg_total_s"] != ""]
    worst = max(scored, key=lambda r: float(r["avg_total_s"])) if scored else None
    fails = [f'{r["site"]}({r["verdict"]})' for r in rows if r["verdict"] not in ("ok", "slow")]
    slow = [f'{r["site"]}={r["avg_total_s"]}s' for r in scored if float(r["avg_total_s"]) > 3]
    jit = [f'{r["site"]}({r["verdict"]})' for r in rows if r["verdict"] == "jitter"]
    line = f'{rows[0]["ts"]} | ok {okn}/{len(rows)}'
    if worst:
        line += f' | worst {worst["site"]} {worst["avg_total_s"]}s'
    if fails:
        line += " | FAIL " + ",".join(fails)
    if slow:
        line += " | SLOW " + ",".join(slow)
    if jit:
        line += " | JITTER " + ",".join(jit)
    with open(os.path.join(OUT_DIR, "webprobe_runs.log"), "a", encoding="utf-8") as f:
        f.write(line + "\n")
    # rotation
    try:
        with open(OUT, encoding="utf-8") as f:
            n = sum(1 for _ in f)
        if n > 20000:
            lines = f.seek(0) or []
        with open(OUT, encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > 20000:
            with open(OUT, "w", encoding="utf-8") as f:
                f.writelines(lines[-10000:])
    except Exception:
        pass
    print(f"webprobe done: {sum(1 for r in rows if '0/3' not in r['ok_rate'])}/{len(rows)} sites ok")

if __name__ == "__main__":
    main()
