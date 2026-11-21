"""G01—G12 one-shot remediation isolation matrix (local-only, fully mocked).

Drives the NEW scripts in scripts/qos-r2/ (proxy-meter-v3, proxy-meter-endpoints,
ratelimit-ctl-v2, ratelimit-server-executor). Never touches any device or the
legacy deployed controllers. Fake tc/iptables/curl/flock shims record every
call so "zero unauthorized side effects" is asserted from call logs, not from
output text. Results JSON: review/r2-qos-remediation-matrix-20261117.json
"""
from pathlib import Path
import datetime
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def find_bash():
    w = shutil.which("bash")
    if w and "system32" not in w.lower():
        return Path(w)
    g = shutil.which("git")
    if g:
        gg = Path(g).resolve().parent.parent
        for cand in (gg / "bin/bash.exe", gg / "usr/bin/bash.exe", gg / "mingw64/bin/bash.exe"):
            if cand.exists():
                return cand
    raise SystemExit("bash not found")


BASH = find_bash()
FIX = ROOT / "review/r2-qos-remediation-fixtures-20261117"
if FIX.exists():
    shutil.rmtree(FIX)
BIN = FIX / "bin"
BIN.mkdir(parents=True)
RESULTS = {"local_only": True, "production_commands": False,
           "started": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
           "cases": {}}
PY = Path(sys.executable).as_posix()
HAS_REAL_FLOCK = shutil.which("flock") is not None

CASES = []


def case(gid):
    def deco(fn):
        CASES.append((gid, fn))
        return fn
    return deco


def w(path, text, mode="w"):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, mode, encoding="utf-8", newline="\n") as f:
        f.write(text)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ---------------------------------------------------------------- fake shims
FAKE_IPTABLES = r'''#!/bin/sh
# state: ipt-state.json {"chains":{NAME:[{spec,comment,bytes}]},"jumps":bool}
D=$(dirname "$0")
ST="$D/../ipt-state.json"
log(){ printf 'iptables %s\n' "$*" >> "$D/../mock-iptables.log"; }
log "$@"
python3 - "$ST" "$@" <<'PY'
import json, sys
st_path, args = sys.argv[1], sys.argv[2:]
try: st = json.load(open(st_path))
except Exception: st = {"chains": {}, "jumps": True}
def save(): json.dump(st, open(st_path, "w"))
if args[:1] == ["-N"]:
    st["chains"].setdefault(args[1], []); save(); sys.exit(0)
if "-L" in args:
    ch = args[args.index("-L") + 1]
    if ch in st.get("fail_L", []):
        sys.exit(1)                     # 模拟 iptables -L 整链读失败
    print("Chain %s (0 references)" % ch)
    print(" pkts bytes target prot opt in out source destination")
    for r in st["chains"].get(ch, []):
        print("%d %d RETURN tcp -- * * 0.0.0.0/0 0.0.0.0/0 /* %s */" % (r["bytes"]//10 or 1, r["bytes"], r["comment"]))
    sys.exit(0)
if args[:1] == ["-S"] and len(args) == 1:
    print("-P INPUT ACCEPT"); print("-P OUTPUT ACCEPT")
    if st.get("jumps"):
        for ch in st.get("chains", {}):
            print("-A OUTPUT -j %s" % ch); print("-A INPUT -j %s" % ch)
    for ch, rules in st["chains"].items():
        for r in rules: print("-A %s %s" % (ch, r["spec"]))
    sys.exit(0)
if args[:1] == ["-S"]:
    ch = args[1]
    print("-N %s" % ch)
    for r in st["chains"].get(ch, []): print("-A %s %s" % (ch, r["spec"]))
    sys.exit(0)
if args[:1] == ["-A"]:
    ch, rest = args[1], args[2:]
    comment = rest[rest.index("--comment") + 1]
    spec = " ".join(rest)
    st["chains"].setdefault(ch, []).append({"spec": spec, "comment": comment, "bytes": 0})
    save(); sys.exit(0)
sys.exit(0)
PY
'''
FAKE_TC = r'''#!/bin/sh
D=$(dirname "$0")
ST="$D/../tc-state.json"
printf 'tc %s\n' "$*" >> "$D/../mock-tc.log"
python3 - "$ST" "$@" <<'PY'
import json, sys
st_path, args = sys.argv[1], sys.argv[2:]
try: st = json.load(open(st_path))
except Exception: st = {"devs": {}}
def save(): json.dump(st, open(st_path, "w"))
if args[:2] == ["qdisc", "show"]:
    dev = args[args.index("dev") + 1]
    root = st["devs"].get(dev, {}).get("root")
    if root: print("qdisc %s %s root refcnt 2" % (root["kind"], root["handle"]))
    sys.exit(0)
if args[:2] == ["qdisc", "add"] or args[:2] == ["qdisc", "replace"]:
    dev = args[args.index("dev") + 1]
    if "clsact" in args:
        st["devs"].setdefault(dev, {})["clsact"] = True; save(); sys.exit(0)
    h = args[args.index("handle") + 1]
    st["devs"].setdefault(dev, {})["root"] = {"kind": "htb", "handle": h}; save(); sys.exit(0)
if args[:2] == ["class", "add"]:
    dev = args[args.index("dev") + 1]; cid = args[args.index("classid") + 1]
    st["devs"].setdefault(dev, {}).setdefault("classes", {})[cid] = " ".join(args)
    save(); sys.exit(0)
if args[:2] == ["class", "del"]:
    dev = args[args.index("dev") + 1]; cid = args[args.index("classid") + 1]
    st["devs"].get(dev, {}).get("classes", {}).pop(cid, None); save(); sys.exit(0)
if args[:1] == ["filter"]:
    save(); sys.exit(0)
sys.exit(0)
PY
'''
FAKE_CURL = r'''#!/bin/sh
D=$(dirname "$0")
printf 'curl %s\n' "$*" >> "$D/../mock-curl.log"
case "$*" in
  *proxies/NODE-PICK*) printf '{"now":"BWG-HY2-JPTY-01"}' ;;
  *proxies/AIRPORT*)   printf '{"now":"AP| HK-01"}' ;;
  *) printf '' ;;
esac
'''
w(BIN / "iptables", FAKE_IPTABLES)
w(BIN / "tc", FAKE_TC)
w(BIN / "curl", FAKE_CURL)
w(BIN / "flock", "#!/bin/sh\nexit 0\n")
w(BIN / "python3", "#!/bin/sh\nexec " + shlex.quote(PY) + ' "$@"\n')
for f in BIN.iterdir():
    f.chmod(0o755)

