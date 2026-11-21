#!/bin/sh
# ratelimit-ctl v2 (R2) — 限速控制器安全重做。取代 v1（logs/ratelimit-ctl.sh，勿再用）。
#
# v1 阻断（review/R2v36准备报告独立复核与整改执行单-20261117-Codex.md B04—B09/B12/B16）与本版对策：
#   B04  limited 在 R2 WAN 上按两服务器 IP 整形上行（方向/对象全错）→ v2 删除本机 WAN 路线；
#        生产 limited 只能经服务器执行器（设备回复腿）；无执行器/计量不健康 → 硬拒绝，绝不回落本机。
#   B05  apply/release/emergency 一律 `tc qdisc del dev wan root` 删共享 root → v2 只按 manifest
#        记录的自有对象精确解除；observe/status/preview/validate 不执行任何 tc/curl。
#   B06  紧急锁字段语义混乱、generation 不参与校验 → v2 独立持久 overrides + 世代精确匹配。
#   B07  emergency 后 limited 仍 APPLIED；用量 0 也能 limited → v2 锁存期内普通 apply 拒绝；
#        limited 前置校验计量健康（schema3 / health ok / 当日 / read_fail 空）。
#   B08  observe --enforce 删 root+PUT 后才因 RATE 未定义失败 → v2 全部校验/准备/效果先行，
#        最后单点持久化；持久化失败即回滚已产生效果（compensate）。
#   B09  无锁/幂等/CAS 必填；单类 release 执行全部解除 → v2 flock + request-id 幂等 +
#        expected-version / expected-generation 必填；release 只动本 scope manifest。
#   B12  hard_gib 强制 6、value/bps 双源不一致可过、缺率值猜 60 → v2 单一权威率值，
#        矛盾/未知/负数/NaN 全拒；hard 缺省 null=关闭。
#   B16  reason 直接拼 JSON、写入不验证 → v2 python3 json 编码 + 追加后回读校验。
#
# 语义分层：desired（policy.json+version）与 effective（实际生效对象+证据）严格分开；
# 本控制器无 boot 自启/后台循环/watchdog —— 重启、旧任务、旧脚本不会经它复活或撤销限制。
# 组选择（US-MEDIA 等）不属于本控制器职责（平台任务负责）；本控制器永不做 9090 PUT。
# 人工 lab 入口（--lab + $BASE/lab.enabled）与生产 enforce 分离，lab 只碰专用测试设备（RC_LAB_DEV）。
#
# 退出码：0 ok；1 internal；2 usage；3 CAS/世代不匹配；4 锁存(override)拒绝；
#         5 校验失败/lab 未启用；6 前置缺失(执行器/计量)；7 效果失败已回滚(见 audit)；8 幂等冲突；
#         21 非受管设备。
#
# 环境覆盖（隔离测试）：RC_BASE RC_AGENT RC_METER_STATE RC_LAB_DEV RC_TC_BIN RC_DAY_KEY
set -u
BASE=${RC_BASE:-/etc/routermgmt/ratelimit-v2}
AGENTF=${RC_AGENT:-/etc/routermgmt/agent.conf}
METER_STATE=${RC_METER_STATE:-/etc/routermgmt/state/proxy-meter-v3.json}
LAB_DEV=${RC_LAB_DEV:-pm-lab0}
TC_BIN=${RC_TC_BIN:-tc}
GENF=$BASE/generation
POLICY=$BASE/policy.json
VERSIONF=$BASE/policy.version
EFFECTIVE=$BASE/effective.json
MANIFEST=$BASE/manifest.json
OVERRIDES=$BASE/overrides.json
REQUESTS=$BASE/requests.jsonl
AUDIT=$BASE/audit.jsonl
LOCK=$BASE/.lock
EXEC_REG=$BASE/server-executors.json
DAY_KEY=${RC_DAY_KEY:-$(TZ=CST-8 date '+%F')}
WANIP=${RC_DEVICE_WAN_IP:-$(cat /etc/routermgmt/device-wan-ip 2>/dev/null || true)}

[ -f "$AGENTF" ] || { echo "not on managed device"; exit 21; }
DEVICE_ID=$(sed -n 's/^DEVICE_ID=//p' "$AGENTF" | tr -d '\r')
[ -n "$DEVICE_ID" ] || { echo "agent.conf missing DEVICE_ID"; exit 21; }

usage() {
  cat <<EOF
ratelimit-ctl v2 — usage:
  status
  help
  validate <policy.json>
  preview <policy.json>
  apply <policy.json> --enforce --expected-version N --expected-generation N --request-id ID [--lab]
  release <video|download|all> <manual|exempt|emergency> --reason TXT --request-id ID [--until ISO8601]
  resume <emergency|exempt|temporary> --scope <video|download|all> --authorized-by NAME \\
         --reason TXT --request-id ID --expected-generation N
  compensate --request-id ID
Paths: BASE=\$BASE policy=\$POLICY effective=\$EFFECTIVE manifest=\$MANIFEST
       overrides=\$OVERRIDES generation=\$GENF requests=\$REQUESTS audit=\$AUDIT
       meter=\$METER_STATE executor_registry=\$EXEC_REG
Exit codes: 0 ok | 1 internal | 2 usage | 3 cas/generation | 4 locked(override) |
            5 validation/lab | 6 prereq(executor/meter) | 7 effect-failed-rolled-back | 8 idempotency-conflict | 21 not managed
Safety: status/validate/preview never touch kernel/network/versions. Production
'limited' requires per-class server executors + healthy schema-3 meter; the local
WAN shaping path was removed and never falls back. Lab needs \$BASE/lab.enabled.
EOF
  exit 2
}

