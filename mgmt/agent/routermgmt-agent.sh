#!/bin/sh
# routermgmt-agent — 路由器管理台 Agent（busybox ash，OpenWrt/iStoreOS）
# 依赖：curl openssl-util jsonfilter（由 install-agent.sh 探测安装）
# 配置：/etc/routermgmt/agent.conf（600）：DEVICE_ID HMAC_KEY MGMT_URL SUB_URL SIGN_PUB
# 主循环：心跳 60s + 任务轮询 10s（测试可用 HEARTBEAT_INTERVAL/TASK_INTERVAL 覆盖）
#
# 安全约定（任务书 §7/§14）：
#  - 禁止 sed/正则解析任务 JSON；一律 jsonfilter
#  - 禁止 eval 服务器参数；shell 变量一律加引号
#  - 每次请求新 nonce；重试也换 nonce（服务端防重放）
#  - 请求路径不含 query；变量名不用 PATH

set -u

CONF_PATH="${RM_CONF:-/etc/routermgmt/agent.conf}"
STATE_DIR="${RM_STATE_DIR:-/etc/routermgmt/state}"
LOG_TAG="routermgmt-agent"

HEARTBEAT_INTERVAL="${HEARTBEAT_INTERVAL:-60}"
TASK_INTERVAL="${TASK_INTERVAL:-10}"
PROXY_PORT="${RM_PROXY_PORT:-127.0.0.1:7890}"
MIHOMO_API="${RM_MIHOMO_API:-127.0.0.1:9090}"
EGRESS_CHECK_URL="${RM_EGRESS_CHECK_URL:-https://www.cloudflare.com/cdn-cgi/trace}"
CN_CHECK_URL="${RM_CN_CHECK_URL:-https://www.baidu.com}"
WAN_IF="${RM_WAN_IF:-}"
CURL_OPTS="${RM_CURL_OPTS:--sS -m 10 -k}"

log() {
    echo "$(date '+%F %T') $*" >&2
    logger -t "$LOG_TAG" -- "$*" 2>/dev/null || true
}

die() {
    log "FATAL: $*"
    exit 1
}

# ---- 配置加载：先严格校验每一行格式，再 source（§14：只允许严格编码的本地生成内容）----
load_conf() {
    [ -f "$CONF_PATH" ] || die "缺少配置 $CONF_PATH"
    while IFS= read -r line; do
        case "$line" in
            ""|\#*) continue ;;
            DEVICE_ID=*|HMAC_KEY=*|MGMT_URL=*|SUB_URL=*|SIGN_PUB=*)
                case "$line" in
                    *[!A-Za-z0-9_=:/.\?\&\-]*) die "配置行含非法字符: ${line%%=*}" ;;
                esac ;;
            *) die "配置包含不允许的键: $line" ;;
        esac
    done < "$CONF_PATH"
    # shellcheck disable=SC1090
    . "$CONF_PATH"
    [ -n "${DEVICE_ID:-}" ] && [ -n "${HMAC_KEY:-}" ] && [ -n "${MGMT_URL:-}" ] || die "配置缺少必填键"
}

# ---- HMAC 签名请求（§7.1，与 Go 侧 CanonicalString 完全一致）----
# 用法: api_call METHOD REQUEST_PATH BODY_FILE OUT_FILE
# 请求路径不含 query；POST 用 --data-binary @file 保证签名字节=发送字节
api_call() {
    _method="$1"; _rpath="$2"; _body="$3"; _out="$4"
    _ts=$(date +%s)
    _nonce=$(openssl rand -hex 16)
    _bhash=$(openssl dgst -sha256 -hex "$_body" | awk '{print $NF}')
    _sig=$(printf '%s\n%s\n%s\n%s\n%s\n%s' \
        "$DEVICE_ID" "$_ts" "$_nonce" "$_method" "$_rpath" "$_bhash" \
        | openssl dgst -sha256 -hmac "$HMAC_KEY" -hex | awk '{print $NF}')
    _hdrdump=""
    [ -n "${RM_HDR_FILE:-}" ] && _hdrdump="-D $RM_HDR_FILE"
    if [ "$_method" = "GET" ]; then
        curl $CURL_OPTS -X GET -o "$_out" -w '%{http_code}' $_hdrdump \
            -H "X-Device-Id: $DEVICE_ID" -H "X-Timestamp: $_ts" -H "X-Nonce: $_nonce" \
            -H "Authorization: HMAC-SHA256 $_sig" \
            "$MGMT_URL$_rpath" 2>/dev/null
    else
        curl $CURL_OPTS -X "$_method" -o "$_out" -w '%{http_code}' \
            -H "X-Device-Id: $DEVICE_ID" -H "X-Timestamp: $_ts" -H "X-Nonce: $_nonce" \
            -H "Authorization: HMAC-SHA256 $_sig" \
            -H "Content-Type: application/json" \
            --data-binary "@$_body" \
            "$MGMT_URL$_rpath" 2>/dev/null
    fi
}

