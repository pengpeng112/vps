"""build_gz_api.py — gz-api 受控构建器（governance-20261118 分册01 最小包）。

check-only：只做本地校验+构建，不部署、不 SSH、不写生产库、不写 logs。
产物与 manifest 输出到 backups/builds/<时间戳>/（gitignored），不覆盖旧产物。

步骤（分册01 §2）：
  1. golden.yaml 与全部迁移 SQL 的 CRLF/LF 检查（CRLF 拒绝）
  2. 源码 HEAD、工作树内容摘要、dirty 清单（显式 --review-range 允许已审核改动）
  3. mgmt/backend 内 go vet ./... 与 go test ./...（不设 ROUTERMGMT_TEST_DSN，
     数据库集成测试自动跳过；绝不连生产）
  4. 交叉编译 GOOS=linux GOARCH=amd64 CGO_ENABLED=0（--arch 可改，须与目标机对拍）
  5. manifest：源码/模板/每条迁移 SHA、Go 版本、vet/test 摘要、二进制 SHA256/大小

构建成功 != 可部署：生产迁移水位/约束/回退由分册03核实。
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "mgmt" / "backend"
GOLDEN = BACKEND / "internal" / "clash" / "golden.yaml"
MIGRATIONS = BACKEND / "internal" / "db" / "migrations"
BUILDS = ROOT / "backups" / "builds"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def run(cmd: list[str], cwd: Path, timeout_s: int = 600, env_extra: dict | None = None) -> dict:
    env = {**os.environ, **(env_extra or {})}
    # 绝不给测试传任何 DSN：清空集成测试变量，数据库测试只能跳过
    env.pop("ROUTERMGMT_TEST_DSN", None)
    env.pop("DATABASE_URL", None)
    env.pop("MIGRATE_DATABASE_URL", None)
    env.pop("ROUTERMGMT_DB_DSN", None)
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout_s, env=env)
    return {"cmd": cmd[:2], "rc": p.returncode, "stdout_tail": p.stdout[-4000:], "stderr_tail": p.stderr[-4000:]}


def git_state() -> dict:
    def g(*args: str) -> str:
        r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=30)
        return r.stdout.rstrip("\n") if r.returncode == 0 else ""
    return {"branch": g("rev-parse", "--abbrev-ref", "HEAD"), "head": g("rev-parse", "HEAD"),
            "dirty": g("status", "--porcelain", "--untracked-files=all").splitlines()}


def main() -> int:
    ap = argparse.ArgumentParser(description="gz-api 受控构建（check-only，不部署）")
    ap.add_argument("--goos", default="linux")
    ap.add_argument("--goarch", default="amd64")
    ap.add_argument("--review-range", default="",
                    help="显式声明允许进入构建的已审核改动前缀（逗号分隔，如 mgmt/backend,scripts）")
    ap.add_argument("--skip-tests", action="store_true", help="跳过 go test（vet 仍跑）")
    ap.add_argument("--check-only", action="store_true", help="仅校验（LF/源码状态/vet/test），不产出二进制")
    ap.add_argument("--output-dir", default="", help="显式产物目录（默认 backups/builds/<时间戳>）")
    args = ap.parse_args()

    report: dict = {"started": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                    "mode": "check-only", "deploys": False, "steps": []}

    # 1. LF 检查
    offenders = []
    files = [GOLDEN, *sorted(MIGRATIONS.glob("*.sql"))]
    for f in files:
        raw = f.read_bytes()
        if b"\r\n" in raw or b"\r" in raw:
            offenders.append(str(f.relative_to(ROOT)))
    report["lf_check"] = {"files": len(files), "crlf_offenders": offenders}
    if offenders:
        print(f"FAIL: CRLF 检测到（§5.3 禁令）: {offenders}")
        return 1
    print(f"[1/5] LF 检查 PASS（golden + {len(files)-1} 迁移）")

    # 2. 源码状态
    gs = git_state()
    allowed = [p.strip() for p in args.review_range.split(",") if p.strip()] if args.review_range else []
    # 常量白名单：git 元文件属逐字审核的仓库收口改动，永远允许
    allowed += [".gitattributes", ".gitignore"]
    unexpected = []
    for line in gs["dirty"]:
        path = line[3:].strip().strip('"').split(" -> ")[-1]
        if allowed and any(path == a.rstrip('/') or path.startswith(a.rstrip('/') + '/') for a in allowed):
            continue
        unexpected.append(line)
    report["source"] = {**gs, "review_range": allowed, "unexpected_dirty": unexpected}
    if unexpected:
        print("FAIL: 存在未审核范围外的工作树改动（先审核或用 --review-range 显式声明）：")
        for u in unexpected:
            print("   ", u)
        return 1
    print(f"[2/5] 源码状态：{gs['branch']}@{gs['head'][:12]} dirty={len(gs['dirty'])}（全部在已审核范围）")

    # 3. vet + test
    vet = run(["go", "vet", "./..."], BACKEND)
    report["go_vet"] = {"rc": vet["rc"], "stderr_tail": vet["stderr_tail"]}
    if vet["rc"] != 0:
        print("FAIL: go vet 未通过")
        print(vet["stderr_tail"])
        return 1
    print("[3/5] go vet PASS")
    if not args.skip_tests:
        t = run(["go", "test", "./..."], BACKEND, timeout_s=900)
        report["go_test"] = {"rc": t["rc"], "stdout_tail": t["stdout_tail"]}
        if t["rc"] != 0:
            print("FAIL: go test 未通过")
            print(t["stdout_tail"])
            return 1
        print("[4/5] go test PASS（数据库集成测试未设 DSN 自动跳过）")
    else:
        report["go_test"] = {"skipped": True}

    if args.check_only:
        report["finished"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
        report["check_only"] = True
        print("[check-only] ALL GREEN（LF/源码范围/vet/test 通过；不产出二进制）")
        return 0

    # 5. 构建
    gv = run(["go", "version"], BACKEND)
    out_dir = Path(args.output_dir) if args.output_dir else BUILDS / dt.datetime.now().strftime("gz-api-%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=False)
    bin_path = out_dir / "server"
    build = run(["go", "build", "-trimpath", "-o", str(bin_path), "./cmd/server"], BACKEND,
                env_extra={"GOOS": args.goos, "GOARCH": args.goarch, "CGO_ENABLED": "0"})
    if build["rc"] != 0:
        report["build"] = {"rc": build["rc"], "stderr_tail": build["stderr_tail"]}
        (out_dir / "manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("FAIL: go build 失败，manifest 已记录")
        return 1

    manifest = {
        **report,
        "finished": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "go_version": gv["stdout_tail"].strip(),
        "target": {"goos": args.goos, "goarch": args.goarch, "cgo": False},
        "golden_yaml_sha256": sha256_file(GOLDEN),
        "migrations": {p.name: sha256_file(p) for p in sorted(MIGRATIONS.glob("*.sql"))},
        "binary": {"path": str(bin_path.relative_to(ROOT)), "size": bin_path.stat().st_size,
                   "sha256": sha256_file(bin_path)},
        "warning": "构建成功不等于可部署：生产迁移水位/约束/兼容回退由分册03核实后才可上线",
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"[5/5] 构建完成：{bin_path}")
    print(f"      sha256={manifest['binary']['sha256']} size={manifest['binary']['size']}")
    print(f"      manifest={out_dir / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