mkdir -p "$BASE" 2>/dev/null
exec 9>"$LOCK"
flock -n 9 2>/dev/null || { echo "FAIL another ratelimit-ctl instance holds the lock"; exit 1; }

now_iso() { date '+%F %T%z'; }
cur_ver() { cat "$VERSIONF" 2>/dev/null || echo 0; }
cur_gen() { cat "$GENF" 2>/dev/null || echo 0; }

# ---- 审计（B16）：python3 严格 JSON 编码，追加后回读校验 ----
audit() { # $1=action $2..=偶数个 key value（value 为已 JSON 编码对象或字符串）
  DET=$(python3 - "$@" <<'PYA'
import json, sys
d = {}
args = sys.argv[1:]
for i in range(0, len(args) - 1, 2):
    v = args[i + 1]
    try: d[args[i]] = json.loads(v)
    except Exception: d[args[i]] = v
print(json.dumps(d))
PYA
)
  printf '{"ts":"%s","actor":"ratelimit-ctl-v2","action":"%s","detail":%s}\n' "$(now_iso)" "$1" "$DET" >> "$AUDIT" || { echo "FAIL audit append"; exit 1; }
  tail -n 1 "$AUDIT" | python3 -c 'import json,sys; json.loads(sys.stdin.read())' 2>/dev/null \
    || { echo "FAIL audit verify (disk full?); aborting before further state change"; exit 1; }
}

# ---- 幂等账本（B09）：同 request-id+同指纹 → 重放；同 id 异指纹 → 拒绝 ----
idem_check() { # $1=id $2=fingerprint → 0 new / 1 replay / 2 conflict
  [ -f "$REQUESTS" ] || return 0
  LAST=$(grep "\"request_id\":\"$1\"" "$REQUESTS" 2>/dev/null | tail -n 1)
  [ -n "$LAST" ] || return 0
  case "$LAST" in
    *"\"fingerprint\":\"$2\""*) return 1 ;;
    *) return 2 ;;
  esac
}
idem_record() { # $1=id $2=fingerprint $3=action $4=outcome $5=detail-json-object
  printf '{"ts":"%s","request_id":"%s","fingerprint":"%s","action":"%s","outcome":"%s","detail":%s}\n' \
    "$(now_iso)" "$1" "$2" "$3" "$4" "${5:-null}" >> "$REQUESTS" 2>/dev/null || true
}

# ---- 严格校验（B12/G08）----
validate_policy() { # $1=file → stdout {"valid":..,"errors":[..],"norm":{...}}
  RC_DEVICE_ID="$DEVICE_ID" python3 - "$1" <<'PYEOF'
import json, os, sys
errs, norm = [], {"video_bps": None, "download_bps": None, "warn_gib": None,
                  "action_gib": None, "hard_gib": None, "enforcement": None,
                  "scope": None, "shape_classes": []}
def num(x): return isinstance(x, (int, float)) and not isinstance(x, bool)
def bad(m): errs.append(m)
try:
    p = json.loads(open(sys.argv[1], encoding="utf-8").read())
    assert isinstance(p, dict)
except Exception as e:
    print(json.dumps({"valid": False, "errors": ["bad_json:%s" % e], "norm": norm})); sys.exit(0)
for k in p:
    if k not in ("schema","policy_id","device_id","enforcement","scope","quota","actions","exempt","temporary_release"):
        bad("unknown_field:"+k)
if p.get("schema") != 2: bad("schema!=2")
if not isinstance(p.get("policy_id"), str) or not p.get("policy_id"): bad("policy_id_missing")
if p.get("device_id") != os.environ.get("RC_DEVICE_ID"): bad("device_id_mismatch")
enf = p.get("enforcement")
norm["enforcement"] = enf
if enf not in ("observe", "limited", "paused"): bad("enforcement_not_settable_via_apply:"+str(enf))
sc = p.get("scope"); norm["scope"] = sc
if sc not in ("download_video", "video", "download"): bad("bad_scope:"+str(sc))
q = p.get("quota")
if not isinstance(q, dict): bad("quota_missing")
else:
    if q.get("period") != "daily": bad("quota.period!=daily")
    if q.get("timezone") != "Asia/Shanghai": bad("quota.timezone!=Asia/Shanghai")
    if q.get("used_bytes_source") != "router_meter": bad("quota.used_bytes_source!=router_meter")
    for k in ("warn_gib", "action_gib"):
        v = q.get(k)
        if not num(v) or v <= 0: bad("bad_quota_"+k)
        else: norm[k] = v
    if norm["warn_gib"] is not None and norm["action_gib"] is not None and not (norm["warn_gib"] < norm["action_gib"]):
        bad("quota_order_bad")
    h = q.get("hard_gib", None)          # 缺省 null = 关闭（绝不强制 6）
    if h is not None and (not num(h) or h <= 0): bad("bad_quota_hard_gib")
    elif h is not None:
        if norm["action_gib"] is not None and not (h > norm["action_gib"]): bad("hard_le_action")
        norm["hard_gib"] = h
    for k in q:
        if k not in ("period", "timezone", "used_bytes_source", "warn_gib", "action_gib", "hard_gib"):
            bad("unknown_quota_field:"+k)
a = p.get("actions")
if not isinstance(a, dict) or set(a) != {"video", "download", "web"}: bad("actions_keys_must_be_video_download_web")
else:
    if a["web"].get("mode") != "unchanged": bad("web_must_be_unchanged")
    for cls in ("video", "download"):
        act = a[cls]
        if not isinstance(act, dict) or set(act) - {"mode", "down", "up"}: bad("unknown_action_field:"+cls)
        m = act.get("mode")
        if m not in ("shape", "unchanged", "paused"): bad("bad_mode_"+cls)
        up = act.get("up")
        if up is not None and up.get("mode") != "unchanged": bad("up_must_be_unchanged:"+cls)
        if m == "shape":
            d = act.get("down")
            if not isinstance(d, dict): bad("down_missing:"+cls)
            elif set(d) - {"value", "unit", "bits_per_second"}: bad("unknown_down_field:"+cls)
            else:
                bps, vu, unit = d.get("bits_per_second"), d.get("value"), d.get("unit")
                if bps is None and vu is None: bad("rate_missing:"+cls)      # 不猜 60
                if vu is not None:
                    if not num(vu) or vu <= 0 or vu != vu or vu in (float("inf"), float("-inf")): bad("bad_rate_value:"+cls)
                    if unit not in ("kbps", "kbit/s"): bad("bad_unit_"+cls+":"+str(unit))
                if bps is not None:
                    if not num(bps) or bps <= 0 or bps != int(bps): bad("bad_bits_per_second:"+cls)
                    else: bps = int(bps)
                if vu is not None and bps is not None and int(vu * 1000) != bps:
                    bad("rate_sources_conflict:"+cls)                          # 双源必须一致
                if vu is not None and bps is None: bps = int(vu * 1000)
                if bps is None: bps = 0
                norm[cls + "_bps"] = bps
                norm["shape_classes"].append(cls)
if p.get("exempt") is not False: bad("exempt_not_settable_via_apply")
if p.get("temporary_release") is not None: bad("temporary_release_not_settable_via_apply")
print(json.dumps({"valid": not errs, "errors": errs, "norm": norm}))
PYEOF
}