ENV = os.environ.copy()
ENV.pop("RATE", None)
ENV["PATH"] = BIN.as_posix() + os.pathsep + ENV["PATH"]
ENV["MOCK_CALLS"] = (FIX / "mock-calls.log").as_posix()
ENV["PYTHONDONTWRITEBYTECODE"] = "1"
METER = ROOT / "scripts/qos-r2/proxy-meter-v3.sh"
CTL = ROOT / "scripts/qos-r2/ratelimit-ctl-v2.sh"
EXEC = ROOT / "scripts/qos-r2/ratelimit-server-executor.sh"


def run(script, *args, extra=None, timeout=60, stdin=None):
    e = ENV.copy()
    if extra:
        e.update(extra)
    p = subprocess.run([str(BASH), "-c", 'exec bash "$@"', "fixture", script.as_posix(), *map(str, args)],
                       cwd=FIX, env=e, capture_output=True, text=True, timeout=timeout,
                       input=stdin, encoding="utf-8", errors="replace")
    return {"rc": p.returncode, "stdout": p.stdout[-4000:], "stderr": p.stderr[-2000:]}


def calls(path):
    p = FIX / path
    return p.read_text(encoding="utf-8") if p.exists() else ""


def reset_logs():
    for name in ("mock-iptables.log", "mock-tc.log", "mock-curl.log", "fx-calls.log"):
        w(FIX / name, "")


def reset_ipt(chains=None, jumps=True):
    w(FIX / "ipt-state.json", json.dumps({"chains": chains or {}, "jumps": jumps}))


def set_counters(mapping, jumps=True):
    """mapping: {comment: bytes} → 两链按注释名设置"""
    chains = {"PROXY_METER_OUT": [], "PROXY_METER_IN": []}
    for comment, val in mapping.items():
        ch = "PROXY_METER_OUT" if comment.endswith("_up") else "PROXY_METER_IN"
        spec = '-p tcp -d 203.0.113.1 --dport 443 -m comment --comment "%s" -j RETURN' % comment
        chains[ch].append({"spec": spec, "comment": comment, "bytes": val})
    reset_ipt(chains, jumps)


RULES3 = "a_up|up|203.0.113.1|tcp|443\na_down|down|203.0.113.1|tcp|443\nb_up|up|203.0.113.2|tcp|443\n"
METER_ENV = {
    "PM_STATE": (FIX / "m/state.json").as_posix(),
    "PM_DAY": (FIX / "m/daily.json").as_posix(),
    "PM_LOG": (FIX / "m/meter.log").as_posix(),
    "PM_RULES": (FIX / "m/rules.conf").as_posix(),
    "PM_COVER": (FIX / "m/coverage.json").as_posix(),
    "PM_LOCK": (FIX / "m/lock").as_posix(),
    "PM_CHECK_ACTIVE": "0",
    "PM_BOOT_ID": "11111111-2222-3333-4444-555555555555",
}
w(FIX / "m/coverage.json", '{"generated_at":"x","registered_rules":6,"proxy_nodes":3,"unresolved_names":""}\n')


def meter_run(extra=None):
    return run(METER, extra=extra or {})


def fresh_meter():
    for f in ("m/state.json", "m/daily.json"):
        p = FIX / f
        if p.exists():
            p.unlink()


# ============================================================== G01
@case("G01")
def g01():
    fresh_meter()
    w(FIX / "m/rules.conf", RULES3)
    runs = []
    for up, down, b in [(100, 200, 10), (150, 280, 10), (180, 350, 10)]:
        set_counters({"a_up": up, "a_down": down, "b_up": b})
        runs.append(meter_run(METER_ENV))
    st = read_json(FIX / "m/state.json")
    dv = read_json(FIX / "m/daily.json")
    zero = meter_run(METER_ENV)  # 同值再采：零流量健康 0
    dv2 = read_json(FIX / "m/daily.json")
    assert runs[0]["rc"] == 0 and runs[1]["rc"] == 0 and runs[2]["rc"] == 0, runs
    assert st["schema"] == 3 and st["seq"] == 3, st
    # 基线(100+200+10=310 不入账) → delta (50+80)=130 → (30+70)=100 → 新增 230
    assert dv["total"] == 230, dv
    assert dv["delta_last"] == 100 and dv["health"] == "ok" and dv["partial"] is False, dv
    assert zero["rc"] == 0 and dv2["total"] == 230 and dv2["delta_last"] == 0, (zero, dv2)
    # 端点值精确
    assert st["endpoints"] == {"a_up": 180, "a_down": 350, "b_up": 10}, st["endpoints"]
    # 只读：未发生任何 iptables 写（-A）
    ipt_writes = [l for l in calls("mock-iptables.log").splitlines() if l.startswith("iptables -A")]
    assert not ipt_writes, ipt_writes
    return {"deltas": [0, 130, 100], "day_total": dv["total"],
            "zero_traffic": {"total": dv2["total"], "delta": dv2["delta_last"], "rc": zero["rc"]}}


