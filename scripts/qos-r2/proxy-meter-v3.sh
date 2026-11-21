#!/bin/sh
# proxy-meter v3 (R2) — per-endpoint proxy byte accounting, schema 3.
#
# v2 教训（review/R2v36准备报告独立复核与整改执行单-20261117-Codex.md B01/B02/B03/B10/B11）：
#   - PREV 路径与内容混用、两样本拼单行喂给 NR 分支 awk → 日账恒 0。
#   - 读失败被替换成 0/旧值仍报 OK、状态损坏仍推进。
# v3 设计：
#   1. 单一原子权威状态文件（含上一采样计数器 + 当日账），tmp+mv 一次提交；
#      日账视图文件为派生物，可随时由权威状态重建（崩溃不丢不算两遍）。
#   2. 上一状态与当前采样作为两个独立文件参数交给同一个 awk（FILENAME 区分），
#      定点键提取 + 受限键名（[A-Za-z0-9_-]+），解析失败按读失败处理，绝不填 0。
#   3. 读失败端点：在权威状态中保留上一好值（恢复后差额自动累计）、贡献 0、
#      health=degraded、rc=3。0 是合法计数（零流量健康 0）。
#   4. 计数下降（同 boot 链重建/fw3 reload）：该端点重定基线（贡献 0、不伪补、不为负），
#      日账保留，记 rebuild 事件；boot_id 变化：全体重定基线，日账保留，continuity 更换。
#   5. 新端点首见重定基线（防止规则晚插把历史累计灌进当天）；端点消失记 drop_ep
#      覆盖缺口（health=degraded），不假装总账可信。
#   6. 跨午夜：未知区间独立记crossday_unassigned，两日仅报下界，无证据不发资格。
#   7. 覆盖显式化：激活组当前节点未注册（域名型机场节点）时点名 → health=degraded。
#   8. 状态损坏：保留 .corrupt-<ts> 取证副本后重建基线，health=degraded。
#   9. 纯计数：用完整 -C 规则判重；仅在没有旧引用时恢复 INPUT/OUTPUT 计数入口。
#      不修改转发动作、路由、限速或已有共享规则。修复入口时记录缺口，不假装账本完整。
# 2026-11-18 Codex：上下行独立键，sticky partial，quota_eligible=false；历史漏记不补造。
# 环境覆盖（隔离测试）：PM_STATE PM_DAY PM_LOG PM_RULES PM_COVER PM_LOCK PM_CHAIN_OUT
#   PM_CHAIN_IN PM_TZ PM_API PM_CHECK_ACTIVE PM_NOW_EPOCH PM_NOW_ISO PM_BOOT_ID（后三/四仅测试注入）
set -u

STATE=${PM_STATE:-/etc/routermgmt/state/proxy-meter-v3.json}
DAYF=${PM_DAY:-/etc/routermgmt/state/proxy-meter-daily-v3.json}
LOG=${PM_LOG:-/etc/routermgmt/logs/proxy-meter-v3.log}
RULES=${PM_RULES:-/etc/routermgmt/proxy-meter-rules-v3.conf}
COVER=${PM_COVER:-/etc/routermgmt/proxy-meter-coverage-v3.json}
LOCKF=${PM_LOCK:-/etc/routermgmt/state/proxy-meter-v3.lock}
CHAIN_OUT=${PM_CHAIN_OUT:-PROXY_METER_OUT}
CHAIN_IN=${PM_CHAIN_IN:-PROXY_METER_IN}
METER_ID=$(sed -n 's/^DEVICE_ID=//p' "${PM_AGENT:-/etc/routermgmt/agent.conf}" 2>/dev/null | tr -d '\r')
[ -n "$METER_ID" ] || METER_ID="unknown-device"
# 日账日键固定 Asia/Shanghai：中国无夏令时，POSIX TZ 形式 CST-8 在 busybox/musl/MSYS
# 下都比 Olson 名（需 tzdata）可靠；PM_TZ 可覆盖（仅测试）。
TZLOCAL=${PM_TZ:-CST-8}
LOG_MAX=5242880

mkdir -p "$(dirname "$STATE")" "$(dirname "$LOG")" 2>/dev/null