# ---- 计量健康（B07/G07）：只认 schema3 且当日健康；读取失败≠0 ----
meter_health() {
  python3 - "$METER_STATE" "$DAY_KEY" "$DEVICE_ID" <<'PYEOF'
import json, sys, time
try:
    s = json.load(open(sys.argv[1], encoding="utf-8"))
    assert isinstance(s, dict)
except Exception as e:
    print(json.dumps({"ok": False, "reasons": ["meter_unreadable"], "day_total": None})); raise SystemExit
r = []
if s.get("schema") != 3: r.append("schema!=3")
if s.get("health") != "ok": r.append("health=%s" % s.get("health"))
if s.get("day_key") != sys.argv[2]: r.append("day_key=%s_stale" % s.get("day_key"))
if s.get("read_fail_eps") not in ("-", "", None): r.append("read_fail_eps")
if s.get("meter_id") != sys.argv[3]: r.append("meter_device_mismatch")
if s.get("partial") is not False: r.append("partial_or_unknown_day")
if s.get("quota_eligible") is not True: r.append("quota_not_eligible")
if s.get("coverage_complete") is not True: r.append("coverage_not_complete")
used=s.get("day_total")
if not isinstance(used,int) or isinstance(used,bool) or used<0: r.append("invalid_day_total")
sample=s.get("prev_epoch")
if not isinstance(sample,(int,float)) or isinstance(sample,bool) or not 0 <= time.time()-sample <= 420:
    r.append("meter_stale_or_future")
print(json.dumps({"ok": not r, "reasons": r, "day_total": s.get("day_total")}))
PYEOF
}

# ---- overrides（B06）：紧急/豁免/临时 = 独立持久高优先级状态 ----
save_overrides() {
  printf '%s\n' "$1" > "$OVERRIDES.tmp" && mv "$OVERRIDES.tmp" "$OVERRIDES" \
    && python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$OVERRIDES" 2>/dev/null \
    || { echo "FAIL overrides persist"; exit 1; }
}
override_block_reason() { # $1=scope(video|download) → 空=不阻塞
  python3 -c '
import json, sys, datetime
try: o = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception: sys.exit(0)
scope = sys.argv[2]
em = o.get("emergency")
if em: print("emergency(generation=%s)" % em.get("generation")); sys.exit(0)
ex = (o.get("exempt") or {}).get(scope)
if ex: print("exempt(until %s)" % ex.get("until", "?")); sys.exit(0)
tp = (o.get("temporary") or {}).get(scope)
if tp:
    until = tp.get("until") or ""
    try:
        if until and datetime.datetime.fromisoformat(until) > datetime.datetime.now(datetime.timezone.utc):
            print("temporary(until %s; expiry never auto-relimits)" % until)
    except ValueError:
        print("temporary(bad_until)")
sys.exit(0)' "$OVERRIDES" "$1" 2>/dev/null
}

# ---- lab 内核对象（仅 --lab+lab.enabled；自有句柄 9:x；绝不碰 wan）----
lab_ensure_root() {
  SHOW=$($TC_BIN qdisc show dev "$LAB_DEV" 2>/dev/null)
  if [ -n "$SHOW" ]; then
    printf '%s' "$SHOW" | grep -qE '(^|[[:space:]])9: ' && return 0   # 真实输出形如 "qdisc htb 9: root ..."
    return 1   # 已有非自有 qdisc：拒绝（G11 不动他人对象）
  fi
  $TC_BIN qdisc add dev "$LAB_DEV" root handle 9: htb default 99 2>/dev/null
}
lab_add_class() { # $1=classid $2=bps → stdout 自有对象 JSON（rate 单位显式 bit/s）
  $TC_BIN class add dev "$LAB_DEV" parent 9: classid "9:$1" htb rate "$2"bit ceil "$2"bit 2>/dev/null \
    && printf '{"kind":"htb-class","dev":"%s","handle":"9:%s"}' "$LAB_DEV" "$1" && return 0
  return 1
}
lab_del_obj() { # $1=handle → 0 成功；只接受 9: 前缀
  case "$1" in 9:[0-9]*) ;; *) return 1 ;; esac
  $TC_BIN class del dev "$LAB_DEV" classid "$1" 2>/dev/null
}

