#!/usr/bin/env python3
"""webprobe_report.py — 网络波动分析报告（只读 webprobe.csv，不改任何配置）
用法：
  python scripts/webprobe_report.py            # 全部数据
  python scripts/webprobe_report.py --days 3   # 最近 3 天
  python scripts/webprobe_report.py --out 报告.md  # 同时写出 Markdown
输出：轮次概览 / 站点波动排名 / 按小时时段波动 / 异常事件清单
"""
import csv, os, sys, datetime, argparse
from collections import defaultdict

CSV = r"F:\vps\webprobe\webprobe.csv"

def load(days):
    rows = []
    with open(CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                r["dt"] = datetime.datetime.strptime(r["ts"], "%Y-%m-%d %H:%M:%S")
            except Exception:
                continue
            rows.append(r)
    if days:
        cutoff = datetime.datetime.now() - datetime.timedelta(days=days)
        rows = [r for r in rows if r["dt"] >= cutoff]
    return rows

def group_rounds(rows):
    """按时间间隔>15分钟切轮；返回 [(轮时间, 该轮rows)]"""
    if not rows:
        return []
    rows = sorted(rows, key=lambda r: r["dt"])
    rounds, cur, prev = [], [rows[0]], rows[0]["dt"]
    for r in rows[1:]:
        if (r["dt"] - prev).total_seconds() > 900:
            rounds.append(cur); cur = []
        cur.append(r); prev = r["dt"]
    rounds.append(cur)
    return rounds

def fnum(r, k):
    try:
        return float(r[k])
    except (ValueError, KeyError, TypeError):
        return None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=0)
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    rows = load(a.days)
    if not rows:
        print("无数据"); return
    rounds = group_rounds(rows)
    L = []
    L.append(f"# 网络波动报告（{rows[0]['ts']} ~ {rows[-1]['ts']}，共 {len(rounds)} 轮 / {len(rows)} 行）\n")

    # 1) 轮次概览
    L.append("## 一、轮次概览（每轮 11 站）\n")
    L.append("| 轮时间 | 通过 | 轮均值 | 最慢站 | 异常 |")
    L.append("|---|---|---|---|---|")
    for rd in rounds:
        okn = sum(1 for r in rd if r["ok_rate"] == "3/3")
        scored = [(r["site"], fnum(r, "avg_total_s")) for r in rd]
        scored = [(s, v) for s, v in scored if v is not None]
        avg = sum(v for _, v in scored) / len(scored) if scored else 0
        ws, wv = max(scored, key=lambda x: x[1]) if scored else ("-", 0)
        bad = [r["site"] for r in rd if r["ok_rate"] != "3/3"]
        t = min(r["dt"] for r in rd).strftime("%m-%d %H:%M")
        L.append(f"| {t} | {okn}/{len(rd)} | {avg:.2f}s | {ws} {wv:.2f}s | {','.join(bad) if bad else '—'} |")

    # 2) 站点波动排名（全期）
    L.append("\n## 二、站点波动排名（按丢包率降序，再按均值降序）\n")
    L.append("| 站点 | 样本 | 丢包率 | 平均 | P95 | 最差 |")
    L.append("|---|---|---|---|---|---|")
    by_site = defaultdict(list)
    for r in rows:
        by_site[r["site"]].append(r)
    stats = []
    for s, rs in by_site.items():
        vals = [v for r in rs if (v := fnum(r, "avg_total_s")) is not None]
        loss = sum(1 for r in rs if r["ok_rate"] != "3/3") / len(rs)
        p95 = sorted(vals)[int(0.95 * (len(vals) - 1))] if vals else 0
        stats.append((s, len(rs), loss, sum(vals) / len(vals) if vals else 0, p95, max(vals) if vals else 0))
    stats.sort(key=lambda x: (-x[2], -x[3]))
    for s, n, loss, avg, p95, mx in stats:
        L.append(f"| {s} | {n} | {loss*100:.0f}% | {avg:.2f}s | {p95:.2f}s | {mx:.2f}s |")

    # 3) 按小时时段波动
    L.append("\n## 三、按小时时段波动（全期聚合，定位坏时段）\n")
    L.append("| 时段 | 轮次样本 | 全站平均 | 丢包事件 |")
    L.append("|---|---|---|---|")
    by_hr = defaultdict(lambda: [0, 0.0, 0])
    for r in rows:
        h = r["dt"].hour
        v = fnum(r, "avg_total_s")
        if v is not None:
            by_hr[h][0] += 1
            by_hr[h][1] += v
        if r["ok_rate"] != "3/3":
            by_hr[h][2] += 1
    for h in sorted(by_hr):
        n, tot, bad = by_hr[h]
        L.append(f"| {h:02d}:00 | {n} | {tot/n:.2f}s | {bad} 次 |")

    # 4) 异常事件清单
    L.append("\n## 四、异常事件（丢包或 >3s）\n")
    bad = []
    for r in rows:
        v = fnum(r, "avg_total_s")
        if r["ok_rate"] != "3/3" or (v and v > 3):
            bad.append(f"- `{r['ts']}` **{r['site']}** ok={r['ok_rate']} avg={r['avg_total_s']}s codes={r['codes']}")
    if bad:
        L.extend(bad[-40:])
    else:
        L.append("（无异常事件）")

    text = "\n".join(L)
    print(text)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print(f"\n已写出 {a.out}")

if __name__ == "__main__":
    main()
