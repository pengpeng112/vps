#!/usr/bin/env python3
"""导出 ZCode 历史会话为 Markdown（v2，2026-09-27 统一优化 M05 修订）。

数据源: ~/.zcode/cli/db/db.sqlite（表 session / message / part，只读打开）

v2 安全修订（对应完善计划 P01）：
  1. 脱敏默认开启；关闭需同时给 --no-redact --danger-confirm（危险导出不再是普通流程）
  2. 项目目录精确/边界匹配（v1 的前缀 LIKE 会把 F:\\vps-other 一并带出）
  3. 源码不再内嵌任何真实密钥；额外精确对从受控文件 F:\\vps\\backups\\.redact_pairs.json
     运行时加载（JSON: [["正则","替换"], ...]），缺失时只用结构化规则并告警
  4. 标题/工具入参/工具输出/思考段统一过脱敏；统计截断、解析失败、替换次数
  5. 消息/分段逐行流式读取，不再 fetchall 整会话
用法:
  python export_zcode_sessions.py                       # F:\\vps 会话，脱敏开
  python export_zcode_sessions.py --tools --last 3      # 最近3个含工具摘要
  python export_zcode_sessions.py --out 合并.md         # 合并单文件
"""
import argparse, json, os, re, sqlite3, sys, datetime

DB = os.path.expanduser("~/.zcode/cli/db/db.sqlite")
DEFAULT_OUT_DIR = r"F:\vps\docs\会话导出"
EXTRA_PAIRS_FILE = r"F:\vps\backups\.redact_pairs.json"  # 受控位置，不入 docs/repo