log() { echo "$(date '+%F %T') $*" >> "$LOG"; }
fail_exit() { log "FAIL $*"; echo "FAIL $*"; exit 1; }

exec 9>"$LOCKF"
if [ "${1:-}" = "--settle" ]; then
  attempts=0
  until flock -n 9 2>/dev/null; do
    attempts=$((attempts+1))
    [ "$attempts" -lt 5 ] || fail_exit "shutdown settlement lock unavailable"
    sleep 1
  done
else
  flock -n 9 2>/dev/null || { log "SKIP lock held"; [ "${1:-}" != "--install-only" ]; exit $?; }
fi

command -v iptables >/dev/null 2>&1 || fail_exit "iptables missing"
[ -f "$RULES" ] || fail_exit "rules file missing: $RULES"

# ---- 时间（TZ 只用于日账日键；epoch 用于乱序/回拨判定） ----
NOW_EPOCH=${PM_NOW_EPOCH:-$(date +%s)}
NOW_ISO=${PM_NOW_ISO:-$(date '+%F %T%z' 2>/dev/null || date '+%F %T')}
DAY_KEY=$(TZ="$TZLOCAL" date -d "@$NOW_EPOCH" '+%F' 2>/dev/null || TZ="$TZLOCAL" date '+%F')

# ---- boot / rules 代次 ----
BOOT_ID=${PM_BOOT_ID:-$(cat /proc/sys/kernel/random/boot_id 2>/dev/null || echo unknown)}
[ -n "$BOOT_ID" ] || BOOT_ID=unknown
RULES_SHA=$(sha256sum "$RULES" 2>/dev/null | cut -c1-16)
[ -n "$RULES_SHA" ] || RULES_SHA=unknown

# ---- 自链内幂等补缺规则：按 comment 标签判存在，绝不重复插入；不动 built-in/jump ----
MISSING_RULES=0
ADDED_RULES=0
ensure_rules() {
  iptables -n -L "$CHAIN_OUT" >/dev/null 2>&1 || iptables -N "$CHAIN_OUT" 2>/dev/null
  iptables -n -L "$CHAIN_IN"  >/dev/null 2>&1 || iptables -N "$CHAIN_IN"  2>/dev/null
  while IFS='|' read -r ep dir ip proto port; do
    case "$ep" in ''|'#'*) continue ;; esac
    [ "$dir" = "up" ] && CH=$CHAIN_OUT || CH=$CHAIN_IN
    # 与现网既有计数规则同款：无 target 纯计数（避免 RETURN 截断后续规则计数）
    if [ "$dir" = "up" ]; then
      iptables -C "$CH" -p "$proto" -d "$ip" --dport "$port" -m comment --comment "$ep" 2>/dev/null && continue
      if iptables -A "$CH" -p "$proto" -d "$ip" --dport "$port" -m comment --comment "$ep" 2>/dev/null; then ADDED_RULES=$((ADDED_RULES+1)); else MISSING_RULES=$((MISSING_RULES+1)); fi
    else
      iptables -C "$CH" -p "$proto" -s "$ip" --sport "$port" -m comment --comment "$ep" 2>/dev/null && continue
      if iptables -A "$CH" -p "$proto" -s "$ip" --sport "$port" -m comment --comment "$ep" 2>/dev/null; then ADDED_RULES=$((ADDED_RULES+1)); else MISSING_RULES=$((MISSING_RULES+1)); fi
    fi
  done < "$RULES"
}
[ "${1:-}" = "--settle" ] || ensure_rules