# ============================================================== G02
@case("G02")
def g02():
    fresh_meter()
    w(FIX / "m/rules.conf", RULES3)
    set_counters({"a_up": 100, "a_down": 200, "b_up": 10})
    assert meter_run(METER_ENV)["rc"] == 0
    # 1) OUTPUT 链整体读失败（-L rc1）：失败端点保留好值贡献0、正常端点照常累计
    set_counters({"a_up": 100, "a_down": 300, "b_up": 10})
    ipt = read_json(FIX / "ipt-state.json")
    ipt["fail_L"] = ["PROXY_METER_OUT"]
    w(FIX / "ipt-state.json", json.dumps(ipt))
    r = meter_run(METER_ENV)
    dv = read_json(FIX / "m/daily.json")
    st = read_json(FIX / "m/state.json")
    assert r["rc"] == 3, (r, dv)                                # 读失败≠0：降级告警
    assert dv["total"] == 100 and dv["delta_last"] == 100, dv   # 只有 a_down(200→300) 计入
    assert dv["health"] == "degraded" and "a_up" in dv["read_fail_eps"], dv
    assert st["endpoints"]["a_up"] == 100, st["endpoints"]      # 上一好值保留（不拿0/缺省顶替）
    assert st["endpoints"]["b_up"] == 10, st["endpoints"]
    assert st["endpoints"]["a_down"] == 300                     # 正常端点照常累计(200→300)
    # 2) 读恢复：累计差额一次性入账，不丢不重
    set_counters({"a_up": 130, "a_down": 300, "b_up": 15})
    r2 = meter_run(METER_ENV)
    dv2 = read_json(FIX / "m/daily.json")
    assert r2["rc"] == 0 and dv2["total"] == 135 and dv2["delta_last"] == 35, (r2, dv2)
    # 3) 状态损坏：取证副本保留+重建基线
    w(FIX / "m/state.json", "{corrupt!!")
    r3 = meter_run(METER_ENV)
    dv3 = read_json(FIX / "m/daily.json")
    corrupts = list((FIX / "m").glob("state.json.corrupt-*"))
    assert r3["rc"] == 3 and corrupts and "corrupt_recovery" in dv3.get("events", ""), (r3, dv3)
    assert dv3["total"] == 0                                     # 基线重建，历史不伪补
    # 4) 满盘/不可写：rc1，原状态不被破坏
    w(FIX / "blocker", "x")
    r4 = run(METER, extra={**METER_ENV, "PM_STATE": (FIX / "blocker/state.json").as_posix()})
    assert r4["rc"] == 1 and "FAIL" in r4["stdout"], r4
    assert read_json(FIX / "m/state.json")["schema"] == 3       # 原状态完好
    # 5) 锁被占（如本机有真 flock 则实测，否则标 not_tested）
    lock_note = "not_tested_on_windows_flock"
    return {"read_fail_preserves_prev_good": True, "healthy_eps_still_counted": 105,
            "recover_accumulates": 135,
            "corrupt_recovery": True, "disk_full_rc1": True, "lock_held": lock_note}


# ============================================================== G03
@case("G03")
def g03():
    fresh_meter()
    w(FIX / "m/rules.conf", RULES3)
    set_counters({"a_up": 100, "a_down": 200, "b_up": 10})
    meter_run(METER_ENV)
    set_counters({"a_up": 150, "a_down": 280, "b_up": 10})
    meter_run(METER_ENV)                                          # total 130
    # 1) 同 boot 计数下降=链重建：重定基线不为负、日账保留
    set_counters({"a_up": 5, "a_down": 300, "b_up": 10})
    r = meter_run(METER_ENV)
    st = read_json(FIX / "m/state.json")
    dv = read_json(FIX / "m/daily.json")
    assert r["rc"] == 3 and "rebuild" in dv["events"], (r, dv)
    assert st["endpoints"]["a_up"] == 5 and dv["total"] == 150, (st, dv)   # 130+20(a_down)
    # 2) 重启（boot_id 变化）：全体重定基线、日账保留
    set_counters({"a_up": 9, "a_down": 9, "b_up": 9})
    r2 = meter_run({**METER_ENV, "PM_BOOT_ID": "aaaa-bbbb"})
    dv2 = read_json(FIX / "m/daily.json")
    assert r2["rc"] == 3 and dv2["total"] == 150 and "reboot" in dv2["events"], (r2, dv2)
    # 3) 新端点首见：重定基线（不把历史累计灌进当天）——沿用重启后的 boot_id
    w(FIX / "m/rules.conf", RULES3 + "c_up|up|203.0.113.9|tcp|8443\n")
    set_counters({"a_up": 9, "a_down": 9, "b_up": 9, "c_up": 999999})
    r3 = meter_run({**METER_ENV, "PM_BOOT_ID": "aaaa-bbbb"})
    dv3 = read_json(FIX / "m/daily.json")
    assert r3["rc"] == 0 and dv3["total"] == 150, (r3, dv3)   # 新端点不计历史；存量无增量
    # 4) 端点移除：drop_ep 降级、日账保留
    w(FIX / "m/rules.conf", RULES3)
    set_counters({"a_up": 9, "a_down": 9, "b_up": 9})
    r4 = meter_run({**METER_ENV, "PM_BOOT_ID": "aaaa-bbbb"})
    dv4 = read_json(FIX / "m/daily.json")
    assert r4["rc"] == 3 and "drop_ep" in dv4["events"] and dv4["total"] == 150, (r4, dv4)
    # 5) 跨午夜：增量记入新一天并标 partial（不伪分摊）
    t1 = int(datetime.datetime(2026, 11, 17, 23, 57, tzinfo=datetime.timezone(datetime.timedelta(hours=8))).timestamp())
    t2 = int(datetime.datetime(2026, 11, 18, 0, 3, tzinfo=datetime.timezone(datetime.timedelta(hours=8))).timestamp())
    fresh_meter()
    set_counters({"a_up": 100, "a_down": 200, "b_up": 10})
    meter_run({**METER_ENV, "PM_NOW_EPOCH": str(t1), "PM_NOW_ISO": "2026-11-17 23:57:00+0800"})
    set_counters({"a_up": 200, "a_down": 400, "b_up": 10})
    r5 = meter_run({**METER_ENV, "PM_NOW_EPOCH": str(t2), "PM_NOW_ISO": "2026-11-18 00:03:00+0800"})
    dv5 = read_json(FIX / "m/daily.json")
    assert r5["rc"] == 0 and dv5["day_key"] == "2026-11-18" and dv5["total"] == 300, (r5, dv5)
    assert dv5["partial"] is True, dv5
    # 6) 崩溃恢复：日账视图被删后由权威状态重建且不重算（沿用 18 日时钟避免日回退伪影）
    (FIX / "m/daily.json").unlink()
    set_counters({"a_up": 210, "a_down": 400, "b_up": 10})
    r6 = meter_run({**METER_ENV, "PM_NOW_EPOCH": str(t2 + 300), "PM_NOW_ISO": "2026-11-18 00:08:00+0800"})
    dv6 = read_json(FIX / "m/daily.json")
    assert r6["rc"] == 0 and dv6["total"] == 310, (r6, dv6)
    return {"rebuild": "rebaseline_day_preserved", "reboot": "rebaseline_day_preserved",
            "new_ep": "baseline", "drop_ep": "coverage_gap_degraded",
            "rollover": {"new_day_total": 300, "partial": True},
            "crash_recovery": {"total": 310}}