json_str() { jsonfilter -s "$1" -e "$2" 2>/dev/null; }

# ---- 指标采集（缺数据输出 null；代理采集口径见 §19.5）----
jstr() { # 字符串转 JSON 字符串或 null（转义反斜杠与引号，避免指标字段注入）
    if [ -z "$1" ]; then printf 'null'; return; fi
    printf '"'
    printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
    printf '"'
}
jnum() {
    if [ -n "$1" ]; then printf '%s' "$1"; else printf 'null'; fi
}

collect_metrics() { # $1 = 输出文件
    _out="$1"
    _sample=$(cat /proc/sys/kernel/random/uuid 2>/dev/null)
    _boot=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null)
    [ -n "$_sample" ] || _sample="s-$(date +%s)-$$"
    [ -n "$_boot" ] || _boot="boot-unknown"

    # CPU%（/proc/stat 相邻采样差值）
    _cpu=""
    _tot1=$(awk '/^cpu /{print $2+$3+$4+$5+$6+$7+$8}' /proc/stat 2>/dev/null)
    _idle1=$(awk '/^cpu /{print $5}' /proc/stat 2>/dev/null)
    if [ -f "$STATE_DIR/cpu.prev" ] && [ -n "$_tot1" ] && [ -n "$_idle1" ]; then
        read -r _ptot _pidle < "$STATE_DIR/cpu.prev" 2>/dev/null
        case "$_ptot" in ''|*[!0-9]*) _ptot="" ;; esac
        if [ -n "$_ptot" ] && [ "$_tot1" -gt "$_ptot" ] 2>/dev/null; then
            _cpu=$(awk -v t="$_tot1" -v i="$_idle1" -v pt="$_ptot" -v pi="$_pidle" \
                'BEGIN{d=t-pt; di=i-pi; if(d>0) printf "%.1f", (d-di)*100/d}')
        fi
    fi
    printf '%s %s\n' "${_tot1:-0}" "${_idle1:-0}" > "$STATE_DIR/cpu.prev" 2>/dev/null

    # 内存
    _memtotal=$(awk '/^MemTotal:/{print int($2/1024)}' /proc/meminfo 2>/dev/null)
    _memavail=$(awk '/^MemAvailable:/{print int($2/1024)}' /proc/meminfo 2>/dev/null)
    _memused=""
    [ -n "$_memtotal" ] && [ -n "$_memavail" ] && _memused=$(($_memtotal - $_memavail))

    # 温度（取最高温区）
    _temp=""
    for z in /sys/class/thermal/thermal_zone*/temp; do
        [ -f "$z" ] || continue
        v=$(cat "$z" 2>/dev/null)
        case "$v" in ''|*[!0-9]*) continue ;; esac
        v=$(awk -v v="$v" 'BEGIN{printf "%.1f", v/1000}')
        case "$v" in *.*) [ -z "$_temp" ] && _temp=$v || _temp=$(awk -v a="$_temp" -v b="$v" 'BEGIN{print (b>a)?b:a}') ;; esac
    done

    _disk=$(df -k /etc 2>/dev/null | awk 'NR==2{print int($4/1024)}')
    _uptime=$(cut -d. -f1 /proc/uptime 2>/dev/null)

    # WAN 接口与 IP（ubus 优先）
    _wanif="$WAN_IF"
    if [ -z "$_wanif" ] && command -v ubus >/dev/null 2>&1; then
        _wjson=$(ubus call network.interface.wan status 2>/dev/null)
        if [ -n "$_wjson" ]; then
            _wanip=$(json_str "$_wjson" '$.ipv4-address[0].address')
            _wanif=$(json_str "$_wjson" '$.device')
            [ -n "$_wanif" ] || _wanif=$(json_str "$_wjson" '$.l3_device')
        fi
    fi
    if [ -n "$_wanif" ] && [ -f "/sys/class/net/$_wanif/statistics/rx_bytes" ]; then
        _wanrx=$(cat "/sys/class/net/$_wanif/statistics/rx_bytes")
        _wantx=$(cat "/sys/class/net/$_wanif/statistics/tx_bytes")
    fi

    # 代理进程与 WG
    _clash=; pidof clash >/dev/null 2>&1 && _clash=true || { pidof mihomo >/dev/null 2>&1 && _clash=true; }
    _wg=; [ -d /sys/class/net/wg0 ] && _wg=true

    # 出口 IP/延迟/当前节点（经代理端口探测；外部探测服务不可用≠节点不可用）
    _exitip=""; _lat=""; _exitnode=""
    _t0=$(date +%s)
    _trace=$(curl $CURL_OPTS -x "http://$PROXY_PORT" "$EGRESS_CHECK_URL" 2>/dev/null)
    _t1=$(date +%s)
    if [ -n "$_trace" ]; then
        _exitip=$(printf '%s\n' "$_trace" | awk -F= '/^ip=/{print $2}' | head -n1)
        _lat=$(($_t1 - $_t0))
        [ -n "$_exitip" ] || _exitip=$(printf '%s\n' "$_trace" | head -n1)
    fi
    _pjson=$(curl $CURL_OPTS "http://$MIHOMO_API/proxies/PROXY" 2>/dev/null)
    [ -n "$_pjson" ] && _exitnode=$(json_str "$_pjson" '$.now')

    # 代理流量：只累计经节点的连接（非 DIRECT），轮询口径为估算（§19.5）
    _prx=""; _ptx=""; _pstatus="unavailable"; _pepoch=""
    _cjson=$(curl $CURL_OPTS "http://$MIHOMO_API/connections" 2>/dev/null)
    if [ -n "$_cjson" ]; then
        _cpid=$(pidof clash 2>/dev/null || pidof mihomo 2>/dev/null)
        _pepoch="$_boot-$_cpid"
        _chains=$(json_str "$_cjson" '$.connections[*].chains[0]')
        _downs=$(json_str "$_cjson" '$.connections[*].download')
        _ups=$(json_str "$_cjson" '$.connections[*].upload')
        if [ -n "$_chains" ] && [ -n "$_downs" ] && [ -n "$_ups" ]; then
            # 三个列表来自同一 JSON 的同序遍历，用分隔段对齐后累计
            _acc=$( { printf '%s\n' "$_chains"; echo "__SPLIT__"; printf '%s\n' "$_downs"; echo "__SPLIT__"; printf '%s\n' "$_ups"; } | awk '
                BEGIN{sec=1} # 首段从 1 开始：split 前的 chains 进段 1，避免整体错位一段
                $0=="__SPLIT__"{sec++; next}
                {cnt[sec]++; line[sec","cnt[sec]]=$0}
                END{
                    for(i=1;i<=cnt[1];i++){
                        ch=line[1","i]; dn=line[2","i]+0; up=line[3","i]+0
                        if(ch!="" && ch!="DIRECT" && ch!="COMPATIBLE" && ch!="REJECT"){
                            tot+=dn; tup+=up
                        }
                    }
                    printf "%d %d", tot+0, tup+0
                }')
            _prx=$(printf '%s' "$_acc" | awk '{print $1}')
            _ptx=$(printf '%s' "$_acc" | awk '{print $2}')
            case "$_prx" in ''|*[!0-9]*) _prx=""; _ptx=""; _pstatus="unavailable" ;; esac
            [ -n "$_prx" ] && _pstatus="partial" # 轮询会漏短连接：界面须标「估算」
        fi
    fi

    _cfgver=0
    [ -f "$STATE_DIR/accepted_version" ] && _cfgver=$(cat "$STATE_DIR/accepted_version" 2>/dev/null)
    case "$_cfgver" in ''|*[!0-9]*) _cfgver=0 ;; esac

    cat > "$_out" <<EOF
{
  "device_id": "$DEVICE_ID",
  "ts": $(date +%s),
  "metrics": {
    "cpu": $(jnum "$_cpu"),
    "mem_used_mb": $(jnum "$_memused"),
    "mem_total_mb": $(jnum "$_memtotal"),
    "temp_c": $(jnum "$_temp"),
    "disk_free_mb": $(jnum "$_disk"),
    "uptime_s": $(jnum "$_uptime"),
    "wan_ip": $(jstr "${_wanip:-}"),
    "exit_ip": $(jstr "$_exitip"),
    "exit_node": $(jstr "$_exitnode"),
    "exit_latency_ms": $(jnum "$_lat"),
    "clash_alive": $(jbool "$_clash"),
    "wg_alive": $(jbool "$_wg"),
    "wan_rx_bytes": $(jnum "${_wanrx:-}"),
    "wan_tx_bytes": $(jnum "${_wantx:-}"),
    "sample_id": "$_sample",
    "boot_id": "$_boot",
    "proxy_counter_epoch": $(jstr "$_pepoch"),
    "proxy_measurement_status": "$( [ -n "$_prx" ] && echo "$_pstatus" || echo unavailable )",
    "proxy_rx_bytes": $(jnum "${_prx:-}"),
    "proxy_tx_bytes": $(jnum "${_ptx:-}"),
    "config_version": $_cfgver
  }
}
EOF
}