# Only repair an absent direct counter jump. Existing references are left intact;
# duplicate/conditional references fail health instead of silently double counting.
JUMP_OK=0
JUMP_REPAIRED=0
for pair in "OUTPUT:$CHAIN_OUT" "INPUT:$CHAIN_IN"; do
  parent=${pair%%:*}; child=${pair#*:}
  refs=$(iptables -S "$parent" 2>/dev/null | grep -cE -- "-j ${child}([[:space:]]|\$)")
  if [ "$refs" = "0" ] && [ "${PM_REPAIR_JUMPS:-1}" = "1" ] && [ "${1:-}" != "--settle" ]; then
    iptables -I "$parent" 1 -m comment --comment "rm-pm3-auto-$parent" -j "$child" 2>/dev/null && JUMP_REPAIRED=1
  fi
done
outrefs=$(iptables -S OUTPUT 2>/dev/null | grep -cE -- "-j ${CHAIN_OUT}([[:space:]]|\$)")
inrefs=$(iptables -S INPUT 2>/dev/null | grep -cE -- "-j ${CHAIN_IN}([[:space:]]|\$)")
[ "$outrefs" = "1" ] && [ "$inrefs" = "1" ] && JUMP_OK=1

if [ "${1:-}" = "--install-only" ]; then
  [ "$JUMP_OK" = "1" ] && [ "$MISSING_RULES" = "0" ] || fail_exit "early counter install incomplete"
  log "BOOT_COUNTERS_READY boot_id=$BOOT_ID rules_sha=$RULES_SHA added=$ADDED_RULES"
  exit 0
fi

# ---- 读计数：空=读失败；0 合法（零流量健康 0） ----
read_counter() { # $1=chain $2=ep-tag → 非负整数或空
  iptables -L "$1" -v -x -n 2>/dev/null | awk -v t="$2" '
    { if (match($0, /\/\*[^*]*\*\//)) { c=substr($0,RSTART,RLENGTH); gsub(/[^A-Za-z0-9_-]/,"",c);
        if (c==t && $2 ~ /^[0-9]+$/) { print $2; exit } } }'
}

# ---- 采集当前样本（结构化单行 JSON，键=端点标签；读失败端点不进样本） ----
PMDIR=$(dirname "$STATE")
CURF=$PMDIR/.pm3-cur.$$
PREVF=$PMDIR/.pm3-prev.$$
trap 'rm -f "$CURF" "$PREVF"' EXIT INT TERM
: > "$CURF" || fail_exit "cannot write sample tmp (disk full?)"
FAILS=""
FIRST=1
while IFS='|' read -r ep dir ip proto port; do
  case "$ep" in ''|'#'*) continue ;; esac
  case "$ep" in *[!A-Za-z0-9_-]*) fail_exit "bad endpoint tag: $ep" ;; esac
  if [ "$dir" = "up" ]; then CH=$CHAIN_OUT; else CH=$CHAIN_IN; fi
  v=$(read_counter "$CH" "$ep")
  if [ -z "$v" ]; then
    FAILS="$FAILS,${ep}_${dir}"
    continue
  fi
  if [ "$FIRST" = "1" ]; then printf '"%s":%s' "${ep}_${dir}" "$v" >> "$CURF"; FIRST=0
  else printf ',"%s":%s' "${ep}_${dir}" "$v" >> "$CURF"; fi
done < "$RULES"

# ---- Strict coverage status: fixed codes, never raw labels. ----
IO_HELPER=${PM_IO_HELPER:-$(dirname "$0")/proxy-meter-io.py}
PYTHON=${PM_PYTHON:-python3}
ACTIVE_ARG=""
[ "${PM_CHECK_ACTIVE:-1}" = "1" ] && ACTIVE_ARG="--active"
REFRESH_ARG=""
[ "${PM_REFRESH_SNAPSHOT:-1}" = "1" ] && [ "$COVER" = "/etc/routermgmt/proxy-meter-coverage-v3.json" ] && REFRESH_ARG="--refresh"
COVER_META=$("$PYTHON" "$IO_HELPER" coverage --cover "$COVER" --rules "$RULES" --api "${PM_API:-http://127.0.0.1:9090}" $ACTIVE_ARG $REFRESH_ARG 2>/dev/null) || fail_exit "coverage validator unavailable"
COVER_NOTE=$(printf '%s\n' "$COVER_META" | sed -n 's/^COVER_NOTE=//p')
ACTIVE_UNCOVERED=$(printf '%s\n' "$COVER_META" | sed -n 's/^ACTIVE_UNCOVERED=//p')
COVER_COMPLETE=$(printf '%s\n' "$COVER_META" | sed -n 's/^COVER_COMPLETE=//p')
case "$COVER_NOTE" in ok|invalid_cover|stale_sources|snapshot_refreshed|active_unregistered|api_or_coverage_error) ;; *) fail_exit "invalid coverage status" ;; esac
case "$ACTIVE_UNCOVERED" in none|unregistered) ;; *) fail_exit "invalid coverage identity" ;; esac
case "$COVER_COMPLETE" in 0|1) ;; *) fail_exit "invalid coverage completeness" ;; esac