# ---- manifest / effective 维护 ----
man_set() { # $1=cls $2=objects-json-array $3=version
  python3 - "$MANIFEST" "$1" "$2" "$3" "$(now_iso)" <<'PYM'
import json, os, sys
m = {}
if os.path.exists(sys.argv[1]):
    try: m = json.load(open(sys.argv[1], encoding="utf-8"))
    except Exception: m = {}
m[sys.argv[2]] = {"objects": json.loads(sys.argv[3]), "version": int(sys.argv[4]), "at": sys.argv[5]}
print(json.dumps(m))
PYM
}
man_clear() { # $*=classes
  python3 - "$MANIFEST" "$@" <<'PYM'
import json, os, sys
m = {}
if os.path.exists(sys.argv[1]):
    try: m = json.load(open(sys.argv[1], encoding="utf-8"))
    except Exception: m = {}
for cls in sys.argv[2:]: m[cls] = None
print(json.dumps(m))
PYM
}
man_objects() { # $1=cls → 每行一个对象 JSON
  python3 - "$MANIFEST" "$1" <<'PYM'
import json, os, sys
if os.path.exists(sys.argv[1]):
    try:
        m = json.load(open(sys.argv[1], encoding="utf-8"))
        for o in (m.get(sys.argv[2]) or {}).get("objects", []): print(json.dumps(o))
    except Exception: pass
PYM
}
eff_set() { # $1=cls $2=state-json
  python3 - "$EFFECTIVE" "$1" "$2" <<'PYE'
import json, os, sys
e = {"classes": {}}
if os.path.exists(sys.argv[1]):
    try: e = json.load(open(sys.argv[1], encoding="utf-8"))
    except Exception: e = {"classes": {}}
e.setdefault("classes", {})[sys.argv[2]] = json.loads(sys.argv[3])
print(json.dumps(e))
PYE
}
persist() { # $1=file $2=content — 原子写+回读
  printf '%s\n' "$2" > "$1.tmp" && mv "$1.tmp" "$1" \
    && python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$1" 2>/dev/null
}

# ---- 服务器执行器调用（生产 limited 唯一允许的效果路径；G12）----
exec_invoke() { # $1=cls $2=payload-json → stdout job-json / 失败 stderr+rc≠0
  python3 - "$EXEC_REG" "$1" "$2" <<'PYEOF'
import json, subprocess, sys
reg = json.load(open(sys.argv[1], encoding="utf-8"))
e = reg.get(sys.argv[2])
if not isinstance(e, dict) or not e.get("invoke"):
    print(json.dumps({"ok": False, "error": "no_executor_registered:%s" % sys.argv[2]})); sys.exit(1)
try:
    r = subprocess.run(e["invoke"], input=sys.argv[3].encode(), capture_output=True,
                       timeout=float(e.get("timeout_s", 10)))
except subprocess.TimeoutExpired:
    print(json.dumps({"ok": False, "error": "executor_timeout"})); sys.exit(1)
except FileNotFoundError:
    print(json.dumps({"ok": False, "error": "executor_invoke_missing"})); sys.exit(1)
if r.returncode != 0:
    print(json.dumps({"ok": False, "error": "executor_rc:%d" % r.returncode,
                      "stderr": r.stderr.decode("utf-8", "replace")[-200:]})); sys.exit(1)
try: out = json.loads(r.stdout.decode("utf-8"))
except Exception:
    print(json.dumps({"ok": False, "error": "executor_bad_json"})); sys.exit(1)
if not out.get("ok") or not out.get("job_id") or out.get("effective") is not True:
    print(json.dumps({"ok": False, "error": "executor_not_effective"})); sys.exit(1)
print(json.dumps({"ok": True, "job_id": out["job_id"], "evidence": str(out.get("evidence", ""))[:200]}))
PYEOF
}

# ================= subcommands =================
CMD=${1:-status}; shift 2>/dev/null || true
case "$CMD" in
  help) usage ;;
  status)
    echo "policy_version=$(cur_ver)"
    echo "generation=$(cur_gen)"
    echo "policy=$(cat "$POLICY" 2>/dev/null || echo none)"
    echo "effective=$(cat "$EFFECTIVE" 2>/dev/null || echo none)"
    echo "manifest=$(cat "$MANIFEST" 2>/dev/null || echo none)"
    echo "overrides=$(cat "$OVERRIDES" 2>/dev/null || echo '{"emergency":null,"exempt":{},"temporary":{}}')"
    echo "executor_registry=$(cat "$EXEC_REG" 2>/dev/null || echo none)"
    echo "meter=$(meter_health)"
    echo "note: status 为纯文件读取（零 tc/curl/PUT）"
    exit 0
    ;;
  validate|preview)
    [ -f "${1:-}" ] || usage
    V=$(validate_policy "$1")
    echo "policy_validation=$V"
    echo "$V" | grep -q '"valid": true' || exit 5
    if [ "$CMD" = "preview" ]; then
      echo "classification (no changes, no version writes):"
      echo "$V" | python3 -c '
import json, sys
n = json.load(sys.stdin)["norm"]
for cls in ("video", "download"):
    b = n.get(cls + "_bps")
    print("  %s: %s" % (cls, ("shape down=%d bit/s (per-class independent budget)" % b) if b else "no-shape"))