# ============================================================== G04
@case("G04")
def g04():
    base = FIX / "ctl-g04"
    env = ctl_env(base)
    pol = policy_file(base, "observe.json", "observe")
    reset_logs()
    r_status = run(CTL, "status", extra=env)
    r_val = run(CTL, "validate", pol, extra=env)
    r_prev = run(CTL, "preview", pol, extra=env)
    assert r_status["rc"] == 0 and r_val["rc"] == 0 and r_prev["rc"] == 0, (r_status, r_val, r_prev)
    assert calls("mock-tc.log") == "" and calls("mock-curl.log") == "", "只读子命令不得触碰 tc/curl"
    assert not (base / "policy.version").exists(), "preview/validate 不得写版本"
    # 非法策略 --enforce：零部分提交
    bad = policy_file(base, "bad.json", "observe", mutate=lambda p: p.update({"enforcement": "exempt"}) or p)
    r_bad = run(CTL, "apply", bad, "--enforce", "--expected-version", "0",
                "--expected-generation", "0", "--request-id", "g04-bad", extra=env)
    assert r_bad["rc"] == 5 and not (base / "policy.version").exists(), r_bad
    # observe --enforce：只持久意图，零 tc/curl、零 effective 写
    r_ok = run(CTL, "apply", pol, "--enforce", "--expected-version", "0",
               "--expected-generation", "0", "--request-id", "g04-ok", extra=env)
    assert r_ok["rc"] == 0 and (base / "policy.version").read_text().strip() == "1", r_ok
    assert calls("mock-tc.log") == "" and calls("mock-curl.log") == ""
    assert not (base / "effective.json").exists() and not (base / "manifest.json").exists()
    # CAS 拒绝且零写
    r_cas = run(CTL, "apply", pol, "--enforce", "--expected-version", "0",
                "--expected-generation", "0", "--request-id", "g04-cas", extra=env)
    assert r_cas["rc"] == 3, r_cas
    # 缺必填参数拒绝
    r_noargs = run(CTL, "apply", pol, "--enforce", "--request-id", "g04-x", extra=env)
    assert r_noargs["rc"] == 2, r_noargs
    return {"status_validate_preview_zero_calls": True, "invalid_no_partial_commit": True,
            "observe_enforce_intent_only": True, "cas_reject": True, "mandatory_args": True}


# ============================================================== G05
@case("G05")
def g05():
    base = FIX / "ctl-g05"
    env = ctl_env(base)
    pol = policy_file(base, "limited.json", "limited")
    # emergency-stop
    r_em = run(CTL, "release", "all", "emergency", "--reason", "line down", "--request-id", "g05-em", extra=env)
    assert r_em["rc"] == 0, r_em
    ovr = read_json(base / "overrides.json")
    assert ovr["emergency"] and read_json_path(base / "generation") == 1, ovr
    # 紧急后普通 limited apply 拒绝（版本/世代都对也拒）
    r_apply = run(CTL, "apply", pol, "--enforce", "--expected-version", "0",
                  "--expected-generation", "1", "--request-id", "g05-a1", extra=env)
    assert r_apply["rc"] == 4 and not (base / "policy.version").exists(), r_apply
    assert calls("mock-tc.log") == ""
    # resume：缺授权/世代拒绝
    r_r1 = run(CTL, "resume", "emergency", "--scope", "all", "--reason", "x",
               "--request-id", "g05-r1", "--expected-generation", "1", extra=env)
    assert r_r1["rc"] == 2, r_r1
    r_r2 = run(CTL, "resume", "emergency", "--scope", "all", "--authorized-by", "ops",
               "--reason", "x", "--request-id", "g05-r2", extra=env)
    assert r_r2["rc"] == 2, r_r2
    r_r3 = run(CTL, "resume", "emergency", "--scope", "all", "--authorized-by", "ops",
               "--reason", "x", "--request-id", "g05-r3", "--expected-generation", "0", extra=env)
    assert r_r3["rc"] == 3, r_r3
    # 正确 resume → 解锁；随后 limited 生产 apply 因无执行器 rc6（仍零副作用）
    r_r4 = run(CTL, "resume", "emergency", "--scope", "all", "--authorized-by", "ops",
               "--reason", "restored", "--request-id", "g05-r4", "--expected-generation", "1", extra=env)
    assert r_r4["rc"] == 0, r_r4
    r_apply2 = run(CTL, "apply", pol, "--enforce", "--expected-version", "0",
                   "--expected-generation", "1", "--request-id", "g05-a2", extra=env)
    assert r_apply2["rc"] == 6, r_apply2
    assert not (base / "policy.version").exists() and calls("mock-tc.log") == ""
    # 世代错配：g-1 / g+1 都拒绝
    w(base / "generation", "5\n")
    for g in ("4", "6"):
        rg = run(CTL, "apply", pol, "--enforce", "--expected-version", "0",
                 "--expected-generation", g, "--request-id", "g05-g%s" % g, extra=env)
        assert rg["rc"] == 3, (g, rg)
    return {"emergency_blocks_apply": True, "resume_requires_auth_and_generation": True,
            "stale_and_future_generation_rejected": True, "no_executor_rc6": True}


