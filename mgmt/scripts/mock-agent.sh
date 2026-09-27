#!/bin/sh
# mock-agent.sh — 验收用 mock 设备（任务书 §12.6）：
#   按 §7.1 HMAC 协议发心跳 N 次；可轮询任务并自动完成 ping_test。
#   在开发机（Git Bash/WSL）直接运行，走 web 入口 http://127.0.0.1:8080。
# 用法：
#   ./mock-agent.sh <bootstrap-package.json> [心跳次数，默认 3] [心跳间隔秒，默认 2]
set -u
PKG="${1:?用法: mock-agent.sh <bootstrap-package.json> [次数] [间隔]}"
COUNT="${2:-3}"
INTERVAL="${3:-2}"
API="${MOCK_API:-http://127.0.0.1:8080}"

command -v python3 >/dev/null 2>&1 || { echo "需要 python3"; exit 2; }

DEVICE_ID=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['device_id'])" "$PKG")
HMAC_KEY=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['hmac_key'])" "$PKG")
[ -n "$DEVICE_ID" ] && [ "$DEVICE_ID" != "None" ] || { echo "引导包缺 device_id"; exit 2; }

jfilter() { jsonfilter -s "$1" -e "$2" 2>/dev/null || true; }
command -v jsonfilter >/dev/null 2>&1 || jfilter() { python3 -c "
import json,sys
d=json.loads(sys.argv[1]); p=sys.argv[2]
for part in p.strip('\$').split('.'):
    if not part: continue
    if '[' in part:
        k,idx=part[:-1].split('['); d=d.get(k) if isinstance(d,dict) else d
        d=d[int(idx)] if idx!='*' else d
    else:
        d=d.get(part) if isinstance(d,dict) else None
print(d if not isinstance(d,(dict,list)) else json.dumps(d,separators=(',',':')))
" "$1" "$2" 2>/dev/null || true; }

sign_call() { # METHOD PATH BODY_FILE
    _m="$1"; _p="$2"; _b="$3"
    _ts=$(date +%s)
    _nonce=$(openssl rand -hex 16)
    _bh=$(openssl dgst -sha256 -hex "$_b" | awk '{print $NF}')
    _sig=$(printf '%s\n%s\n%s\n%s\n%s\n%s' "$DEVICE_ID" "$_ts" "$_nonce" "$_m" "$_p" "$_bh" \
        | openssl dgst -sha256 -hmac "$HMAC_KEY" -hex | awk '{print $NF}')
    if [ "$_m" = "GET" ]; then
        curl -s -o /tmp/ma.resp -w '%{http_code}' -X GET \
            -H "X-Device-Id: $DEVICE_ID" -H "X-Timestamp: $_ts" -H "X-Nonce: $_nonce" \
            -H "Authorization: HMAC-SHA256 $_sig" "$API$_p"
    else
        curl -s -o /tmp/ma.resp -w '%{http_code}' -X "$_m" \
            -H "X-Device-Id: $DEVICE_ID" -H "X-Timestamp: $_ts" -H "X-Nonce: $_nonce" \
            -H "Authorization: HMAC-SHA256 $_sig" -H 'Content-Type: application/json' \
            --data-binary "@$_b" "$API$_p"
    fi
}

echo "mock-agent device=$DEVICE_ID api=$API"
i=0
seq=0
while [ "$i" -lt "$COUNT" ]; do
    seq=$((seq + 1))
    cat > /tmp/ma.hb <<EOF
{"device_id":"$DEVICE_ID","ts":$(date +%s),"metrics":{
 "cpu":$((10 + seq)).5,"mem_used_mb":210,"mem_total_mb":512,"temp_c":58,
 "uptime_s":$((1000 * seq)),"wan_ip":"192.168.1.100","exit_ip":"203.0.113.50",
 "exit_node":"MOCK","exit_latency_ms":$((20 + seq)),
 "clash_alive":true,"wg_alive":true,
 "wan_rx_bytes":$((10000 * seq)),"wan_tx_bytes":$((5000 * seq)),
 "sample_id":"mock-$(date +%s)-$seq","boot_id":"mock-boot-1",
 "proxy_counter_epoch":"mock-1","proxy_measurement_status":"available",
 "proxy_rx_bytes":$((3000 * seq)),"proxy_tx_bytes":$((1500 * seq)),
 "config_version":0}}
EOF
    code=$(sign_call POST /api/v1/agent/heartbeat /tmp/ma.hb)
    echo "[$((i + 1))/$COUNT] heartbeat http=$code $(head -c 120 /tmp/ma.resp)"
    # 顺带领任务并自动完成 ping_test（验证闭环）
    : > /tmp/ma.empty
    code=$(sign_call GET /api/v1/agent/tasks /tmp/ma.empty)
    one=$(jfilter "$(cat /tmp/ma.resp)" '$.tasks[0]')
    if [ -n "$one" ]; then
        tid=$(jfilter "$one" '$.task_id'); lease=$(jfilter "$one" '$.lease_id'); type=$(jfilter "$one" '$.type')
        echo "  claimed task $tid type=$type"
        if [ "$type" = "ping_test" ]; then
            printf '{"lease_id":"%s","status":"ok","detail":{"delay_ms":12}}' "$lease" > /tmp/ma.res
            code=$(sign_call POST "/api/v1/agent/tasks/$tid/result" /tmp/ma.res)
            echo "  ping_test result http=$code"
        else
            printf '{"lease_id":"%s","status":"failed","detail":{"error":"mock agent only supports ping_test"}}' "$lease" > /tmp/ma.res
            sign_call POST "/api/v1/agent/tasks/$tid/result" /tmp/ma.res >/dev/null
        fi
    fi
    i=$((i + 1))
    [ "$i" -lt "$COUNT" ] && sleep "$INTERVAL"
done
echo "mock-agent 完成 $COUNT 次心跳"