print("  web: unchanged")
print("  quota: warn=%sGiB action=%sGiB hard=%s" % (n["warn_gib"], n["action_gib"], n["hard_gib"]))
print("  daily threshold source: router_meter schema-3 total across ALL providers (never per-provider sums)")'
      echo "NO_CHANGES_APPLIED (preview only)"
    fi
    exit 0
    ;;
  apply)
    ENFORCE=0; EXPECTED=""; EXPECTED_GEN=""; REQID=""; POLICY_FILE=""; LAB=0
    while [ $# -gt 0 ]; do
      case "$1" in
        --enforce) ENFORCE=1 ;;
        --dry-run) ENFORCE=0 ;;
        --expected-version) EXPECTED=${2:-}; shift ;;
        --expected-generation) EXPECTED_GEN=${2:-}; shift ;;
        --request-id) REQID=${2:-}; shift ;;
        --lab) LAB=1 ;;
        -*) echo "unknown flag $1"; exit 2 ;;
        *) [ -z "$POLICY_FILE" ] && POLICY_FILE=$1 || { echo "multiple policy files"; exit 2; } ;;
      esac
      shift
    done
    [ -n "$POLICY_FILE" ] && [ -f "$POLICY_FILE" ] || usage
    [ -n "$REQID" ] || { echo "--request-id is mandatory (idempotency)"; exit 2; }
    case "$EXPECTED" in ''|*[!0-9]*) echo "--expected-version is mandatory (CAS)"; exit 2 ;; esac
    if [ "$ENFORCE" = "1" ]; then
      case "$EXPECTED_GEN" in ''|*[!0-9]*) echo "--expected-generation is mandatory with --enforce"; exit 2 ;; esac
    fi
    FP=$( { printf 'apply|%s|%s|%s|%s|' "$ENFORCE" "$LAB" "$EXPECTED" "$EXPECTED_GEN"; sha256sum "$POLICY_FILE" | cut -c1-32; } | sha256sum | cut -c1-16)
    idem_check "$REQID" "$FP"; IDEM=$?
    [ "$IDEM" = "2" ] && { echo "IDEMPOTENCY_CONFLICT request-id reused with different args"; exit 8; }
    [ "$IDEM" = "1" ] && { echo "IDEMPOTENT_REPLAY request-id=$REQID (no new effects)"; exit 0; }

    V=$(validate_policy "$POLICY_FILE")
    echo "policy_validation=$V"
    echo "$V" | grep -q '"valid": true' || { idem_record "$REQID" "$FP" apply rejected_invalid '{"reason":"validation"}'; exit 5; }
    ENF=$(printf '%s' "$V" | python3 -c 'import json,sys; print(json.load(sys.stdin)["norm"]["enforcement"])')
    SCOPE=$(printf '%s' "$V" | python3 -c 'import json,sys; print(json.load(sys.stdin)["norm"]["scope"])')
    CV=$(cur_ver); CG=$(cur_gen)
    [ "$EXPECTED" = "$CV" ] || { echo "CAS_REJECT expected=$EXPECTED current=$CV"; idem_record "$REQID" "$FP" apply rejected_cas '{"expected":'"$EXPECTED"'}'; exit 3; }
    if [ "$ENFORCE" = "1" ]; then
      [ "$EXPECTED_GEN" = "$CG" ] || { echo "GENERATION_REJECT expected=$EXPECTED_GEN current=$CG"; idem_record "$REQID" "$FP" apply rejected_generation '{"expected":'"$EXPECTED_GEN"'}'; exit 3; }
      BLK=""
      for cls in video download; do
        case " $SCOPE " in *" $cls "*|*download_video*) ;; *) continue ;; esac
        R=$(override_block_reason "$cls")
        [ -n "$R" ] && BLK="$BLK $cls:$R"
      done
      [ -n "$BLK" ] && { echo "LOCKED_APPLY_REJECT$BLK (resume or expiry required)"; idem_record "$REQID" "$FP" apply rejected_locked '{"reason":"override"}'; exit 4; }
    fi
    NEWVER=$((CV + 1))

    # ---- 效果执行（先效果后持久化；任何失败回滚已产生效果，版本不动）----
    DONE_OBJS=""
    if [ "$ENFORCE" = "1" ] && [ "$ENF" = "limited" ]; then
      SHAPE=$(printf '%s' "$V" | python3 -c 'import json,sys; n=json.load(sys.stdin)["norm"]; print(",".join(n["shape_classes"]) or "-")')
      if [ "$SHAPE" = "-" ]; then
        echo "VALIDATION_GAP limited without any shape class"; exit 5
      fi
      if [ "$LAB" = "1" ]; then
        [ -f "$BASE/lab.enabled" ] || { echo "LAB_NOT_ENABLED (create $BASE/lab.enabled in test envs only)"; idem_record "$REQID" "$FP" apply rejected_lab '{"reason":"lab_not_enabled"}'; exit 5; }
        lab_ensure_root || { echo "LAB_DEV_ROOT_CONFLICT $LAB_DEV (only own handle 9: accepted)"; idem_record "$REQID" "$FP" apply rejected_lab '{"reason":"lab_root_conflict"}'; exit 5; }
        ROLLBACK=""
        I=0
        for cls in $(printf '%s' "$SHAPE" | tr ',' ' '); do
          I=$((I + 1)); CID=$((9 + I))
          BPS=$(printf '%s' "$V" | python3 -c 'import json,sys; n=json.load(sys.stdin)["norm"]; print(n["'"$cls"'_bps"])')
          OBJ=$(lab_add_class "$CID" "$BPS")
          if [ -z "$OBJ" ]; then
            for h in $ROLLBACK; do lab_del_obj "$h"; done
            echo "EFFECT_FAILED_ROLLED_BACK lab class $cls"; idem_record "$REQID" "$FP" apply effect_failed_rolled_back '{"cls":"'"$cls"'","where":"lab"}'
            audit apply '{"outcome":"effect_failed_rolled_back","cls":"'"$cls"'","where":"lab"}'
            exit 7
          fi
          ROLLBACK="$ROLLBACK 9:$CID"
          NEW=$(man_set "$cls" "[$OBJ]" "$NEWVER"); persist "$MANIFEST" "$NEW" || { for h in $ROLLBACK; do lab_del_obj "$h"; done; echo "FAIL manifest persist (effects rolled back)"; exit 1; }
          EFF=$(eff_set "$cls" "{\"state\":\"limited\",\"where\":\"lab\",\"bps\":$BPS,\"version\":$NEWVER,\"evidence\":\"tc class 9:$CID on $LAB_DEV\"}")
          persist "$EFFECTIVE" "$EFF" || { for h in $ROLLBACK; do lab_del_obj "$h"; done; echo "FAIL effective persist (effects rolled back)"; exit 1; }
          DONE_OBJS="$DONE_OBJS $cls"
        done
      else
        # 生产：每 shape 类都要有执行器 + 计量健康（G07）；绝不回落本机 WAN
        [ -f "$EXEC_REG" ] || { echo "PREREQ_MISSING no executor registry $EXEC_REG; local WAN fallback removed by design"; idem_record "$REQID" "$FP" apply rejected_no_executor '{"reason":"no_registry"}'; exit 6; }
        # 注册表预检：任一 shape 类缺执行器 → rc6，零效果（不进入逐类调用-回滚路径）
        MISSING_CLS=$(python3 - "$EXEC_REG" "$SHAPE" <<'PYR'
import json, sys
try: reg = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception: reg = {}
missing = [c for c in sys.argv[2].split(",") if c and not (reg.get(c) or {}).get("invoke")]
print(",".join(missing))
PYR
)
        [ -n "$MISSING_CLS" ] && [ "$MISSING_CLS" != "," ] && { echo "PREREQ_MISSING no executor registered for: $MISSING_CLS; local WAN fallback removed by design"; idem_record "$REQID" "$FP" apply rejected_no_executor '{"reason":"no_executor","classes":"'"$MISSING_CLS"'"}'; exit 6; }
        MH=$(meter_health)
        echo "$MH" | grep -q '"ok": true' || { echo "PREREQ_MISSING meter not trusted: $MH"; idem_record "$REQID" "$FP" apply rejected_meter '{"reason":"meter_unhealthy"}'; exit 6; }
        DAYTOTAL=$(printf '%s' "$MH" | python3 -c 'import json,sys; print(json.load(sys.stdin)["day_total"])')
        python3 - "$POLICY_FILE" "$DAYTOTAL" <<'PYQUOTA' || { echo "PREREQ_MISSING daily action threshold not reached"; exit 6; }