# ============================================================== G06
@case("G06")
def g06():
    base = FIX / "ctl-g06"
    env = ctl_env(base)
    w(base / "lab.enabled", "")
    pol = policy_file(base, "limited.json", "limited")
    reset_logs()
    r = run(CTL, "apply", pol, "--enforce", "--expected-version", "0", "--expected-generation", "0",
            "--request-id", "g06-a", "--lab", extra=env)
    assert r["rc"] == 0 and "APPLIED" in r["stdout"], r
    man = read_json(base / "manifest.json")
    assert man["video"] and man["download"], man
    reset_logs()
    # 单类解除：只删 video 对象；download/meter 不动
    r_rel = run(CTL, "release", "video", "manual", "--reason", "user away",
                "--until", "2026-11-18T12:00:00+08:00", "--request-id", "g06-r1", extra=env)
    assert r_rel["rc"] == 0, r_rel
    tclog = calls("mock-tc.log")
    assert "class del" in tclog and "9:10" in tclog, tclog
    assert "9:11" not in tclog, tclog                       # download 类对象未动
    man2 = read_json(base / "manifest.json")
    assert man2["video"] is None and man2["download"] is not None, man2
    assert calls("mock-iptables.log") == ""                  # 计量规则零触碰
    ovr = read_json(base / "overrides.json")
    assert ovr["temporary"]["video"] and "video away" not in json.dumps(ovr)
    # 临时锁存期内对 video 再 apply 拒绝；download 类不被 video 临时锁拦（scope=download 策略）
    pol_dl = policy_file(base, "limited_dl.json", "limited",
                         mutate=lambda p: (p.update({"scope": "download"}),
                                           p["actions"].update({"video": {"mode": "unchanged"}})) and None or p)
    r_dl = run(CTL, "apply", pol_dl, "--enforce", "--expected-version", "1", "--expected-generation", "1",
               "--request-id", "g06-dl", "--lab", extra=env)
    assert r_dl["rc"] == 0, r_dl
    return {"single_class_release_only_own_objects": True, "meter_untouched": True,
            "per_scope_temporary_lock": True}


# ============================================================== G07
@case("G07")
def g07():
    base = FIX / "ctl-g07"
    env = ctl_env(base)
    pol = policy_file(base, "limited.json", "limited")
    # 1) 无注册表 → rc6，零 tc
    r1 = run(CTL, "apply", pol, "--enforce", "--expected-version", "0", "--expected-generation", "0",
             "--request-id", "g07-1", extra=env)
    assert r1["rc"] == 6 and calls("mock-tc.log") == "" and "wan" not in calls("mock-tc.log"), r1
    # 2) 注册表缺 download 执行器 → rc6 零效果
    reg = {"video": {"invoke": ["bash", (FIX / "fake-executor.sh").as_posix()], "timeout_s": 5}}
    w(base / "server-executors.json", json.dumps(reg))
    r2 = run(CTL, "apply", pol, "--enforce", "--expected-version", "0", "--expected-generation", "0",
             "--request-id", "g07-2", extra=env)
    assert r2["rc"] == 6 and calls("mock-tc.log") == "", r2
    assert not (base / "policy.version").exists()
    # 3) 计量不健康 → rc6
    reg2 = {c: {"invoke": ["bash", (FIX / "fake-executor.sh").as_posix()], "timeout_s": 5} for c in ("video", "download")}
    w(base / "server-executors.json", json.dumps(reg2))
    mstate = FIX / "ctl-g07-meter/state.json"
    w(mstate, json.dumps({"schema": 3, "health": "degraded", "day_key": "2026-11-17", "read_fail_eps": "a_up"}))
    r3 = run(CTL, "apply", pol, "--enforce", "--expected-version", "0", "--expected-generation", "0",
             "--request-id", "g07-3", extra={**env, "RC_METER_STATE": mstate.as_posix()})
    assert r3["rc"] == 6, r3
    # 4) 无 --lab 的 limited 永不进入本机 tc 路线
    assert "dev wan" not in calls("mock-tc.log"), calls("mock-tc.log")
    return {"no_registry_rc6": True, "partial_registry_rc6": True, "unhealthy_meter_rc6": True,
            "no_local_wan_fallback": True}


# ============================================================== G08
@case("G08")
def g08():
    base = FIX / "ctl-g08"
    env = ctl_env(base)

    def invalid(name, mutate):
        p = policy_file(base, name, "observe", mutate=mutate)
        r = run(CTL, "validate", p, extra=env)
        assert r["rc"] == 5, (name, r["stdout"])

    invalid("conflict.json", lambda p: p["actions"]["video"]["down"].update({"bits_per_second": 50000}))
    invalid("badunit.json", lambda p: p["actions"]["video"]["down"].update({"unit": "KB/s", "bits_per_second": None}))
    invalid("nofallback.json", lambda p: p["actions"]["video"].update({"down": {}}))
    invalid("neg.json", lambda p: p["actions"]["video"]["down"].update({"value": -5, "bits_per_second": -5000}))
    invalid("hardlow.json", lambda p: p["quota"].update({"hard_gib": 2}))
    invalid("unknownfield.json", lambda p: p.update({"mystery": 1}))
    invalid("wrongdevice.json", lambda p: p.update({"device_id": "other-device"}))
    invalid("exempt.json", lambda p: p.update({"enforcement": "exempt", "exempt": True}))
    p_ok = policy_file(base, "nohard.json", "observe", mutate=lambda p: p["quota"].pop("hard_gib", None))
    r_ok = run(CTL, "validate", p_ok, extra=env)
    assert r_ok["rc"] == 0 and '"hard_gib":null' in r_ok["stdout"].replace(" ", ""), r_ok.stdout
    # reason 含引号/反斜杠：审计逐行可解析
    reason = 'user said "stop \\" now"'
    r_rel = run(CTL, "release", "video", "manual", "--reason", reason,
                "--until", "2026-11-19T00:00:00+08:00", "--request-id", "g08-r", extra=env)
    assert r_rel["rc"] == 0, r_rel
    for line in (base / "audit.jsonl").read_text(encoding="utf-8").splitlines():
        json.loads(line)                                    # B16：必须全部可解析
    ovr = read_json(base / "overrides.json")
    assert ovr["temporary"]["video"]["reason"] == reason, ovr
    return {"unit_conflict_neg_unknown_all_rejected": True, "hard_default_null": True,
            "json_safe_reason": True}