[ "$JUMP_REPAIRED" = "1" ] && COVER_NOTE="jump_repaired"
[ "$ADDED_RULES" -gt 0 ] && COVER_NOTE="rules_repaired"

# ---- 权威状态完好性预检（损坏保留取证副本后重建） ----
STATE_MODE="absent"
if [ -f "$STATE" ]; then
  if "$PYTHON" "$IO_HELPER" normalize "$STATE" > "$PREVF" 2>/dev/null; then
    STATE_MODE="ok"
  else
    STATE_MODE="corrupt"
  fi
fi
PREV_EPOCH=$(sed -n 's/.*"prev_epoch":\([0-9]*\).*/\1/p' "$PREVF" 2>/dev/null)
EXPECTED_PREV_DAY=""
[ -n "$PREV_EPOCH" ] && EXPECTED_PREV_DAY=$(TZ="$TZLOCAL" date -d "@$PREV_EPOCH" '+%F' 2>/dev/null)
CORRUPT_TS=""
if [ "$STATE_MODE" = "corrupt" ] || [ "$STATE_MODE" = "unreadable" ]; then
  CORRUPT_TS=$(date '+%Y%m%d-%H%M%S')
  cp "$STATE" "$STATE.corrupt-$CORRUPT_TS" 2>/dev/null
  log "STATE $STATE_MODE; forensic copy .corrupt-$CORRUPT_TS; rebaseline"
fi
[ "$STATE_MODE" = "ok" ] || : > "$PREVF"   # 无前状态/损坏：空 prev 文件（awk 需可读）

