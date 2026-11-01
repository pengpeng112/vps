#!/usr/bin/env python3
"""Small PC-side network observer. Public GETs only; never changes proxy or router state."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "webprobe" / "network_watch"
EXPECTED_GATEWAY = "192.168.2.1"
TARGETS = (
    ("google", "https://www.google.com/generate_204", 204, None, False),
    ("gstatic", "https://www.gstatic.com/generate_204", 204, None, False),
    ("mail_google", "https://mail.google.com/", 301, None, False),
    ("baidu_default", "https://www.baidu.com/", 200, None, False),
    ("baidu_good_pop", "https://www.baidu.com/", 200, "110.242.69.21", False),
    ("baidu_known_bad_pop", "https://www.baidu.com/", 200, "110.242.70.57", True),
)
FIELDS = ("remote_ip", "time_namelookup", "time_connect", "time_appconnect", "time_starttransfer", "time_total")


def timestamp():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def classify(exit_code, http_code, expected):
    if exit_code == 6:
        return "dns_error"
    if exit_code == 28:
        return "timeout"
    if exit_code == 7:
        return "connect_error"
    if exit_code in (35, 51, 60):
        return "tls_error"
    if exit_code != 0:
        return "transport_error"
    if http_code != expected:
        return "http_unexpected"
    return "ok"


def default_gateways():
    try:
        p = subprocess.run(["route", "print", "-4"], capture_output=True, text=True,
                           errors="replace", timeout=8)
        if p.returncode:
            return []
        return sorted(set(re.findall(
            r"^\s*0\.0\.0\.0\s+0\.0\.0\.0\s+((?:\d{1,3}\.){3}\d{1,3})\s+",
            p.stdout, re.MULTILINE)))
    except (OSError, subprocess.TimeoutExpired):
        return []


def gateway_probe():
    good = 0
    for _ in range(3):
        try:
            p = subprocess.run(["ping", "-n", "1", "-w", "500", EXPECTED_GATEWAY],
                               capture_output=True, timeout=4)
            good += p.returncode == 0 and b"TTL=" in p.stdout.upper()
        except (OSError, subprocess.TimeoutExpired):
            pass
    return {"target": "gateway", "attempts": 3, "replies": good,
            "status": "ok" if good == 3 else "loss" if good else "unreachable"}


def http_probe(target):
    name, url, expected, pin, control = target
    cmd = ["curl.exe", "--noproxy", "*", "--http1.1", "-sS", "-o", "NUL",
           "--connect-timeout", "4", "--max-time", "7", "-w", "%{json}"]
    if pin:
        cmd += ["--resolve", "www.baidu.com:443:" + pin]
    cmd.append(url)
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        exit_code = p.returncode
        # curl output is a controlled public URL; never store stderr, headers or body.
        try:
            raw = json.loads(p.stdout)
        except (json.JSONDecodeError, ValueError):
            raw = {}
        code = int(raw.get("http_code") or 0)
        result = {k: raw.get(k) for k in FIELDS}
    except (OSError, subprocess.TimeoutExpired):
        exit_code, code, result = -1, 0, {k: None for k in FIELDS}
    return {"target": name, "expected_http": expected, "http_code": code,
            "curl_exit": exit_code, "status": classify(exit_code, code, expected),
            "control": control, **result}


def sample(origin):
    gateways = default_gateways()
    with ThreadPoolExecutor(max_workers=7) as pool:
        futures = [pool.submit(gateway_probe)] + [pool.submit(http_probe, t) for t in TARGETS]
        results = [f.result() for f in futures]
    return {"schema": 1, "run_id": uuid.uuid4().hex[:12], "ts": timestamp(),
            "origin": origin,
            "expected_gateway": EXPECTED_GATEWAY, "default_gateways": gateways,
            "route_status": "ok" if gateways == [EXPECTED_GATEWAY] else "unknown" if not gateways else "drift",
            "probes": results}


def files():
    return sorted(DATA.glob("observations-??????.jsonl"))


def valid_record(item):
    if not isinstance(item, dict) or item.get("schema") != 1 or not isinstance(item.get("run_id"), str):
        return False
    probes = item.get("probes")
    if not isinstance(probes, list) or len(probes) != len(TARGETS) + 1:
        return False
    expected = {"gateway", *(target[0] for target in TARGETS)}
    if {p.get("target") for p in probes if isinstance(p, dict)} != expected:
        return False
    return all(isinstance(p, dict) and p.get("status") in
               {"ok", "loss", "unreachable", "dns_error", "timeout", "connect_error", "tls_error", "transport_error", "http_unexpected"}
               for p in probes)


def load(cutoff, end):
    rows = []
    invalid = 0
    for path in files():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
                if not valid_record(item):
                    invalid += 1
                    continue
                when = datetime.fromisoformat(item["ts"])
                if when.tzinfo is None:
                    invalid += 1
                    continue
                if cutoff <= when.astimezone(timezone.utc) <= end:
                    rows.append(item)
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                invalid += 1
    return sorted(rows, key=lambda r: r["ts"]), invalid


def percentile(values, part):
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[math.ceil(len(ordered) * part) - 1], 3)


def report(hours):
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)
    rows, invalid = load(start, end)
    lines = [f"# 网络观察（最近 {hours:g} 小时）", ""]
    if not rows:
        return "\n".join(lines + [f"本窗口没有采样数据；扫描文件时跳过无效记录{invalid}条。", ""])
    first = datetime.fromisoformat(rows[0]["ts"]).astimezone(timezone.utc)
    last = datetime.fromisoformat(rows[-1]["ts"]).astimezone(timezone.utc)
    gaps = [(datetime.fromisoformat(b["ts"]) - datetime.fromisoformat(a["ts"])).total_seconds() / 60
            for a, b in zip(rows, rows[1:])]
    missed = sum(g > 25 for g in gaps)
    beginning_gap = max(0, (first - start).total_seconds() / 60)
    ending_gap = max(0, (end - last).total_seconds() / 60)
    duplicate_runs = len(rows) - len({r.get("run_id") for r in rows})
    origins = {name: sum(r.get("origin", "unmarked") == name for r in rows)
               for name in ("task", "manual", "unmarked")}
    lines += [f"请求窗口：{start.astimezone().isoformat(timespec='minutes')}—{end.astimezone().isoformat(timespec='minutes')}。",
              f"实际采样：{len(rows)}轮；首轮 {rows[0]['ts']}；末轮 {rows[-1]['ts']}。",
              f"运行入口：计划任务 {origins['task']}轮，手工命令 {origins['manual']}轮，旧记录未标记 {origins['unmarked']}轮。计划任务也可由人工触发。",
              f"窗口起点至首轮 {beginning_gap:.1f}分钟；末轮至现在 {ending_gap:.1f}分钟；相邻采样超过25分钟 {missed}处；最大间隔 {max(gaps, default=0):.1f}分钟。",
              f"无效记录 {invalid}条；重复run_id {duplicate_runs}条。", ""]
    if beginning_gap > 25 or ending_gap > 25 or missed or invalid or duplicate_runs:
        lines += ["**本报告存在采样覆盖缺口或数据异常，成功率只代表实际采到的请求。**", ""]
    lines += ["| 目标 | 采样 | 成功 | 超时 | DNS失败 | 其他失败 | P95总耗时 | 最慢 |",
              "|---|---:|---:|---:|---:|---:|---:|---:|"]
    names = ["gateway"] + [t[0] for t in TARGETS]
    for name in names:
        samples = [p for r in rows for p in r.get("probes", []) if p.get("target") == name]
        if not samples:
            continue
        ok = sum(p["status"] == "ok" for p in samples)
        if name == "gateway":
            lost = sum(max(0, p.get("attempts", 0) - p.get("replies", 0)) for p in samples)
            lines.append(f"| {name} | {len(samples)}轮 | {ok}轮 | — | — | 丢{lost}包 | — | — |")
            continue
        timed = sum(p["status"] == "timeout" for p in samples)
        dns = sum(p["status"] == "dns_error" for p in samples)
        other = len(samples) - ok - timed - dns
        durations = [float(p["time_total"]) for p in samples if p.get("time_total") is not None]
        lines.append(f"| {name} | {len(samples)} | {ok} | {timed} | {dns} | {other} | "
                     f"{percentile(durations, .95)}s | {max(durations, default=0):.3f}s |")
    drift = sum(r.get("route_status") != "ok" for r in rows)
    lines += ["", f"默认网关异常/无法读取：{drift}轮。",
              "`baidu_known_bad_pop`为历史异常地址的对照项，不计入常规网站可用率；失败仍须保留。",
              "HTTP 状态只在与该目标预期值相同时算成功。P95包含有curl耗时的失败样本；本程序不能证明整个网页或应用会话正常。", ""]
    bad = [(r["ts"], p) for r in rows for p in r.get("probes", [])
           if p.get("status") != "ok" and not p.get("control")]
    lines += [f"常规目标异常事件：{len(bad)}条（以下最多20条）。", ""]
    for ts, p in bad[-20:]:
        lines.append(f"- {ts} {p['target']}: {p['status']}，curl={p.get('curl_exit', '-')}, "
                     f"HTTP={p.get('http_code', '-')}, 总耗时={p.get('time_total', '-')}s")
    if not bad:
        lines.append("- 本窗口未采到常规目标异常。")
    lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--report", action="store_true", help="Read local observations; make no requests")
    ap.add_argument("--scheduled", action="store_true", help="Mark Task Scheduler samples")
    ap.add_argument("--hours", type=float, default=24)
    args = ap.parse_args()
    if args.hours <= 0:
        ap.error("--hours must be positive")
    if args.report:
        print(report(args.hours))
        return
    result = sample("task" if args.scheduled else "manual")
    DATA.mkdir(parents=True, exist_ok=True)
    period = datetime.fromisoformat(result["ts"]).strftime("%Y%m")
    with (DATA / f"observations-{period}.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n")
    normal = [p for p in result["probes"] if not p.get("control")]
    print(f"{result['ts']} gateway={result['route_status']} "
          f"ok={sum(p['status'] == 'ok' for p in normal)}/{len(normal)} "
          f"known_bad_pop={result['probes'][-1]['status']}")


if __name__ == "__main__":
    main()
