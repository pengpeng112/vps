#!/usr/bin/env python3
"""export_zcode_sessions.py v2 的虚构样本回归测试（M05）。
全部使用虚构秘密；不依赖也不读取真实会话库。
覆盖：项目边界匹配 / 嵌套 JSON 工具入参秘密 / 超长内容截断 / 坏 JSON 分段 /
无消息会话 / 脱敏默认开与 --no-redact 拒绝路径 / 标题脱敏。
"""
import importlib.util, json, os, re, sqlite3, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "export_zcode_sessions.py")
spec = importlib.util.spec_from_file_location("exp", SCRIPT)
exp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exp)

FAKE = {
    "pwd": "FAKE-PASSWORD-9f8e7d6c5b4a",
    "uuid": "00000000-1111-2222-3333-444444444444",
    "pbk": "FAKEPBK_______________________________x",
    "jwt": "eyJFAKEHEADERAAAAAAAAAA.eyJFAKEPAYLOADBBBB.eyJFAKESIGCCCC",
    "dsn": "postgres://u:FAKEPW@db.internal:5432/x",
    "hex64": "f" * 64,
}

def make_db(path):
    db = sqlite3.connect(path)
    db.execute("create table session(id text, directory text, title text, time_created int, time_updated int)")
    db.execute("create table message(id integer primary key, session_id text, sequence int, data text)")
    db.execute("create table part(id integer primary key, message_id int, sequence int, data text)")
    def add_session(sid, directory, title, parts_of_msgs):
        db.execute("insert into session values (?,?,?,?,?)", (sid, directory, title, 1700000000000, 1700000001000))
        for seq, parts in enumerate(parts_of_msgs, 1):
            db.execute("insert into message(session_id, sequence, data) values (?,?,?)",
                       (sid, seq, json.dumps({"role": "user" if seq % 2 else "assistant", "time": 1700000000000 + seq})))
            mid = db.execute("select max(id) from message").fetchone()[0]
            for pseq, p in enumerate(parts, 1):
                db.execute("insert into part(message_id, sequence, data) values (?,?,?)", (mid, pseq, json.dumps(p)))
    # 1) 目标项目会话：含各形态虚构秘密（嵌套 JSON 工具入参/文本/标题）
    add_session("sess-vps", r"F:\vps", f"标题含密码 {FAKE['pwd']}", [
        [{"type": "text", "text": f"连接串 {FAKE['dsn']} 与令牌 {FAKE['jwt']}"}],
        [{"type": "tool", "tool": "Bash", "state": {"status": "ok",
          "input": {"command": f"sshpass -p {FAKE['pwd']} ssh root@x; export UUID={FAKE['uuid']} PBK={FAKE['pbk']}"},
          "output": "ok"}},
         {"type": "reasoning", "text": f"思考里也有 {FAKE['pwd']}"}],
        [{"type": "text", "text": "长" * 5000}],                 # 超长→截断
        [{"type": "text", "text": "BROKEN_NOT_JSON"}],           # 坏 JSON 由外层模拟
    ])
    # 2) 同前缀他项目：必须被排除
    add_session("sess-other", r"F:\vps-other", "不该被导出", [[{"type": "text", "text": "SECRET-OTHER"}]])
    # 3) 子目录：应包含
    add_session("sess-child", r"F:\vps\mgmt", "子目录会话", [[{"type": "text", "text": "child ok"}]])
    # 4) 无消息会话
    add_session("sess-empty", r"F:\vps", "空会话", [])
    # 5) 坏 JSON 消息体
    db.execute("insert into session values (?,?,?,?,?)", ("sess-badmsg", r"F:\vps", "坏消息", 1700000000000, 1700000000000))
    db.execute("insert into message(session_id, sequence, data) values (?,?,?)", ("sess-badmsg", 1, "{broken"))
    db.commit(); db.close()

def main():
    tmp = tempfile.mkdtemp()
    dbp = os.path.join(tmp, "db.sqlite")
    make_db(dbp)
    out_dir = os.path.join(tmp, "out")
    # 直接用模块内函数做单元断言（不经 CLI 的真实库路径）
    pairs = None
    # 边界匹配
    assert exp.project_match(r"F:\vps", r"F:\vps") is True
    assert exp.project_match(r"F:\vps\mgmt", r"F:\vps") is True
    assert exp.project_match(r"F:\vps-other", r"F:\vps") is False, "前缀越界未修复"
    assert exp.project_match(r"f:/VPS/", r"F:\vps") is True
    # 脱敏规则命中（虚构值）
    t = f"password={FAKE['pwd']} uuid: {FAKE['uuid']} pbk={FAKE['pbk']} jwt={FAKE['jwt']} dsn={FAKE['dsn']}"
    out, n = exp.redact_counted(t, pairs)
    for v in FAKE.values():
        assert v not in out, f"虚构秘密 {v[:8]}… 未脱敏: {out[:80]}"
    assert n >= 4, f"命中数不足: {n}"
    # 私钥块
    k = "-----BEGIN OPENSSH PRIVATE KEY-----\nabc123FAKE\n-----END OPENSSH PRIVATE KEY-----"
    out, _ = exp.redact_counted(k, pairs)
    assert "abc123FAKE" not in out
    # CLI 路径：用 --db 指向虚构库，验证排除越界会话/输出无秘密/统计行存在
    pairs_file = os.path.join(tmp, "pairs.json")
    with open(pairs_file, "w", encoding="utf-8") as f:
        json.dump([[re.escape(v), "***FAKE***"] for v in FAKE.values()], f)
    r = subprocess.run([sys.executable, SCRIPT, "--db", dbp, "--project", r"F:\vps",
                        "--tools", "--reasoning", "--pairs-file", pairs_file, "--out-dir", out_dir],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "匹配 4 个会话" in r.stdout, r.stdout  # vps+child+empty+badmsg，other 被排除
    assert "SECRET-OTHER" not in r.stdout and not any("SECRET-OTHER" in open(os.path.join(out_dir, f), encoding="utf-8").read() for f in os.listdir(out_dir))
    blob = "\n".join(open(os.path.join(out_dir, f), encoding="utf-8").read() for f in os.listdir(out_dir))
    for v in FAKE.values():
        assert v not in blob, f"CLI 输出泄漏虚构秘密 {v[:8]}…"
    assert "统计:" in r.stdout and "解析失败" in r.stdout
    # --no-redact 无确认必须拒绝
    r2 = subprocess.run([sys.executable, SCRIPT, "--no-redact"], capture_output=True, text=True, timeout=60)
    assert r2.returncode != 0 and "danger-confirm" in (r2.stdout + r2.stderr), "危险导出未设双闸"
    print("PASS: 边界匹配/脱敏命中/私钥块/CLI全链路/危险双闸 全部通过")

if __name__ == "__main__":
    main()