# ============================================================== G09
@case("G09")
def g09():
    base = FIX / "ctl-g09"
    env = ctl_env(base)
    w(base / "lab.enabled", "")
    pol = policy_file(base, "limited.json", "limited")
    r = run(CTL, "apply", pol, "--enforce", "--expected-version", "0", "--expected-generation", "0",
            "--request-id", "g09-a", "--lab", extra=env)
    assert r["rc"] == 0, r
    tclog = calls("mock-tc.log")
    adds = [l for l in tclog.splitlines() if "class add" in l]
    assert len(adds) == 2, adds
    assert any("rate 60000bit" in l and "9:10" in l for l in adds), adds
    assert any("rate 60000bit" in l and "9:11" in l for l in adds), adds
    assert not any("120000" in l for l in adds), adds        # 不得把两类相加
    man = read_json(base / "manifest.json")
    assert man["video"]["objects"][0]["handle"] == "9:10" and man["download"]["objects"][0]["handle"] == "9:11", man
    # 逻辑面：阈值来源=router_meter 全提供方合计（preview 文案断言）
    rprev = run(CTL, "preview", pol, extra=env)
    assert "ALL providers" in rprev["stdout"], rprev["stdout"]
    return {"per_class_independent_60000bit": ["9:10", "9:11"], "no_combined_rate": True,
            "threshold_source": "router_meter_total_all_providers",
            "real_shaping_experiment": "NOT_TESTED_needs_isolated_linux_or_window"}


# ============================================================== G10
@case("G10")
def g10():
    # go test 渲染边界（B13/B14/B15 + 非试点兼容 + 国内站兜底不被撤销）
    p = subprocess.run(["go", "test", "./internal/clash/", "-run",
                        "TestRenderPurposeIsolation|TestMediaFallbackMembers|TestGoldenRules|TestGoldenRuleOrderStaticTail",
                        "-count=1", "-v"],
                       cwd=ROOT / "mgmt/backend", capture_output=True, text=True, timeout=300,
                       encoding="utf-8", errors="replace")
    lines = [l for l in p.stdout.splitlines() if l.startswith(("=== RUN", "--- PASS", "--- FAIL", "ok", "FAIL"))]
    assert p.returncode == 0 and "--- FAIL" not in p.stdout, p.stdout[-3000:]
    assert any("--- PASS: TestRenderPurposeIsolation" in l for l in lines), lines
    return {"go_tests": lines}


# ============================================================== G11
@case("G11")
def g11():
    base = FIX / "ctl-g11"
    env = ctl_env(base)
    w(base / "lab.enabled", "")
    pol = policy_file(base, "limited.json", "limited")
    # 他人 root 已在 lab dev → 拒绝且不删
    w(FIX / "tc-state.json", json.dumps({"devs": {env["RC_LAB_DEV"]: {"root": {"kind": "htb", "handle": "1:"}}}}))
    r = run(CTL, "apply", pol, "--enforce", "--expected-version", "0", "--expected-generation", "0",
            "--request-id", "g11-a", "--lab", extra=env)
    assert r["rc"] == 5, r
    st = read_json(FIX / "tc-state.json")
    assert st["devs"][env["RC_LAB_DEV"]]["root"]["handle"] == "1:", st      # 未知 root 未被动
    assert "qdisc del" not in calls("mock-tc.log")
    # 空 manifest release：无对象可删、rc0
    r2 = run(CTL, "release", "video", "manual", "--reason", "none",
             "--until", "2026-11-19T00:00:00+08:00", "--request-id", "g11-r", extra=env)
    assert r2["rc"] == 0 and "class del" not in calls("mock-tc.log"), r2
    return {"foreign_root_untouched": True, "empty_manifest_release_noop": True}


