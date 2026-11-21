"""session_guard.py — 多会话协作锁（governance-20261118 分册01 最小包，纯标准库）。

用法:
  python scripts/session_guard.py status [--json]
  python scripts/session_guard.py lock --session <身份> --scope <scope> [--note <备注>] [--record <执行记录路径>]
  python scripts/session_guard.py unlock --lock-id <ID> --session <身份>
  python scripts/session_guard.py --self-test [--fixture-dir <dir>]

scope 层级（点分层级；冒号命名空间按整串匹配）:
  gz-prod          广州后端/DB/任务发布（含 gz-prod.* 子scope）
  device:<id>      单台设备配置/脚本/任务（R1..R11/GL/athena）
  server:<name>    单台服务器（HK/BWG/GIA/DMIT…）
  code:<module>    本地源码/脚本写协调（backend/agent/frontend/scripts）
  docs[:<主题>]    文档写协调

纪律（分册01 §1）:
  - status 完全只读；缺文件视无锁，不初始化。
  - lock 原子获取（OS互斥文件 + 原子替换），多进程争用仅一方成功。
  - 过期(>4h)只标 STALE，不自动失效/抢占/解除。
  - unlock 只准本人身份或锁ID配合本人身份；跨机回收需人工审计。
  - 注册表损坏拒写并报告，不清空。
  - 本地文件锁只是协作机制，不能阻止不调用本工具的会话；生产动作前仍需对拍远端状态。
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import platform
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK_DIR = Path(os.environ.get("SG_LOCK_DIR") or (ROOT / "backups" / ".session-locks"))
REGISTRY = LOCK_DIR / "registry.json"
MUTEX = LOCK_DIR / ".mutex"
STALE_AFTER_H = 4.0
MUTEX_STALE_S = 90.0


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _utcnow() -> float:
    return time.time()


def _pid_started(pid: int) -> str:
    try:
        if os.name == "nt":
            import ctypes

            k32 = ctypes.windll.kernel32
            h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not h:
                return "unknown"
            try:
                ft = ctypes.c_ulonglong()
                ret = k32.GetProcessTimes(h, ctypes.byref(ft), None, None, None)
                return hex(ft.value) if ret else "unknown"
            finally:
                k32.CloseHandle(h)
        else:
            with open(f"/proc/{pid}/stat", "rb") as fh:
                data = fh.read(256)
            return data.rsplit(b")", 1)[1].split()[19].decode()
    except Exception:
        return "unknown"


def _scope_conflicts(a: str, b: str) -> bool:
    """同scope或点分层级祖先关系冲突；冒号命名空间整串比较。"""
    if a == b:
        return True
    return a.startswith(b + ".") or b.startswith(a + ".")


class RegistryCorrupt(RuntimeError):
    pass


def _read_registry() -> dict:
    if not REGISTRY.exists():
        return {"version": 1, "locks": []}
    try:
        data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RegistryCorrupt(f"注册表损坏（拒绝写入，人工检查 {REGISTRY}）: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("locks"), list):
        raise RegistryCorrupt("注册表结构非法（locks 非列表）")
    return data


def _mutex_acquire(timeout_s: float = 15.0) -> int:
    """Kernel-held mutex. Never steal a live holder based on file age."""
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    deadline = _utcnow() + timeout_s
    fd = os.open(MUTEX, os.O_CREAT | os.O_RDWR, 0o600)
    if os.fstat(fd).st_size == 0:
        os.write(fd, b"0")
    while _utcnow() < deadline:
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except OSError:
            time.sleep(0.1 + secrets.randbelow(100) / 1000.0)
    os.close(fd)
    raise TimeoutError(f"获取互斥超时（{MUTEX} 被占用）")


def _mutex_release(fd: int) -> None:
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _atomic_write(path: Path, text: str) -> None:
    tmp_fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _mark_stale(locks: list) -> None:
    for item in locks:
        try:
            age_h = (_utcnow() - dt.datetime.fromisoformat(item["acquired"]).timestamp()) / 3600
        except (KeyError, ValueError):
            age_h = None
        item["stale"] = bool(age_h is not None and age_h > STALE_AFTER_H)
        item["age_hours"] = round(age_h, 2) if age_h is not None else None


def _git_state() -> dict:
    def git(*args: str) -> str:
        try:
            r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=20)
            return r.stdout.rstrip("\n") if r.returncode == 0 else ""
        except Exception:
            return ""

    return {
        "branch": git("rev-parse", "--abbrev-ref", "HEAD") or "unknown",
        "head": git("rev-parse", "HEAD") or "unknown",
        "dirty_count": len(git("status", "--porcelain").splitlines()),
    }


ROOT_ALLOWED_FILES = {
    "AGENTS.md", "CLAUDE.md", "GEMINI.md", "README.md",
    ".cursorrules", ".windsurfrules", ".clinerules",
    ".gitignore", ".gitattributes", ".zcodeignore",
    "登录信息与配置总表-20260917.md",
}
DOCS_ROOT_ALLOWED_FILES = {
    "AI协作入口.md", "INDEX.md", "设备引导包-雅典娜.json",
}
DOCS_CURRENT_ALLOWED_FILES = {
    "现势总览.md", "当前状态.md",
}


def _scan_unplaced_docs() -> list[str]:
    """只读检查根目录、docs/ 顶层、docs/current/ 顶层是否出现未归位的新建文书。"""
    found: list[str] = []
    checks = [
        (ROOT, ROOT_ALLOWED_FILES, ""),
        (ROOT / "docs", DOCS_ROOT_ALLOWED_FILES, "docs/"),
        (ROOT / "docs" / "current", DOCS_CURRENT_ALLOWED_FILES, "docs/current/"),
    ]
    for base, allow, prefix in checks:
        if not base.is_dir():
            continue
        try:
            for item in sorted(base.iterdir()):
                if item.is_file() and item.name not in allow:
                    found.append(prefix + item.name)
        except OSError:
            pass
    return found


def cmd_status(json_out: bool = False) -> int:
    """完全只读：缺文件视无锁；不创建/不重写任何文件。"""
    try:
        data = _read_registry()
    except RegistryCorrupt as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    locks = data["locks"]
    _mark_stale(locks)
    unplaced = _scan_unplaced_docs()
    info = {
        "now": _now(),
        "registry": str(REGISTRY),
        "lock_count": len(locks),
        "locks": [
            {k: item.get(k) for k in ("id", "scope", "session", "host", "pid", "acquired", "stale", "note", "record")}
            for item in locks
        ],
        "git": _git_state(),
        "overview_doc": "docs/current/现势总览.md",
        "status_doc": "docs/current/当前状态.md",
        "unplaced_docs": unplaced,
        "note": "本地协作锁不阻止不调用本工具的会话；生产动作前仍需对拍远端状态。",
    }
    if json_out:
        print(json.dumps(info, ensure_ascii=False, indent=2))
    else:
        print(f"== session_guard status @ {info['now']} ==")
        print(f"git: {info['git']['branch']} @ {info['git']['head'][:12]} dirty={info['git']['dirty_count']}")
        if not locks:
            print("无锁（registry 缺失或为空）。开工三读后可 lock。")
        for item in info["locks"]:
            flag = " [STALE]" if item.get("stale") else ""
            print(f"  {item['id']}{flag} scope={item['scope']} session={item['session']} "
                  f"host={item['host']} pid={item['pid']} acquired={item['acquired']}")
            if item.get("note"):
                print(f"    note: {item['note']}")
        if unplaced:
            print(f"[doc-hygiene] 发现未归位文书/散落文件 ({len(unplaced)}): {', '.join(unplaced[:8])}")
            print("  → 请按 AGENTS.md §5 提醒并协助用户规范落位、脱敏与更新索引（不擅删原件）。")
    return 0


def cmd_lock(session: str, scope: str, note: str, record: str) -> int:
    if not session or not scope:
        print("ERROR: --session 与 --scope 必填", file=sys.stderr)
        return 2
    fd = _mutex_acquire()
    try:
        data = _read_registry()  # 损坏即抛出，绝不覆盖
        _mark_stale(data["locks"])
        for item in data["locks"]:
            if _scope_conflicts(item["scope"], scope):
                stale = " [STALE>4h，但仍不自动抢占——联系所有者或人工审计]" if item.get("stale") else ""
                print(f"ERROR: scope 冲突：{item['scope']}（{item['stale'] and 'STALE' or 'active'}）"
                      f" 由 {item['session']} @ {item['host']} pid={item['pid']} 持有{stale}", file=sys.stderr)
                return 3
        lock = {
            "id": "lock-" + secrets.token_hex(6),
            "scope": scope,
            "session": session,
            "host": os.environ.get("COMPUTERNAME") or platform.node(),
            "pid": os.getpid(),
            "pid_started": _pid_started(os.getpid()),
            "acquired": _now(),
            "note": note or "",
            "record": record or "",
        }
        data["locks"].append(lock)
        _atomic_write(REGISTRY, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        print(f"LOCKED {lock['id']} scope={scope}")
        print(f"unlock: python scripts/session_guard.py unlock --lock-id {lock['id']} --session {session}")
        return 0
    except RegistryCorrupt as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    finally:
        _mutex_release(fd)


def cmd_unlock(lock_id: str, session: str) -> int:
    if not lock_id or not session:
        print("ERROR: --lock-id 与 --session 必填（不得省略所有者校验）", file=sys.stderr)
        return 2
    fd = _mutex_acquire()
    try:
        data = _read_registry()
        remaining, found = [], None
        for item in data["locks"]:
            if item.get("id") == lock_id:
                found = item
            else:
                remaining.append(item)
        if not found:
            print(f"ERROR: 锁不存在或已解除: {lock_id}", file=sys.stderr)
            return 4
        if found.get("session") != session or found.get("host") != (os.environ.get("COMPUTERNAME") or platform.node()):
            print(f"ERROR: 非所有者（lock 属 {found.get('session')}，你声明 {session}）；"
                  f"跨会话回收需人工审计后在注册表中处理", file=sys.stderr)
            return 5
        data["locks"] = remaining
        _atomic_write(REGISTRY, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        print(f"UNLOCKED {lock_id} (was scope={found.get('scope')})")
        return 0
    except RegistryCorrupt as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    finally:
        _mutex_release(fd)


# ---- 自测（分册01 §1 必测项，夹具目录隔离，不碰真实注册表） ----

def _self_test(fixture_dir: Path | None) -> int:
    import shutil

    # A caller-supplied directory is a parent, never a recursive-delete target.
    parent = Path(fixture_dir).resolve() if fixture_dir else None
    if parent:
        parent.mkdir(parents=True, exist_ok=True)
    fixture = Path(tempfile.mkdtemp(prefix="sg-test-", dir=parent))
    global LOCK_DIR, REGISTRY, MUTEX
    real = (LOCK_DIR, REGISTRY, MUTEX)
    real_before = real[1].read_bytes() if real[1].exists() else None
    LOCK_DIR = fixture / "locks"
    REGISTRY = LOCK_DIR / "registry.json"
    MUTEX = LOCK_DIR / ".mutex"
    results = []

    def check(name: str, ok: bool, detail: str = ""):
        results.append((name, ok, detail))

    # T1 status 零写
    rc = cmd_status(json_out=True)
    check("T1 status缺文件只读零写", rc == 0 and not REGISTRY.exists() and not LOCK_DIR.exists())

    # T2 双进程争用（子进程经 SG_LOCK_DIR 指向夹具，绝不碰真实注册表）
    subenv = {**os.environ, "SG_LOCK_DIR": str(LOCK_DIR)}
    procs = [
        subprocess.Popen([sys.executable, "-I", str(Path(__file__)), "lock",
                          "--session", f"sess-{tag}", "--scope", "gz-prod"],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=subenv)
        for tag in ("p1", "p2")
    ]
    codes = [p.wait() for p in procs]
    check("T2 双进程争用仅一方成功", sorted(codes)[0] == 0 and sorted(codes)[1] == 3, str(codes))
    data = _read_registry()
    check("T2b 注册表恰一把锁", len(data["locks"]) == 1)
    winner_id = data["locks"][0]["id"]
    winner_session = data["locks"][0]["session"]

    # T3 不同 scope 不冲突
    rc3 = cmd_lock("sess-C", "server:HK", "", "")
    check("T3 不同scope可并存", rc3 == 0 and len(_read_registry()["locks"]) == 2)

    # T3b 层级冲突：gz-prod.db 与 gz-prod
    rc3b = cmd_lock("sess-D", "gz-prod.db", "", "")
    check("T3b 层级scope冲突被拒", rc3b == 3)

    # T4 过期不抢占
    data = _read_registry()
    for item in data["locks"]:
        if item["scope"] == "server:HK":
            item["acquired"] = (dt.datetime.now().astimezone() - dt.timedelta(hours=5)).isoformat(timespec="seconds")
    _atomic_write(REGISTRY, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    rc4 = cmd_lock("sess-E", "server:HK", "", "")
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cmd_status(json_out=True)
    check("T4 过期锁不抢占仍拒新锁且标STALE", rc4 == 3 and '"stale": true' in buf.getvalue())

    # T5 非所有者解锁拒绝
    rc5 = cmd_unlock(winner_id, "sess-其他会话")
    check("T5 非所有者解锁被拒", rc5 == 5 and len(_read_registry()["locks"]) == 2)
    check("T5c 省略所有者不能解锁", cmd_unlock(winner_id, "") == 2 and len(_read_registry()["locks"]) == 2)
    # T5b 所有者解锁成功
    rc5b = cmd_unlock(winner_id, winner_session)
    check("T5b 所有者解锁成功", rc5b == 0 and len(_read_registry()["locks"]) == 1)

    # T6 注册表损坏拒写
    REGISTRY.write_text("{ broken json", encoding="utf-8")
    rc6 = cmd_lock("sess-F", "device:R2", "", "")
    check("T6 损坏注册表拒写不清空", rc6 == 2 and "{ broken json" in REGISTRY.read_text(encoding="utf-8"))

    # T7 未知锁ID解锁
    REGISTRY.unlink()
    rc7 = cmd_unlock("lock-nonexist", "anyone")
    check("T7 未知锁ID报4", rc7 == 4)

    # T8 dirty 文件不受影响（fixture 外真实文件不动）
    check("T8 真实注册表字节未被夹具触碰", (real[1].read_bytes() if real[1].exists() else None) == real_before)

    LOCK_DIR, REGISTRY, MUTEX = real
    failed = [r for r in results if not r[1]]
    for name, ok, detail in results:
        print(f"  {'PASS' if ok else 'FAIL'} {name} {detail}")
    print(f"self-test: {len(results) - len(failed)}/{len(results)} PASS")
    if fixture_dir is None:
        shutil.rmtree(fixture, ignore_errors=True)
    return 0 if not failed else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="多会话协作锁（governance-20261118/01 最小包）")
    sub = ap.add_subparsers(dest="cmd", required=False)
    sp = sub.add_parser("status")
    sp.add_argument("--json", action="store_true")
    lk = sub.add_parser("lock")
    lk.add_argument("--session", required=True)
    lk.add_argument("--scope", required=True)
    lk.add_argument("--note", default="")
    lk.add_argument("--record", default="")
    ul = sub.add_parser("unlock")
    ul.add_argument("--lock-id", required=True)
    ul.add_argument("--session", required=True)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--fixture-dir", default=None)
    args = ap.parse_args()
    if args.self_test:
        return _self_test(Path(args.fixture_dir) if args.fixture_dir else None)
    if not args.cmd:
        ap.print_usage()
        print("缺少子命令（status/lock/unlock）或 --self-test", file=sys.stderr)
        return 2
    if args.cmd == "status":
        return cmd_status(json_out=args.json)
    if args.cmd == "lock":
        return cmd_lock(args.session, args.scope, args.note, args.record)
    return cmd_unlock(args.lock_id, args.session)


if __name__ == "__main__":
    sys.exit(main())