# ---- 差分/日账计算：prev-state 与 cur-sample 为两个独立文件参数（结构化解析） ----
# 注：busybox awk 无用户自定义函数（R2 真机实测拒绝），键提取全部内联。
AWKOUT=$(awk \
  -v prev_mode="$STATE_MODE" -v boot="$BOOT_ID" -v day="$DAY_KEY" \
  -v now_iso="$NOW_ISO" -v now_ep="$NOW_EPOCH" -v meter="$METER_ID" \
  -v rules_sha="$RULES_SHA" -v fails="${FAILS#,}" -v miss_rules="$MISSING_RULES" \
  -v expected_pday="$EXPECTED_PREV_DAY" -v cover_complete="$COVER_COMPLETE" -v jump_ok="$JUMP_OK" -v cover_note="$COVER_NOTE" -v active_unc="$ACTIVE_UNCOVERED" '
FILENAME == ARGV[1] && prev_mode == "ok" {
  pline = $0
  k = "\"seq\":"
  if (index(pline, k)) { m = substr(pline, index(pline, k) + length(k)); if (match(m, /^[0-9]+/)) pseq = substr(m, 1, RLENGTH) + 0 }
  k = "\"prev_epoch\":"
  if (index(pline, k)) { m = substr(pline, index(pline, k) + length(k)); if (match(m, /^[0-9]+/)) pepoch = substr(m, 1, RLENGTH) + 0 }
  k = "\"day_total\":"
  if (index(pline, k)) { m = substr(pline, index(pline, k) + length(k)); if (match(m, /^[0-9]+/)) pday_total = substr(m, 1, RLENGTH) + 0 }
  k = "\"boot_id\":\""
  if (index(pline, k)) { m = substr(pline, index(pline, k) + length(k)); pboot = substr(m, 1, index(m, "\"") - 1) }
  k = "\"continuity_id\":\""
  if (index(pline, k)) { m = substr(pline, index(pline, k) + length(k)); pcont = substr(m, 1, index(m, "\"") - 1) }
  k = "\"day_key\":\""
  if (index(pline, k)) { m = substr(pline, index(pline, k) + length(k)); pday = substr(m, 1, index(m, "\"") - 1) }
  k = "\"rules_sha\":\""
  if (index(pline,k)) {m=substr(pline,index(pline,k)+length(k));prules=substr(m,1,index(m,"\"")-1)}
  k = "\"day_first_at\":\""
  if (index(pline, k)) { m = substr(pline, index(pline, k) + length(k)); pday_first = substr(m, 1, index(m, "\"") - 1) }
  directional = (index(pline, "\"counter_key_format\":\"direction-v1\"") > 0)
  k = "\"crossday_unassigned\":"
  if (index(pline,k)) {m=substr(pline,index(pline,k)+length(k));if(match(m,/^[0-9]+/)) prev_unassigned=substr(m,1,RLENGTH)+0}
  previous_partial = (index(pline, "\"partial\":true") > 0)
  # 内层 endpoints 对象（自产扁平结构，到第一个 } 止）
  marker = "\"endpoints\":{"
  if (index(pline, marker)) {
    inner = substr(pline, index(pline, marker) + length(marker))
    inner = substr(inner, 1, index(inner, "}") - 1)
    while (match(inner, /"[A-Za-z0-9_-]+":[0-9]+/)) {
      pair = substr(inner, RSTART, RLENGTH); inner = substr(inner, RSTART + RLENGTH)
      split(pair, pr, ":"); key = substr(pr[1], 2, length(pr[1]) - 2)
      prev[key] = pr[2] + 0
    }
  }
}
FILENAME == ARGV[2] {
  cline = $0
  while (match(cline, /"[A-Za-z0-9_-]+":[0-9]+/)) {
    pair = substr(cline, RSTART, RLENGTH); cline = substr(cline, RSTART + RLENGTH)
    split(pair, pr, ":"); key = substr(pr[1], 2, length(pr[1]) - 2)
    cur[key] = pr[2] + 0
  }
}
END {
  events = ""; health = "ok"; delta = 0; rebuilt = ""; neweps = ""; dropped = ""
  nf = split(fails, fa, ","); for (i = 1; i <= nf; i++) if (fa[i] != "") failarr[fa[i]] = 1

  if (prev_mode != "ok") {
    seq = 1; cont = "init-" boot; pday = ""; pday_total = 0; pcont = "none"
    day_total = 0; day_first = now_iso
    if (prev_mode == "absent") day_events = "init_baseline"
    else { day_events = "corrupt_recovery"; health = "degraded" }
    rebooted = 1
  } else {
    seq = pseq + 1; rebooted = (boot != pboot)
    if (prules!="" && prules!=rules_sha) {health="degraded";day_events="registry_change_unknown"}
    if (!directional) {
      # Old state merged upload/download keys; keep its historical lower bound,
      # but do not compare incompatible counters or invent the missing direction.
      delete prev; events = events "direction_migration,"; health = "degraded"
      day_events = "direction_migration"; previous_partial = 1
    }
    if (rebooted) { cont = "reboot-" boot; health = "degraded"; day_events = "reboot_rebaseline"; events = events "reboot," }
    else cont = pcont
    if (pday == "") { pday = day; pday_total = 0 }
    skew = 0
    if (expected_pday == "" || expected_pday != pday) {skew=1;health="degraded";day_events=day_events ",invalid_day_key"}
    if (pepoch != "" && now_ep + 0 <= pepoch + 0) { events = events "time_skew,"; skew = 1 }
    # Q1.3（2026-11-19 Batch1）日界修复：正常无缝跨日（无重启/无缺口/无时钟异常）
    # 记 rollover_clean，不再触发 partial；仅伴随异常的跨日记 rollover_partial。
    exact_boundary = ((now_ep+28800) % 86400 == 0)
    if (pday != day) {
      gap_now = (pepoch != "" && now_ep - pepoch > 420) ? 1 : 0
      if (!rebooted && !gap_now && !skew && exact_boundary) {
        day_events = (day_events == "" ? "" : day_events ",") "rollover_clean"
        events = events "rollover_clean,"
      } else {
        day_events = (day_events == "" ? "" : day_events ",") "rollover_partial"
        events = events "rollover_partial,"
      }
    }
    day_first = (pday_first != "") ? pday_first : now_iso
    if (rebooted) {
      # 重启：全体重定基线（无论计数方向），日账保留
      for (e in cur) { }
    } else {
      for (e in cur) {
        if (!(e in prev)) { neweps = neweps e ","; continue }        # 首见：重定基线，贡献 0
        d = cur[e] - prev[e]
        if (d < 0) { rebuilt = rebuilt e ","; continue }             # 同 boot 计数下降：重建，重定基线
        delta += d
      }
    }
    for (e in prev) {
      if (e in cur) continue
      if (e in failarr) continue                                    # 读失败：保留上一好值
      dropped = dropped e ","                                       # 规则移除：覆盖缺口
    }
    if (rebuilt != "") { events = events "rebuild,"; health = "degraded"; day_events = (day_events == "" ? "" : day_events ",") "rebuild" }
    if (neweps != "") {events = events "new_ep,";health="degraded";day_events=day_events ",new_ep_unknown"}
    if (dropped != "") { events = events "drop_ep,"; health = "degraded"; day_events = (day_events == "" ? "" : day_events ",") "drop_ep" }
    if (pday != day) {
      archive_total=pday_total;archive_unassigned=prev_unassigned
      if (exact_boundary && !rebooted && !skew) {archive_total+=delta;unassigned=0}
      else {unassigned=delta;archive_unassigned+=delta}
      day_total=0;day_first=now_iso
    }
    else {day_total = pday_total + delta;unassigned=prev_unassigned}
  }
  if (fails != "") health = "degraded"
  if (miss_rules + 0 > 0) { health = "degraded"; events = events "rules_add_fail," }
  if (jump_ok != "1") { health = "degraded"; events = events "jump_missing," }
  if (cover_note != "ok") { health = "degraded"; events = events "cover_" cover_note "," }
  if (active_unc != "none" && active_unc != "") { health = "degraded"; events = events "uncovered_active:" active_unc "," }
  if (now_ep + 0 <= pepoch + 0 && prev_mode == "ok") { health = "degraded"; day_events = day_events ",time_skew" }
  if (prev_mode == "ok" && now_ep - pepoch > 420) { health = "degraded"; day_events = day_events ",sample_gap" }
  sub(/,$/, "", events); if (events == "") events = "-"
  sub(/,$/, "", rebuilt); sub(/,$/, "", neweps); sub(/,$/, "", dropped); sub(/,$/, "", fails)
  if (fails == "") fails = "-"

  # 新权威状态：成功读到的端点=当前值；读失败端点=保留上一好值；重建端点=新基线值
  ne = 0; for (e in cur) ord[++ne] = e
  for (e in prev) if (!(e in cur)) { if (e in failarr) { ord[++ne] = e } }
  eps = ""
  for (i = 1; i <= ne; i++) {
    e = ord[i]
    val = (e in cur) ? cur[e] : prev[e]
    sep = (i < ne) ? "," : ""
    eps = eps "\"" e "\":" sprintf("%.0f", val) sep
  }
  # Q1.3（2026-11-19 Batch1）资格状态机：
  # 1) previous_partial 只在同一自然日内传染；新自然日（pday != day）清零，历史 partial 不再永久传染。
  # 2) rollover 正则收紧为 rollover_partial——rollover_clean（正常换日）不算 partial。
  prev_partial_effective = (pday != day) ? 0 : previous_partial
  partial = (prev_partial_effective || prev_mode != "ok" || health != "ok" || day_events ~ /reboot|rebuild|corrupt|migration|skew|gap|rollover_partial|invalid_day_key|new_ep_unknown|registry_change_unknown/) ? "true" : "false"
  # 3) quota_eligible 动态计算（原为写死 false 常量，导致观察期永远无法起算）：
  qe = (health == "ok" && partial == "false" && (active_unc == "none" || active_unc == "") && cover_note == "ok" && cover_complete == "1") ? "true" : "false"
  printf "{\"schema\":3,\"counter_key_format\":\"direction-v1\",\"meter_id\":\"%s\",\"seq\":%.0f,\"boot_id\":\"%s\",\"rules_sha\":\"%s\",\"continuity_id\":\"%s\",\"prev_continuity\":\"%s\",\"prev_epoch\":%.0f,\"day_key\":\"%s\",\"day_total\":%.0f,\"day_first_at\":\"%s\",\"health\":\"%s\",\"partial\":%s,\"quota_eligible\":%s,\"coverage_complete\":%s,\"crossday_unassigned\":%.0f,\"read_fail_eps\":\"%s\",\"endpoints\":{%s}}\n", meter, seq, boot, rules_sha, cont, pcont, now_ep, day, day_total, day_first, health, partial, qe, (cover_complete=="1"?"true":"false"), unassigned, fails, eps
  day_delta=(pday!=day?0:delta)
  printf "{\"day_key\":\"%s\",\"total\":%.0f,\"delta_last\":%.0f,\"sample_delta\":%.0f,\"sampled_at\":\"%s\",\"meter_id\":\"%s\",\"seq\":%.0f,\"health\":\"%s\",\"partial\":%s,\"quota_eligible\":%s,\"coverage_complete\":%s,\"crossday_unassigned\":%.0f,\"events\":\"%s\",\"read_fail_eps\":\"%s\"}\n", day, day_total, day_delta, delta, now_iso, meter, seq, health, partial, qe, (cover_complete=="1"?"true":"false"), unassigned, day_events, fails
  printf "DELTA=%.0f HEALTH=%s EVENTS=%s DAY_TOTAL=%.0f\n", delta, health, events, day_total
  archive_partial=(previous_partial || archive_unassigned>0 || !exact_boundary || health!="ok" || cover_complete!="1")?"true":"false"
  if (pday != day && pday != "") printf "{\"day_key\":\"%s\",\"lower_bound\":%.0f,\"crossday_unassigned\":%.0f,\"closed_at\":\"%s\",\"partial\":%s,\"source_seq\":%.0f}\n",pday,archive_total,archive_unassigned,now_iso,archive_partial,seq

}' "$PREVF" "$CURF" 2>>"$LOG") || fail_exit "compute failed"

NEW_STATE=$(printf '%s\n' "$AWKOUT" | sed -n '1p')
NEW_DAY=$(printf '%s\n'   "$AWKOUT" | sed -n '2p')
SUMMARY=$(printf '%s\n'   "$AWKOUT" | sed -n '3p')
[ -n "$NEW_STATE" ] && [ -n "$NEW_DAY" ] && [ -n "$SUMMARY" ] || fail_exit "compute produced empty output"
printf '%s' "$NEW_STATE" | grep -q '"schema":3' || fail_exit "computed state malformed"

OLD_DAY=$(printf '%s\n' "$AWKOUT" | sed -n '4p')
if [ -n "$OLD_DAY" ]; then
  ARCHIVE_DIR="${PM_ARCHIVE_DIR:-$(dirname "$STATE")/proxy-meter-days}"
  mkdir -p "$ARCHIVE_DIR" || fail_exit "day archive directory failed"
  OLD_KEY=$(printf '%s' "$OLD_DAY" | sed -n 's/.*"day_key":"\([0-9-]*\)".*/\1/p')
  [ -n "$OLD_KEY" ] || fail_exit "day archive key invalid"
  ARCHIVE_PATH="$ARCHIVE_DIR/$OLD_KEY-close-$NOW_EPOCH.json"
  if [ -e "$ARCHIVE_PATH" ]; then
    [ "$(cat "$ARCHIVE_PATH")" = "$OLD_DAY" ] || fail_exit "conflicting day archive"
  else
    printf '%s\n' "$OLD_DAY" > "$ARCHIVE_PATH.tmp" && mv "$ARCHIVE_PATH.tmp" "$ARCHIVE_PATH" || fail_exit "day archive commit failed"
  fi
fi

# ---- 原子提交：权威状态一次 mv；日账为派生视图（失败可重建，不阻塞权威提交） ----
printf '%s\n' "$NEW_STATE" > "$STATE.tmp" && mv "$STATE.tmp" "$STATE" || fail_exit "state commit failed"
printf '%s\n' "$NEW_DAY" > "$DAYF.tmp" && mv "$DAYF.tmp" "$DAYF" 2>/dev/null || log "WARN daily view write failed (authoritative state intact)"

# ---- 留存轮转 ----
SZ=$(stat -c %s "$LOG" 2>/dev/null || echo 0)
[ "$SZ" -gt $LOG_MAX ] && mv "$LOG" "$LOG.$(date '+%Y%m%d-%H%M%S')" 2>/dev/null

echo "OK $SUMMARY"
log "OK $SUMMARY"
case "$SUMMARY" in
  *HEALTH=ok*) exit 0 ;;
  *) exit 3 ;;
esac