jbool() { if [ -n "$1" ]; then echo true; else echo false; fi; }

send_heartbeat() {
    _body="$STATE_DIR/hb.json"
    collect_metrics "$_body"
    _code=$(api_call POST /api/v1/agent/heartbeat "$_body" "$STATE_DIR/hb.resp")
    if [ "$_code" = "200" ]; then
        log "heartbeat ok"
        apply_policy_from_hb 2>/dev/null || true
    else
        log "heartbeat failed http=$_code resp=$(head -c 200 "$STATE_DIR/hb.resp" 2>/dev/null)"
        return 1
    fi
}

# ---- 任务执行 ----
post_result() { # $1 task_id $2 lease_id $3 status $4 detail-json(对象或空)
    _tid="$1"; _lease="$2"; _st="$3"; _det="${4:-\{\}}"
    _body="$STATE_DIR/result.$$.json"
    printf '{"lease_id":"%s","status":"%s","detail":%s}\n' "$_lease" "$_st" "$_det" > "$_body"
    _try=0
    while [ "$_try" -lt 3 ]; do
        _code=$(api_call POST "/api/v1/agent/tasks/$_tid/result" "$_body" "$STATE_DIR/result.$$.resp")
        case "$_code" in 200|409) break ;; esac
        _try=$((_try + 1))
        sleep 2
    done
    rm -f "$_body" "$STATE_DIR/result.$$.resp"
    case "$_code" in
        200|409) return 0 ;;
        *) log "report result failed task=$_tid http=$_code"; return 1 ;;
    esac
}

