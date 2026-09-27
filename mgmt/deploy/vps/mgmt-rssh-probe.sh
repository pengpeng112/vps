#!/bin/bash
# mgmt-rssh-probe — VPS 本机反向 SSH 排障口健康探测器（2026-09-24 阶段1C）
#
# 背景：路由器 mgmt-tunnel 的 -R 排障转发（雅典娜 18222 / SN-R1 13222）由 Dropbear
# dbclient 建立，无 ExitOnForwardFailure：绑定失败时进程不退出，procd 显示 running
# 的“半通假健康”。本脚本由 systemd timer 每 30s 调起，对每个口做
#   1) 127.0.0.1:PORT 是否监听
#   2) 能否读到以 "SSH-" 开头的 banner（仅监听不等于可用）
# 结果上报管理台内部接口（X-Internal-Token），由后端落库/告警/下发白名单修复任务。
#
# 修复策略（防无限重启）：单轮故障最多自动修复 2 次（间隔 REPAIR_COOLDOWN 秒），
# 仍失败则只持续上报（告警保持），等待人工处理。恢复后计数清零。
#
# 配置：/etc/mgmt-rssh-probe.conf  每行 "<端口> <设备ID>"，# 为注释
# 令牌：/etc/mgmt-rssh-probe.token 600 root（即管理台 INTERNAL_TOKEN）
# 状态：/var/lib/mgmt-rssh-probe/<端口>.state

set -u
CONF=${MGMT_RSSH_PROBE_CONF:-/etc/mgmt-rssh-probe.conf}
TOKEN_FILE=${MGMT_RSSH_PROBE_TOKEN:-/etc/mgmt-rssh-probe.token}
STATE_DIR=${MGMT_RSSH_PROBE_STATE:-/var/lib/mgmt-rssh-probe}
API=${MGMT_RSSH_PROBE_API:-https://10.10.0.1:8443/api/v1/internal/tunnel-health}
LOGFILE=/var/log/mgmt-rssh-probe.log
ALERT_THRESHOLD=3
MAX_REPAIRS=2
REPAIR_COOLDOWN=300   # 两次自动修复之间的最小间隔（秒）

log() {
  echo "$(date '+%F %T') $*" >> "$LOGFILE"
  logger -t mgmt-rssh-probe -- "$*" 2>/dev/null || true
}

now_epoch() { date +%s; }
now_rfc3339() { date -u +%Y-%m-%dT%H:%M:%SZ; }

# 探测一个端口：0=健康(监听+SSH banner)，1=异常
probe_port() {
  local p="$1" banner
  if ! ss -lntH "sport = :$p" 2>/dev/null | grep -q '127\.0\.0\.1:'"$p"; then
    return 1
  fi
  banner=$(timeout 5 nc 127.0.0.1 "$p" </dev/null 2>/dev/null | head -c 8 | tr -d '\r\n')
  case "$banner" in
    SSH-*) return 0 ;;
    *) return 1 ;;
  esac
}

# 上报；$1=device_id $2=port $3=alive(0/1) $4=cf $5=first_fail(可为空)
# $6=request_repair(0/1) $7=repair_idem(可为空)
report() {
  local dev="$1" port="$2" alive="$3" cf="$4" first="$5" rrep="$6" ridem="$7"
  local body
  # alive/rrep 必须是 JSON 字面布尔（数字 1/0 会让后端 ShouldBindJSON 失败）
  _alive=false; [ "$alive" = "1" ] && _alive=true
  _rrep=false;  [ "$rrep" = "1" ] && _rrep=true
  if [ -n "$first" ]; then
    body=$(printf '{"device_id":"%s","port":%s,"alive":%s,"consecutive_failures":%s,"first_failure_at":"%s","request_repair":%s,"repair_idem_key":"%s"}' \
      "$dev" "$port" "$_alive" "$cf" "$first" "$_rrep" "$ridem")
  else
    body=$(printf '{"device_id":"%s","port":%s,"alive":%s,"consecutive_failures":%s}' \
      "$dev" "$port" "$_alive" "$cf")
  fi
  local code
  code=$(curl -sk -m 8 -o /tmp/mgmt-rssh-probe.resp -w '%{http_code}' \
    -H "X-Internal-Token: $(cat "$TOKEN_FILE" 2>/dev/null)" \
    -H 'Content-Type: application/json' \
    -d "$body" "$API" 2>/dev/null || echo 000)
  if [ "$code" != "200" ]; then
    log "report FAILED http=$code port=$port dev=$dev alive=$alive cf=$cf body=$(head -c 120 /tmp/mgmt-rssh-probe.resp 2>/dev/null)"
    return 1
  fi
  return 0
}