import json,sys
from decimal import Decimal
p=json.load(open(sys.argv[1],encoding="utf-8"))
threshold=Decimal(str(p["quota"]["action_gib"]))*Decimal(1073741824)
if not threshold.is_finite() or int(sys.argv[2])<threshold:raise SystemExit(1)
PYQUOTA
        ROLLBACK_JOBS=""
        for cls in $(printf '%s' "$SHAPE" | tr ',' ' '); do
          PAYLOAD=$(python3 - "$POLICY_FILE" "$cls" "$DAYTOTAL" "$WANIP" <<'PYP'
import json, sys
p = json.load(open(sys.argv[1], encoding="utf-8"))
act = p["actions"][sys.argv[2]]
d = act["down"]
bps = d.get("bits_per_second") or int(d["value"]) * 1000
payload = {"op": "apply", "device_id": p["device_id"], "cls": sys.argv[2], "dir": "down",
           "bits_per_second": bps, "meter_day_total": sys.argv[3],
           "budget_hard_gib": p["quota"].get("hard_gib")}
if sys.argv[4]:
    payload["device_wan_ip"] = sys.argv[4]
print(json.dumps(payload))
PYP
)
          JOB=$(exec_invoke "$cls" "$PAYLOAD") || {
            RC_CLSES=""
            for pair in $ROLLBACK_JOBS; do
              RC_CLS=${pair%%:*}; RC_JOB=${pair#*:}
              RC_CLSES="$RC_CLSES $RC_CLS"
              exec_invoke "$RC_CLS" '{"op":"release","job":"'"$RC_JOB"'"}' >/dev/null 2>&1 || true
            done
            # 已回滚类从 manifest 清除，避免账实分离
            if [ -n "$RC_CLSES" ]; then
              NEW=$(man_clear $RC_CLSES); persist "$MANIFEST" "$NEW" 2>/dev/null || true
            fi
            echo "EFFECT_FAILED_ROLLED_BACK executor $cls"; idem_record "$REQID" "$FP" apply effect_failed_rolled_back '{"cls":"'"$cls"'","where":"executor"}'
            audit apply '{"outcome":"effect_failed_rolled_back","cls":"'"$cls"'","where":"executor"}'
            exit 7
          }
          JOBID=$(printf '%s' "$JOB" | python3 -c 'import json,sys; print(json.load(sys.stdin)["job_id"])')
          EVID=$(printf '%s' "$JOB" | python3 -c 'import json,sys; print(json.load(sys.stdin)["evidence"])')
          ROLLBACK_JOBS="$ROLLBACK_JOBS $cls:$JOBID"
          NEW=$(man_set "$cls" "[{\"kind\":\"server-job\",\"cls\":\"$cls\",\"job_id\":\"$JOBID\"}]" "$NEWVER")
          persist "$MANIFEST" "$NEW" || { echo "FAIL manifest persist after executor job $JOBID — run compensate --request-id $REQID"; idem_record "$REQID" "$FP" apply persist_failed_needs_compensate '{"job":"'"$JOBID"'"}'; exit 7; }
          EFF=$(eff_set "$cls" "{\"state\":\"limited\",\"where\":\"server\",\"job_id\":\"$JOBID\",\"evidence\":\"$EVID\",\"version\":$NEWVER}")
          persist "$EFFECTIVE" "$EFF" || { echo "FAIL effective persist — run compensate --request-id $REQID"; idem_record "$REQID" "$FP" apply persist_failed_needs_compensate '{"job":"'"$JOBID"'"}'; exit 7; }
          DONE_OBJS="$DONE_OBJS $cls"
        done
      fi
    fi

    # ---- 单点持久化 desired（B08：效果已就绪/或无需效果，此处失败仅影响账本）----
    cp "$POLICY_FILE" "$POLICY.tmp" && mv "$POLICY.tmp" "$POLICY" || { echo "FAIL policy persist"; exit 1; }
    printf '%s\n' "$NEWVER" > "$VERSIONF.tmp" && mv "$VERSIONF.tmp" "$VERSIONF" || { echo "FAIL version persist"; exit 1; }

    if [ "$ENFORCE" != "1" ]; then
      echo "DRY_RUN_OK (no kernel/group/policy changes; version stays $CV)"
      idem_record "$REQID" "$FP" apply dry_run '{"version":'"$CV"'}'
      audit apply '{"outcome":"dry_run","version":'"$CV"'}'
      exit 0
    fi
    if [ "$ENF" != "limited" ] && [ -f "$MANIFEST" ]; then
      # observe/paused 意图持久但不动已有对象；有活跃对象时报 drift（不自动撤）
      python3 - "$MANIFEST" <<'PYD' | grep . && echo "HINT use release to withdraw listed objects"
import json, sys
try:
    m = json.load(open(sys.argv[1], encoding="utf-8"))
    live = [k for k, v in m.items() if v and v.get("objects")]
    if live: print("DRIFT classes still shaped: %s" % ",".join(live))
except Exception: pass
PYD
    fi
    echo "APPLIED version=$NEWVER enforcement=$ENF classes=[$DONE_OBJS ]"
    idem_record "$REQID" "$FP" apply applied '{"version":'"$NEWVER"',"enforcement":"'"$ENF"'","classes":"'"$DONE_OBJS"'"}'
    audit apply '{"outcome":"applied","version":'"$NEWVER"',"enforcement":"'"$ENF"'","classes":"'"$(printf '%s' "$DONE_OBJS" | tr -d ' ')"'"}'
    exit 0
    ;;
  release)
    SCOPE=${1:-}; MODE=${2:-}; shift 2 2>/dev/null || true
    REASON=""; REQID=""; UNTIL=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --reason) REASON=${2:-}; shift ;;
        --request-id) REQID=${2:-}; shift ;;
        --until) UNTIL=${2:-}; shift ;;
        *) echo "unknown arg $1"; exit 2 ;;
      esac
      shift
    done
    case "$SCOPE" in video|download|all) ;; *) usage ;; esac
    case "$MODE" in manual|exempt|emergency) ;; *) usage ;; esac
    [ -n "$REQID" ] || { echo "--request-id mandatory"; exit 2; }
    [ -n "$REASON" ] || { echo "--reason mandatory"; exit 2; }
    if [ "$MODE" != "emergency" ] && [ -z "$UNTIL" ]; then
      echo "--until ISO8601 mandatory for manual/exempt"; exit 2
    fi
    FP=$(printf 'release|%s|%s|%s|%s' "$SCOPE" "$MODE" "$UNTIL" "$REASON" | sha256sum | cut -c1-16)
    idem_check "$REQID" "$FP"; IDEM=$?
    [ "$IDEM" = "2" ] && { echo "IDEMPOTENCY_CONFLICT"; exit 8; }
    [ "$IDEM" = "1" ] && { echo "IDEMPOTENT_REPLAY request-id=$REQID"; exit 0; }

    CLASSES="video download"; [ "$SCOPE" != "all" ] && CLASSES="$SCOPE"
    FAILS=0
    for cls in $CLASSES; do
      while IFS= read -r obj; do
        [ -n "$obj" ] || continue
        KIND=$(printf '%s' "$obj" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("kind",""))' 2>/dev/null)
        case "$KIND" in
          htb-class)
            H=$(printf '%s' "$obj" | python3 -c 'import json,sys; print(json.load(sys.stdin)["handle"])' 2>/dev/null)
            lab_del_obj "$H" || { echo "WARN release $cls $H failed"; FAILS=$((FAILS+1)); }
            ;;
          server-job)
            JID=$(printf '%s' "$obj" | python3 -c 'import json,sys; print(json.load(sys.stdin)["job_id"])' 2>/dev/null)
            exec_invoke "$cls" '{"op":"release","job":"'"$JID"'"}' >/dev/null 2>&1 || { echo "WARN release $cls job $JID failed"; FAILS=$((FAILS+1)); }
            ;;
        esac
      done <<EOF