# 任务结果落盘（网络断时先持久化；重启后补报）
persist_result() {
    mkdir -p "$STATE_DIR/results"
    printf '{"task_id":"%s","lease_id":"%s","status":"%s","detail":%s}\n' \
        "$1" "$2" "$3" "${4:-\{\}}" > "$STATE_DIR/results/$1.json"
}
clear_result() { rm -f "$STATE_DIR/results/$1.json"; }

flush_pending_results() {
    [ -d "$STATE_DIR/results" ] || return 0
    for f in "$STATE_DIR/results"/*.json; do
        [ -f "$f" ] || continue
        _tid=$(json_str "$(cat "$f")" '$.task_id')
        _lease=$(json_str "$(cat "$f")" '$.lease_id')
        _st=$(json_str "$(cat "$f")" '$.status')
        _det=$(json_str "$(cat "$f")" '$.detail')
        [ -n "$_det" ] || _det='{}'
        post_result "$_tid" "$_lease" "$_st" "$_det" && rm -f "$f"
    done
}

run_task() { # $1 = 单个任务的 JSON 文本
    _tj="$1"
    _tid=$(json_str "$_tj" '$.task_id')
    _type=$(json_str "$_tj" '$.type')
    _lease=$(json_str "$_tj" '$.lease_id')
    log "task start id=$_tid type=$_type"
    case "$_type" in
        switch_node|reload_proxy)
            task_apply_config "$_tj" ;;
        reboot)
            task_reboot "$_tj" ;;
        ping_test)
            task_ping_test "$_tj" ;;
        set_proxy_state)
            task_set_proxy_state "$_tj" ;;
        set_wifi)
            task_set_wifi "$_tj" ;;
        agent_update)
            task_agent_update "$_tj" ;;
        repair_mgmt_tunnel)
            task_repair_mgmt_tunnel "$_tj" ;;
        apply_policy|set_ratelimit|set_reboot_schedule|set_license)
            task_apply_policy "$_tj" ;;
        collect_diag)
            task_collect_diag "$_tj" ;;
        *)
            persist_result "$_tid" "$_lease" failed "{\"error\":\"unsupported task type\"}"
            ;;
    esac
    flush_pending_results
}

# 基础任务：ping_test / reboot（§14：限制目标格式/次数/超时，不经 eval）
task_ping_test() {
    _tj="$1"
    _tid=$(json_str "$_tj" '$.task_id'); _lease=$(json_str "$_tj" '$.lease_id')
    _host=$(json_str "$_tj" '$.params.host')
    case "$_host" in
        ''|*[!A-Za-z0-9._\-]*) 
            persist_result "$_tid" "$_lease" failed '{"error":"invalid host"}'
            return ;;
    esac
    _out=$(ping -c 1 -W 3 "$_host" 2>&1)
    _rc=$?
    if [ "$_rc" = "0" ]; then
        _ms=$(printf '%s\n' "$_out" | awk -F'time=' '/time=/{split($2,a," ");print a[1];exit}' | tr -d ' ' | awk -F. '{print int($1)}')
        case "$_ms" in ''|*[!0-9]*) _ms=0 ;; esac
        persist_result "$_tid" "$_lease" ok "{\"delay_ms\":$_ms,\"host\":\"$_host\"}"
    else
        persist_result "$_tid" "$_lease" failed "{\"error\":\"ping failed\",\"host\":\"$_host\"}"
    fi
}

task_reboot() {
    # 重启前持久化标记 + 旧 boot_id；重启后确认 boot_id 变化才报 ok（§19.2）
    _tj="$1"
    _tid=$(json_str "$_tj" '$.task_id'); _lease=$(json_str "$_tj" '$.lease_id')
    _oldboot=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null)
    mkdir -p "$STATE_DIR/reboot_pending"
    printf '%s\n%s\n%s\n' "$_tid" "$_lease" "$_oldboot" > "$STATE_DIR/reboot_pending/$_tid"
    sync
    # 先尝试回报"即将重启"无效——服务端要求最终 ok 必须带重启证据，这里保持 running
    sleep 2
    reboot || persist_result "$_tid" "$_lease" failed '{"error":"reboot command failed"}'
}

check_reboot_evidence() {
    # 开机恢复：boot_id 已变化 → 报 ok；未变化（没重启成）→ 报 failed
    [ -d "$STATE_DIR/reboot_pending" ] || return 0
    _nowboot=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null)
    for f in "$STATE_DIR/reboot_pending"/*; do
        [ -f "$f" ] || continue
        _tid=$(sed -n 1p "$f"); _lease=$(sed -n 2p "$f"); _old=$(sed -n 3p "$f")
        if [ "$_nowboot" != "$_old" ]; then
            persist_result "$_tid" "$_lease" ok '{"boot_id_changed":true}'
            rm -f "$f"
        else
            persist_result "$_tid" "$_lease" failed '{"error":"boot_id unchanged, reboot did not happen"}'
            rm -f "$f"
        fi
    done
}

poll_tasks() {
    : > "$STATE_DIR/tasks.body"
    _code=$(api_call GET /api/v1/agent/tasks "$STATE_DIR/tasks.body" "$STATE_DIR/tasks.resp")
    [ "$_code" = "200" ] || { [ -n "$_code" ] && log "poll tasks http=$_code"; return 0; }
    _one=$(json_str "$(cat "$STATE_DIR/tasks.resp")" '$.tasks[0]')
    [ -n "$_one" ] || return 0
    run_task "$_one"
}

# ---- 主循环 ----
main() {
    load_conf
    mkdir -p "$STATE_DIR"
    chmod 700 "$STATE_DIR" 2>/dev/null

    # 加载 handlers（同 shell 进程，函数共享）
    _hd="/etc/routermgmt/handlers/switch_node.sh"
    [ -f "$_hd" ] || _hd="$(dirname "$0")/handlers/switch_node.sh"
    [ -f "$_hd" ] || die "找不到 handlers/switch_node.sh"
    # shellcheck disable=SC1090
    . "$_hd"

    # 崩溃/重启恢复：未提交事务回滚 + reboot 证据 + 补报结果
    txn_recover_all
    check_reboot_evidence
    flush_pending_results

    _last_hb=0
    log "agent started device=$DEVICE_ID url=$MGMT_URL"
    while :; do
        _now=$(date +%s)
        if [ $(( _now - _last_hb )) -ge "$HEARTBEAT_INTERVAL" ]; then
            send_heartbeat && _last_hb=$_now
        fi
        flush_pending_results  # 每轮补报（重启证据等结果不能只靠启动时/有任务时上报）
        poll_tasks
        sleep "$TASK_INTERVAL"
    done
}

main "$@"