# 读取状态键 $1=port $2=key
st_get() { sed -n "s/^$2=//p" "$STATE_DIR/$1.state" 2>/dev/null | tail -n1; }
st_set() { # $1=port 余下为 key=value 对（整体重写）
  local p="$1"; shift
  : > "$STATE_DIR/$p.state"
  printf '%s\n' "$@" >> "$STATE_DIR/$p.state"
}

mkdir -p "$STATE_DIR"
[ -f "$CONF" ] || { log "FATAL: missing $CONF"; exit 1; }
[ -s "$TOKEN_FILE" ] || { log "FATAL: missing/empty $TOKEN_FILE"; exit 1; }

grep -Ev '^\s*(#|$)' "$CONF" | while read -r port dev; do
  case "$port" in ''|*[!0-9]*) log "skip invalid conf line: $port $dev"; continue ;; esac
  [ -n "$dev" ] || continue

  if probe_port "$port"; then
    # 恢复/持续健康：仅在从故障转健康时打日志；每次都上报以刷新 last_check
    if [ -n "$(st_get "$port" cf)" ] && [ "$(st_get "$port" cf)" != "0" ]; then
      log "RECOVERED port=$port dev=$dev (was cf=$(st_get "$port" cf))"
    fi
    report "$dev" "$port" 1 0 "" 0 "" || true
    rm -f "$STATE_DIR/$port.state"
  else
    cf=$(st_get "$port" cf); case "$cf" in ''|*[!0-9]*) cf=0 ;; esac
    cf=$((cf + 1))
    first=$(st_get "$port" first)
    if [ -z "$first" ]; then
      first=$(now_rfc3339)
      log "FAILING port=$port dev=$dev cf=$cf first=$first"
    fi
    outage=$(st_get "$port" outage); [ -n "$outage" ] || outage=$(date -u +%Y%m%dT%H%M%S)
    repairs=$(st_get "$port" repairs); case "$repairs" in ''|*[!0-9]*) repairs=0 ;; esac
    next_repair=$(st_get "$port" next_repair); case "$next_repair" in ''|*[!0-9]*) next_repair=0 ;; esac

    rrep=0; ridem=""
    if [ "$cf" -ge "$ALERT_THRESHOLD" ]; then
      now=$(now_epoch)
      if [ "$repairs" -lt "$MAX_REPAIRS" ] && [ "$now" -ge "$next_repair" ]; then
        repairs=$((repairs + 1))
        next_repair=$((now + REPAIR_COOLDOWN))
        rrep=1
        ridem="tunnel-repair-$port-$outage-$repairs"
        log "DISPATCH_REPAIR port=$port dev=$dev attempt=$repairs cf=$cf idem=$ridem"
      elif [ "$repairs" -ge "$MAX_REPAIRS" ]; then
        log "MAX_REPAIR_REACHED port=$port dev=$dev cf=$cf — manual intervention required"
      fi
    fi
    st_set "$port" "cf=$cf" "first=$first" "outage=$outage" "repairs=$repairs" "next_repair=$next_repair"
    report "$dev" "$port" 0 "$cf" "$first" "$rrep" "$ridem" || true
  fi
done
rm -f /tmp/mgmt-rssh-probe.resp