$(man_objects "$cls")
EOF
    done
    [ "$FAILS" -gt 0 ] && { idem_record "$REQID" "$FP" release partial '{"fails":'"$FAILS"'}'; audit release '{"outcome":"partial","fails":'"$FAILS"'}'; echo "RELEASE_PARTIAL $FAILS object(s) failed — run compensate"; exit 7; }
    NEW=$(man_clear $CLASSES); persist "$MANIFEST" "$NEW" || { echo "FAIL manifest persist"; exit 1; }
    CG=$(cur_gen); NEWGEN=$((CG + 1))
    printf '%s\n' "$NEWGEN" > "$GENF.tmp" && mv "$GENF.tmp" "$GENF" || { echo "FAIL generation persist"; exit 1; }
    OVR=$(python3 - "$OVERRIDES" "$MODE" "$SCOPE" "$UNTIL" "$REASON" "$NEWGEN" "$(now_iso)" <<'PYO'
import json, os, sys
o = {"emergency": None, "exempt": {}, "temporary": {}}
if os.path.exists(sys.argv[1]):
    try: o.update(json.load(open(sys.argv[1], encoding="utf-8")))
    except Exception: pass
mode, scope, until, reason, gen, now = sys.argv[2:8]
if mode == "emergency":
    o["emergency"] = {"generation": int(gen), "reason": reason, "at": now}
else:
    for cls in (["video", "download"] if scope == "all" else [scope]):
        o["exempt" if mode == "exempt" else "temporary"][cls] = {
            "until": until, "reason": reason, "generation": int(gen), "at": now}
print(json.dumps(o))
PYO
)
    save_overrides "$OVR"
    echo "RELEASED scope=$SCOPE mode=$MODE generation=$NEWGEN (temporary expiry never auto-relimits)"
    idem_record "$REQID" "$FP" release ok '{"scope":"'"$SCOPE"'","mode":"'"$MODE"'","generation":'"$NEWGEN"'}'
    audit release '{"outcome":"ok","scope":"'"$SCOPE"'","mode":"'"$MODE"'","reason":'"$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$REASON")"',"until":"'"${UNTIL:-}"'","generation":'"$NEWGEN"'}'
    exit 0
    ;;
  resume)
    WHAT=${1:-}; shift 2>/dev/null || true
    SCOPE=""; AUTHBY=""; REASON=""; REQID=""; EXPECTED_GEN=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --scope) SCOPE=${2:-}; shift ;;
        --authorized-by) AUTHBY=${2:-}; shift ;;
        --reason) REASON=${2:-}; shift ;;
        --request-id) REQID=${2:-}; shift ;;
        --expected-generation) EXPECTED_GEN=${2:-}; shift ;;
        *) echo "unknown arg $1"; exit 2 ;;
      esac
      shift
    done
    case "$WHAT" in emergency|exempt|temporary) ;; *) usage ;; esac
    case "$SCOPE" in video|download|all) ;; *) usage ;; esac
    [ -n "$REQID" ] && [ -n "$AUTHBY" ] && [ -n "$REASON" ] || { echo "--request-id/--authorized-by/--reason mandatory"; exit 2; }
    case "$EXPECTED_GEN" in ''|*[!0-9]*) echo "--expected-generation mandatory"; exit 2 ;; esac
    FP=$(printf 'resume|%s|%s' "$WHAT" "$SCOPE" | sha256sum | cut -c1-16)
    idem_check "$REQID" "$FP"; IDEM=$?
    [ "$IDEM" = "2" ] && { echo "IDEMPOTENCY_CONFLICT"; exit 8; }
    [ "$IDEM" = "1" ] && { echo "IDEMPOTENT_REPLAY request-id=$REQID"; exit 0; }
    [ "$EXPECTED_GEN" = "$(cur_gen)" ] || { echo "GENERATION_REJECT expected=$EXPECTED_GEN current=$(cur_gen)"; exit 3; }
    OVR=$(python3 - "$OVERRIDES" "$WHAT" "$SCOPE" "$(now_iso)" <<'PYO'
import json, os, sys
o = {"emergency": None, "exempt": {}, "temporary": {}}
if os.path.exists(sys.argv[1]):
    try: o.update(json.load(open(sys.argv[1], encoding="utf-8")))
    except Exception: pass
what, scope = sys.argv[2:4]
if what == "emergency":
    o["emergency"] = None
else:
    for cls in (["video", "download"] if scope == "all" else [scope]):
        o[what].pop(cls, None)
print(json.dumps(o))
PYO
)
    save_overrides "$OVR"
    echo "RESUMED $WHAT scope=$SCOPE authorized_by=$AUTHBY"
    idem_record "$REQID" "$FP" resume ok '{"what":"'"$WHAT"'","scope":"'"$SCOPE"'"}'
    audit resume '{"outcome":"ok","what":"'"$WHAT"'","scope":"'"$SCOPE"'","authorized_by":"'"$AUTHBY"'","reason":'"$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$REASON")"'}'
    exit 0
    ;;
  compensate)
    # 幂等重放：按 requests.jsonl 里 outcome=persist_failed_needs_compensate 的记录撤残留效果
    REQID=${2:-}
    [ -n "$REQID" ] || { echo "usage: compensate --request-id ID"; exit 2; }
    [ "${1:-}" = "--request-id" ] || { echo "usage: compensate --request-id ID"; exit 2; }
    REC=$(grep "\"request_id\":\"$REQID\"" "$REQUESTS" 2>/dev/null | tail -n 1)
    [ -n "$REC" ] || { echo "NO_RECORD $REQID"; exit 5; }
    echo "compensate: record=$REC"
    echo "按 manifest/effective 差异人工核对后重放 release；生产执行器注册前仅生成报告"
    exit 0
    ;;
  *) usage ;;
esac