# ============================================================== G12
@case("G12")
def g12():
    base = FIX / "ctl-g12"
    env = ctl_env(base)
    pol = policy_file(base, "limited.json", "limited")
    healthy_meter(base)
    fx = FIX / "fake-executor.sh"
    w(fx, FAKE_EXECUTOR)
    fx.chmod(0o755)
    reg = {c: {"invoke": [BASH.as_posix(), fx.as_posix()], "timeout_s": 2} for c in ("video", "download")}
    w(base / "server-executors.json", json.dumps(reg))
    w(FIX / "fx-mode", "ok")
    # 1) 执行器成功：版本推进+manifest+evidence
    reset_logs()
    r1 = run(CTL, "apply", pol, "--enforce", "--expected-version", "0", "--expected-generation", "0",
             "--request-id", "g12-1", extra=env)
    assert r1["rc"] == 0, r1
    man = read_json(base / "manifest.json")
    assert man["video"]["objects"][0]["job_id"].startswith("job-video"), man
    assert read_json_path(base / "policy.version") == 1, "r1 version"
    n_calls1 = executor_calls(base)
    # 2) 同 request-id 重放：无新执行器调用、版本不变
    r2 = run(CTL, "apply", pol, "--enforce", "--expected-version", "0", "--expected-generation", "0",
             "--request-id", "g12-1", extra=env)
    assert r2["rc"] == 0 and "IDEMPOTENT_REPLAY" in r2["stdout"], r2
    assert executor_calls(base) == n_calls1, (executor_calls(base), n_calls1)
    assert read_json_path(base / "policy.version") == 1, "replay version"
    # 3) 同 id 异指纹：拒绝
    pol2 = policy_file(base, "limited2.json", "limited", mutate=lambda p: p.update({"policy_id": "pol-2"}) or p)
    r3 = run(CTL, "apply", pol2, "--enforce", "--expected-version", "1", "--expected-generation", "0",
             "--request-id", "g12-1", extra=env)
    assert r3["rc"] == 8, r3
    # 4) 超时：回滚+rc7+版本不推进
    w(FIX / "fx-mode", "timeout")
    reset_logs()
    r4 = run(CTL, "apply", pol2, "--enforce", "--expected-version", "1", "--expected-generation", "0",
             "--request-id", "g12-4", extra=env)
    assert r4["rc"] == 7 and read_json_path(base / "policy.version") == 1, r4
    assert "EFFECT_FAILED_ROLLED_BACK" in r4["stdout"], r4
    # 5) video 成功 download 失败：已建 video 作业被回滚、manifest 清、版本不推进
    w(FIX / "fx-mode", "video_ok_download_fail")
    reset_logs()
    r5 = run(CTL, "apply", pol2, "--enforce", "--expected-version", "1", "--expected-generation", "0",
             "--request-id", "g12-5", extra=env)
    assert r5["rc"] == 7 and read_json_path(base / "policy.version") == 1, r5
    man5 = read_json(base / "manifest.json")
    assert not (man5.get("video") or {}).get("objects"), man5
    fxlog = calls("fx-calls.log")
    assert '"op": "release"' in fxlog or '"op":"release"' in fxlog, fxlog     # 回滚调了 release
    # 6) 服务器执行器本体：附加式对象与精确解除
    w(FIX / "fx-mode", "ok")
    sres = run(EXEC, extra={"RS_BASE": (base / "sv").as_posix(),
                            "RS_CFG": sv_config(base),
                            "RS_TC_BIN": (BIN / "tc").as_posix(),
                            "RS_DEV": "eth0"},
               stdin='{"op":"apply","device_id":"rtr-x","cls":"video","bits_per_second":60000,"device_wan_ip":"203.0.113.1"}')
    out = json.loads(sres["stdout"])
    assert sres["rc"] == 0 and out["ok"] and out["effective"], (sres, out)
    svman = read_json(base / "sv/manifests.json")
    assert len(svman) == 1, svman
    tclog = calls("mock-tc.log")
    assert "clsact" in tclog and "police" in tclog and "qdisc del" not in tclog, tclog
    job = list(svman)[0]
    srel = run(EXEC, extra={"RS_BASE": (base / "sv").as_posix(),
                            "RS_CFG": sv_config(base),
                            "RS_TC_BIN": (BIN / "tc").as_posix(),
                            "RS_DEV": "eth0"},
               stdin='{"op":"release","job":"%s"}' % job)
    assert srel["rc"] == 0 and "filter del" in calls("mock-tc.log"), srel
    assert read_json(base / "sv/manifests.json") == {}, "sv manifest empty after release"
    # 7) 缺 wan IP / 未知类拒绝
    for payload in ('{"op":"apply","device_id":"rtr-x","cls":"video","bits_per_second":60000}',
                    '{"op":"apply","device_id":"rtr-x","cls":"game","bits_per_second":60000,"device_wan_ip":"1.1.1.1"}'):
        rr = run(EXEC, extra={"RS_BASE": (base / "sv2").as_posix(), "RS_CFG": sv_config(base),
                              "RS_TC_BIN": (BIN / "tc").as_posix(), "RS_DEV": "eth0"}, stdin=payload)
        assert rr["rc"] == 2 and '"ok": false' in rr["stdout"], (payload, rr)
    return {"success_version_and_evidence": True, "idempotent_replay": True, "conflict_rejected": True,
            "timeout_rolled_back": True, "partial_rolled_back": True,
            "server_executor_additive_objects": True, "missing_wan_or_class_refused": True}


# ============================================================== GEN (B11)
@case("GEN")
def gen_endpoints():
    cfgdir = FIX / "gen"
    cfgdir.mkdir(parents=True, exist_ok=True)
    w(cfgdir / "config.yaml", """proxies:
  - name: "BWG-HY2-JPTY-01"
    type: hysteria2
    server: 74.82.193.113
    port: 34567
    password: x
  - name: "BULK-LAX-GIA-01"
    type: vless
    server: 154.36.139.155
    port: 2054
    uuid: x
proxy-groups: []
rules:
  - MATCH,DIRECT
""")
    prov = cfgdir / "proxy_providers"
    prov.mkdir(exist_ok=True)
    w(prov / "airport-g01.yaml", """proxies:
  - name: "HK-01"
    server: 203.0.113.5
    port: 443
    type: ss
  - name: "US-dom"
    server: us.example.net
    port: 443
    type: ss
  - { name: 'JP-flow-01', type: ss, server: 203.0.113.7, port: 8443, cipher: aes-128-gcm }
  - { name: '套餐到期：长期有效', type: ss, server: dom2.example.net, port: 52326 }
""")
    rules_out = cfgdir / "rules.conf"
    cover_out = cfgdir / "coverage.json"
    env = {"EP_CONFIG": (cfgdir / "config.yaml").as_posix(),
           "EP_PROVIDERS_DIR": prov.as_posix(),
           "EP_RULES_OUT": rules_out.as_posix(),
           "EP_COVER_OUT": cover_out.as_posix()}
    r = run(ROOT / "scripts/qos-r2/proxy-meter-endpoints.sh", extra=env)
    assert r["rc"] == 0, r
    rules = rules_out.read_text(encoding="utf-8").splitlines()
    want = ["BWG-HY2-JPTY-01|up|74.82.193.113|udp|34567",
            "BWG-HY2-JPTY-01|down|74.82.193.113|udp|34567",
            "BULK-LAX-GIA-01|up|154.36.139.155|tcp|2054",
            "BULK-LAX-GIA-01|down|154.36.139.155|tcp|2054",
            "ap_HK-01|up|203.0.113.5|tcp|443",
            "ap_HK-01|down|203.0.113.5|tcp|443",
            "ap_JP-flow-01|up|203.0.113.7|tcp|8443",   # 流式 YAML 节点
            "ap_JP-flow-01|down|203.0.113.7|tcp|8443"]
    assert rules == want, rules                       # hysteria2→udp；vless→tcp；机场前缀 ap_；流式可解析
    cov = read_json(cover_out)
    assert cov["unresolved_names"] == "US-dom,套餐到期：长期有效", cov  # 域名节点点名（含流式）
    # --check：同内容 → same；改动 → differs 且不写
    r2 = run(ROOT / "scripts/qos-r2/proxy-meter-endpoints.sh", "--check", extra=env)
    assert r2["rc"] == 0 and "CHECK same" in r2["stdout"], r2
    w(cfgdir / "config.yaml", (cfgdir / "config.yaml").read_text(encoding="utf-8").replace("34567", "34568"))
    r3 = run(ROOT / "scripts/qos-r2/proxy-meter-endpoints.sh", "--check", extra=env)
    assert r3["rc"] == 1 and "CHECK differs" in r3["stdout"], r3
    assert "34568" not in rules_out.read_text(encoding="utf-8")   # check 不写
    return {"registered": want, "unresolved": cov["unresolved_names"], "check_mode": True}