# 结构化规则：不含任何真实秘密值，靠形态识别
STRUCT_RULES = [
    (r"(?i)(password|passwd|pwd|secret|token|api[_-]?key|private[_-]?key)\s*[:=]\s*([^\s'\"，。;；]{4,})", r"\1: ***REDACTED***"),
    (r"(密钥|密码|口令)\s*[:：=]?\s*([A-Za-z0-9!@#$%^&*()_+=./\-\[\]]{6,})", r"\1***REDACTED***"),
    (r"(?i)(sshpass\s+)-p\s+\S+", r"\1-p ***REDACTED***"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----", "***PRIVATE_KEY_BLOCK***"),
    (r"(?i)(uuid|short-id|shortid|sid)\s*[:=]\s*([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{8,16})\b", r"\1: ***REDACTED***"),
    (r"(?i)(public-key|pbk)\s*[:=]\s*[A-Za-z0-9_-]{32,64}", r"\1: ***REDACTED***"),
    (r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b", "***JWT***"),
    (r"\b[A-Fa-f0-9]{64}\b(?=.*?(密|key|key\b))?", "***HEX64***"),
    (r"(?i)(ssh|db|postgres|mysql|redis|amqp)://[^\s'\"]+@[^\s'\"]+", r"***DSN***"),
]

STATS = {"redactions": 0, "truncated": 0, "parse_fail": 0, "messages": 0, "parts": 0}

def load_extra_pairs(path=EXTRA_PAIRS_FILE):
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            pairs = json.load(f)
        if isinstance(pairs, list):
            return [(p[0], p[1]) for p in pairs if isinstance(p, list) and len(p) == 2]
    except Exception as e:
        print(f"[警告] 精确脱敏对文件解析失败（忽略）: {e}", file=sys.stderr)
    return None

def redact_counted(text, pairs):
    """带计数的脱敏：比较替换次数。"""
    if not text:
        return text, 0
    total = 0
    for pat, rep in STRUCT_RULES + (pairs or []):
        text, n = re.subn(pat, rep, text)
        total += n
    STATS["redactions"] += total
    return text, total

def fmt_time(ms):
    if isinstance(ms, dict):
        ms = ms.get("created") or ms.get("time")
    try:
        return datetime.datetime.fromtimestamp(int(ms) / 1000).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ""

def short(s, n):
    s = (s or "").strip().replace("\r", "")
    if len(s) <= n:
        return s
    STATS["truncated"] += 1
    return s[:n] + f" …[截断,共{len(s)}字符]"

def norm_dir(p):
    return (p or "").replace("/", "\\").rstrip("\\").lower()

def project_match(directory, project):
    """精确或一级子目录匹配；F:\\vps 不再吞并 F:\\vps-other。"""
    d, p = norm_dir(directory), norm_dir(project)
    return d == p or d.startswith(p + "\\")

def render_session(db, sid, meta, with_tools, full_tools, with_reasoning, pairs):
    title, _ = redact_counted(meta["title"] or "(无标题)", pairs)
    lines = [f"# 会话: {title}",
             f"- 会话ID: `{sid}`",
             f"- 项目目录: {meta['directory']}",
             f"- 创建时间: {fmt_time(meta['time_created'])} | 最后更新: {fmt_time(meta['time_updated'])}",
             "", "---", ""]
    # 流式迭代消息（不 fetchall）
    for (seq, mdata) in db.execute(
            "select sequence, data from message where session_id=? order by sequence", (sid,)):
        try:
            m = json.loads(mdata)
        except Exception:
            STATS["parse_fail"] += 1
            continue
        STATS["messages"] += 1
        role = m.get("role", "?")
        plines = []
        for (mid,) in db.execute(
                "select id from message where session_id=? and sequence=?", (sid, seq)):
            for (pdata,) in db.execute(
                    "select data from part where message_id=? order by sequence", (mid,)):
                try:
                    p = json.loads(pdata)
                except Exception:
                    STATS["parse_fail"] += 1
                    continue
                STATS["parts"] += 1
                t = p.get("type")
                if t == "text":
                    body, _ = redact_counted(p.get("text", ""), pairs)
                    plines.append(body)
                elif t == "reasoning" and with_reasoning:
                    body, _ = redact_counted(p.get("text", ""), pairs)
                    plines.append("> [思考] " + short(body, 600).replace("\n", "\n> "))
                elif t == "tool" and with_tools:
                    tool = p.get("tool", "?")
                    st = p.get("state") or {}
                    inp, _ = redact_counted(json.dumps(st.get("input", {}), ensure_ascii=False), pairs)
                    head = f"> [工具] {tool}({short(inp, 160)}) → {st.get('status', '?')}"
                    if full_tools:
                        out, _ = redact_counted(str(st.get("output", "")), pairs)
                        head += "\n>   输出: " + short(out, 800).replace("\n", "\n>   ")
                    plines.append(head)
                elif t == "compaction":
                    plines.append("> [上下文压缩点]")
        body = "\n".join(x for x in plines if x.strip())
        if not body.strip():
            continue
        lines.append(f"## [{role}] {fmt_time(m.get('time'))}\n\n{body}\n")
    return "\n".join(lines)

def main():
    ap = argparse.ArgumentParser(description="导出 ZCode 会话为 Markdown（脱敏默认开启）")
    ap.add_argument("--project", default=r"F:\vps", help="按项目目录过滤(默认 F:\\vps，含子目录，不含同名前缀他目录)")
    ap.add_argument("--all", action="store_true", help="不过滤项目,导出全部")
    ap.add_argument("--out", help="合并输出到单个 .md 文件")
    ap.add_argument("--out-dir", default=DEFAULT_OUT_DIR, help="每会话一个文件的输出目录")
    ap.add_argument("--last", type=int, default=0, help="只导最近 N 个会话(按创建时间)")
    ap.add_argument("--title", help="按标题关键词过滤")
    ap.add_argument("--tools", action="store_true", help="包含工具调用摘要行")
    ap.add_argument("--full-tools", action="store_true", help="包含工具输出(会很大,同样脱敏)")
    ap.add_argument("--reasoning", action="store_true", help="包含模型思考段")
    ap.add_argument("--db", default=DB, help="会话库路径（默认真实库；测试/隔离导出可指向副本）")
    ap.add_argument("--pairs-file", default=EXTRA_PAIRS_FILE, help="精确脱敏对 JSON（默认受控文件）")
    ap.add_argument("--no-redact", action="store_true", help="关闭脱敏(危险)")
    ap.add_argument("--danger-confirm", action="store_true", help="与 --no-redact 同时给才生效")
    args = ap.parse_args()

    do_redact = True
    if args.no_redact:
        if not args.danger_confirm:
            sys.exit("拒绝执行：--no-redact 必须与 --danger-confirm 同用（外发请保持默认脱敏）")
        do_redact = False

    if not os.path.exists(args.db):
        sys.exit(f"找不到会话库: {args.db}")
    db = sqlite3.connect(f"file:{args.db.replace(os.sep, '/')}?mode=ro", uri=True)

    pairs = None
    if do_redact:
        pairs = load_extra_pairs(args.pairs_file)
        if pairs is None:
            print("[提示] 未配置精确脱敏对（--pairs-file），仅结构化规则生效", file=sys.stderr)

    rows = db.execute("select id, directory, title, time_created, time_updated from session").fetchall()
    sessions = [r for r in rows if args.all or project_match(r[1], args.project)]
    if args.title:
        sessions = [r for r in sessions if args.title in (r[2] or "")]
    sessions.sort(key=lambda r: r[3] or 0, reverse=True)
    if args.last:
        sessions = sessions[:args.last]
    if not sessions:
        sys.exit("没有匹配的会话")

    print(f"匹配 {len(sessions)} 个会话（库内共 {len(rows)} 个；脱敏={'开' if do_redact else '关(危险)'}）")
    os.makedirs(args.out_dir, exist_ok=True)
    combined = []
    for sid, directory, title, tc, tu in sessions:
        meta = {"directory": directory, "title": title or "(无标题)", "time_created": tc, "time_updated": tu}
        md = render_session(db, sid, meta, args.tools, args.full_tools, args.reasoning, pairs if do_redact else None)
        combined.append(md)
        safe = re.sub(r"[\\/:*?\"<>|\s]+", "_", meta["title"])[:40] or sid[-8:]
        path = os.path.join(args.out_dir, f"{safe}_{sid[-8:]}.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(md)
        print(f"  ✓ {os.path.basename(path)} ({len(md)//1024} KB)")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write("\n\n===== 会话分隔 =====\n\n".join(combined))
        print(f"合并输出: {args.out} ({os.path.getsize(args.out)//1024} KB)")
    db.close()
    print(f"统计: 消息 {STATS['messages']} | 分段 {STATS['parts']} | 脱敏替换 {STATS['redactions']} | "
          f"截断 {STATS['truncated']} | 解析失败 {STATS['parse_fail']}")
    if do_redact and STATS['redactions'] == 0:
        print("[提示] 本次导出未命中任何脱敏规则——若会话确实无敏感内容属正常，外发前请再人工抽查。")

if __name__ == "__main__":
    main()