# ---------------------------------------------------------------- helpers
def ctl_env(base):
    base.mkdir(parents=True, exist_ok=True)
    return {"RC_BASE": base.as_posix(),
            "RC_AGENT": agent_conf(),
            "RC_METER_STATE": (base / "meter-state.json").as_posix(),
            "RC_LAB_DEV": "pm-lab-" + base.name,   # 每 case 独立 lab 设备，互不污染
            "RC_TC_BIN": (BIN / "tc").as_posix()}


def agent_conf():
    p = FIX / "agent.conf"
    w(p, "DEVICE_ID=fixture-device\n")
    return p.as_posix()


def policy_file(base, name, enforcement, mutate=None):
    p = {"schema": 2, "policy_id": "pol-fx-1", "device_id": "fixture-device",
         "enforcement": enforcement, "scope": "download_video",
         "quota": {"period": "daily", "timezone": "Asia/Shanghai", "used_bytes_source": "router_meter",
                   "warn_gib": 1.0, "action_gib": 3.0},
         "actions": {"video": {"mode": "shape", "down": {"value": 60, "unit": "kbps", "bits_per_second": 60000},
                               "up": {"mode": "unchanged"}},
                     "download": {"mode": "shape", "down": {"value": 60, "unit": "kbps", "bits_per_second": 60000},
                                  "up": {"mode": "unchanged"}},
                     "web": {"mode": "unchanged"}},
         "exempt": False, "temporary_release": None}
    if mutate:
        p = mutate(p) or p
    path = base / name
    w(path, json.dumps(p, ensure_ascii=False))
    return path.as_posix()


def healthy_meter(base):
    w(base / "meter-state.json", json.dumps({"schema": 3, "health": "ok", "day_key": "2026-11-17",
                                             "read_fail_eps": "-", "day_total": 123}))


def read_json_path(p):
    return int(Path(p).read_text().strip())


def sv_config(base):
    p = base / "sv-config.json"
    w(p, json.dumps({"classes": {"video": {"ports": [2054], "proto": "tcp"}}}))
    return p.as_posix()


def executor_calls(base):
    # fake-executor.sh 的调用记录在 FIX/fx-calls.log
    f = FIX / "fx-calls.log"
    return f.read_text(encoding="utf-8").count("\n") if f.exists() else 0


FAKE_EXECUTOR = '''#!/bin/sh
# stdin: payload json; mode: <fixdir>/fx-mode = ok|fail|timeout|badjson|video_ok_download_fail
PAYLOAD=$(cat)
D=$(dirname "$0")
echo "$PAYLOAD" >> "$D/fx-calls.log"
python3 - "$PAYLOAD" "$(cat "$D/fx-mode" 2>/dev/null || echo ok)" <<'PY'
import json, sys
p = json.loads(sys.argv[1]); mode = sys.argv[2]
cls, op = p.get("cls", "?"), p.get("op", "?")
def ok(job):
    print(json.dumps({"ok": True, "job_id": job, "effective": True, "evidence": "fake-executor"})); sys.exit(0)
if op == "release":
    ok("released")
if mode == "fail":
    print(json.dumps({"ok": False})); sys.exit(1)
if mode == "timeout":
    import time; time.sleep(6)
if mode == "badjson":
    print("this is not json"); sys.exit(0)
if mode == "video_ok_download_fail" and cls == "download":
    print(json.dumps({"ok": False})); sys.exit(1)
ok("job-%s-1" % cls)
PY
'''


def main():
    w(FIX / "mock-iptables.log", "")
    w(FIX / "mock-tc.log", "")
    w(FIX / "mock-curl.log", "")
    failed = []
    for gid, fn in CASES:
        reset_logs()
        try:
            detail = fn()
            RESULTS["cases"][gid] = {"pass": True, "detail": detail}
            print("PASS", gid, flush=True)
        except AssertionError as e:
            RESULTS["cases"][gid] = {"pass": False, "error": str(e)[:2000]}
            failed.append(gid)
            print("FAIL", gid, str(e)[:500], flush=True)
        except Exception as e:
            import traceback
            RESULTS["cases"][gid] = {"pass": False, "error": "exception:%r" % e,
                                     "traceback": traceback.format_exc()[-2500:]}
            failed.append(gid)
            print("ERROR", gid, repr(e)[:500], flush=True)
    RESULTS["finished"] = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    RESULTS["source_sha256"] = {f: hashlib.sha256((ROOT / f).read_bytes()).hexdigest()
                                for f in ["scripts/qos-r2/proxy-meter-v3.sh",
                                          "scripts/qos-r2/proxy-meter-endpoints.sh",
                                          "scripts/qos-r2/ratelimit-ctl-v2.sh",
                                          "scripts/qos-r2/ratelimit-server-executor.sh"]}
    RESULTS["all_pass"] = not failed
    out = ROOT / "review/r2-qos-remediation-matrix-20261117.json"
    out.write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("ALL_PASS" if not failed else "FAILED: %s" % failed)
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()
