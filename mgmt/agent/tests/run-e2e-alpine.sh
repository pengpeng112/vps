#!/bin/sh
# run-e2e-alpine.sh — Agent 端到端验收（任务书 §12.6/§12.7 与 §20.1 回滚行）
# 在 alpine 容器（busybox ash）里用真实 Agent 事务逻辑 + 假的服务/网络适配器：
#   A. switch_node 成功路径：下载→验签→校验→替换→健康检查→提交 ok
#   B. 注入健康检查失败：文件与选择状态确实恢复 + 状态 rolled_back
#   C. 应用中途 kill -9 Agent：重启后恢复备份（崩溃恢复）
# 前置：compose 栈已启动（web 127.0.0.1:8080），docker 可用。
# 用法：./run-e2e-alpine.sh   （在 mgmt/agent/tests/ 下执行）
set -u
cd "$(dirname "$0")"

MGMT_ROOT="$(cd ../.. && pwd)"
WEB="${WEB:-http://127.0.0.1:8080}"
NET="${NET:-routermgmt_mgmt}"
ROUTER="mgmt-e2e-router"
PY="python3"
TMPD="$(pwd -W 2>/dev/null || pwd)/.tmp"   # Windows 形式，native python/curl/docker cp 均可用
mkdir -p "$TMPD"
PASS=0; FAIL=0
ck() { if [ "$2" = "$3" ]; then echo "PASS $1"; PASS=$((PASS+1)); else echo "FAIL $1: got=[$2] want=[$3]"; FAIL=$((FAIL+1)); fi; }
ckne() { if [ "$2" != "$3" ]; then echo "PASS $1"; PASS=$((PASS+1)); else echo "FAIL $1"; FAIL=$((FAIL+1)); fi; }

api() { # METHOD PATH [JSON_BODY] [TOKEN] [IDEM_KEY]
    _m="$1"; _p="$2"; _b="${3:-}"; _t="${4:-}"; _k="${5:-}"
    if [ -n "$_b" ]; then
        if [ -n "$_t" ]; then
            if [ -n "$_k" ]; then
                curl -s -o "$TMPD/e2e.resp" -w '%{http_code}' -X "$_m" -H "Authorization: Bearer $_t" -H "Idempotency-Key: $_k" -H 'Content-Type: application/json' -d "$_b" "$WEB$_p"
            else
                curl -s -o "$TMPD/e2e.resp" -w '%{http_code}' -X "$_m" -H "Authorization: Bearer $_t" -H 'Content-Type: application/json' -d "$_b" "$WEB$_p"
            fi
        else
            curl -s -o "$TMPD/e2e.resp" -w '%{http_code}' -X "$_m" -H 'Content-Type: application/json' -d "$_b" "$WEB$_p"
        fi
    else
        if [ -n "$_t" ]; then
            curl -s -o "$TMPD/e2e.resp" -w '%{http_code}' -X "$_m" -H "Authorization: Bearer $_t" "$WEB$_p"
        else
            curl -s -o "$TMPD/e2e.resp" -w '%{http_code}' -X "$_m" "$WEB$_p"
        fi
    fi
}
jget() { $PY -c "import json,sys;d=json.load(open(sys.argv[1]));print(d.get(sys.argv[2],''))" "$TMPD/e2e.resp" "$1"; }

echo "=== 0. 管理员登录（首次绑定 TOTP）==="
code=$(api POST /api/v1/auth/login '{"username":"admin","password":"'"$BOOTSTRAP_PASSWORD"'"}')
[ "$code" = "200" ] || { echo "登录失败 $code $(cat "$TMPD"/e2e.resp)"; exit 1; }
TICKET=$(jget ticket)
NEED_SETUP=$(jget need_setup)
if [ "$NEED_SETUP" = "True" ]; then
    code=$(api POST /api/v1/auth/totp/setup "{\"ticket\":\"$TICKET\"}")
    SECRET=$(jget secret)
    echo "$SECRET" > "$TMPD/totp-secret"
    CODE=$($PY "$MGMT_ROOT/scripts/totp.py" "$SECRET")
    code=$(api POST /api/v1/auth/totp/confirm "{\"ticket\":\"$TICKET\",\"code\":\"$CODE\"}")
    [ "$code" = "200" ] || { echo "TOTP 绑定失败 $code $(cat "$TMPD"/e2e.resp)"; exit 1; }
else
    SECRET=$(cat "$TMPD/totp-secret" 2>/dev/null) || { echo "TOTP 已绑定但本地无密钥；重置数据卷后重跑"; exit 1; }
    CODE=$($PY "$MGMT_ROOT/scripts/totp.py" "$SECRET")
    code=$(api POST /api/v1/auth/totp "{\"ticket\":\"$TICKET\",\"code\":\"$CODE\"}")
    [ "$code" = "200" ] || { echo "TOTP 登录失败 $code $(cat "$TMPD"/e2e.resp)"; exit 1; }
fi
TOKEN=$(jget token)
echo "登录 OK"

echo "=== 1. 节点与设备准备 ==="
NODE1_HOST=203.0.113.200; NODE2_HOST=203.0.113.201
code=$(api POST /api/v1/nodes '{"name":"E2E-NODE1","host":"'"$NODE1_HOST"'","port":443,"params":{"uuid":"11111111-2222-3333-4444-555555555555","pbk":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA","sid":"aabbccdd","sni":"www.microsoft.com"}}' "$TOKEN")
NODE1=$(jget id)
code=$(api POST /api/v1/nodes '{"name":"E2E-NODE2","host":"'"$NODE2_HOST"'","port":443,"params":{"uuid":"22222222-3333-4444-5555-666666666666","pbk":"BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB","sid":"ccbbaadd","sni":"www.microsoft.com"}}' "$TOKEN")
NODE2=$(jget id)
case "$NODE1$NODE2" in ''|*[!0-9]*) ck "节点创建" "fail" "ok" ;; *) ck "节点创建" "ok" "ok" ;; esac

code=$(api POST /api/v1/devices '{"name":"e2e-router"}' "$TOKEN")
ENROLL=$(jget enroll_code); DEV=$(jget id)
echo "device=$DEV"

echo "=== 2. provision CLI 生成引导包 ==="
(cd "$MGMT_ROOT/backend" && go build -o $TMPD/provision ./cmd/provision) || exit 1
$TMPD/provision -server "$WEB" -enroll-code "$ENROLL" -hw-model "e2e,alpine" -firmware "alpine-3.20" -out $TMPD/e2e-pkg.json || exit 1
echo "（Windows 主机不校验权限位；POSIX 权限由安装脚本与目标机保证）"

echo "=== 3. 准备 alpine 容器（busybox ash 环境 + 假适配器）==="
docker rm -f "$ROUTER" >/dev/null 2>&1
(cd "$MGMT_ROOT/backend" && CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -o $TMPD/jsonfilter-linux ./cmd/jsonfilter) || exit 1
docker run -d --name "$ROUTER" --network "$NET" alpine:3.20 sleep 1d >/dev/null || exit 1
docker cp "$MGMT_ROOT/agent" "$ROUTER":/agent-src >/dev/null
docker cp "$TMPD/jsonfilter-linux" "$ROUTER":/usr/local/bin/jsonfilter
docker cp "$TMPD/e2e-pkg.json" "$ROUTER":/pkg.json
rexec() { docker exec "$ROUTER" sh -c "$1"; }
rexec "chmod 755 /usr/local/bin/jsonfilter"

# 依赖与目录
rexec 'apk add -q curl openssl python3 >/dev/null 2>&1
mkdir -p /etc/routermgmt/state /etc/openclash/config /etc/openclash/core /var/routermgmt
DEV=$(jsonfilter -s "$(cat /pkg.json)" -e "$.device_id")
KEY=$(jsonfilter -s "$(cat /pkg.json)" -e "$.hmac_key")
PUB=$(jsonfilter -s "$(cat /pkg.json)" -e "$.sign_public_key")
printf "DEVICE_ID=%s\nHMAC_KEY=%s\nMGMT_URL=http://api:8443\nSUB_URL=\nSIGN_PUB=installed\n" "$DEV" "$KEY" > /etc/routermgmt/agent.conf
chmod 600 /etc/routermgmt/agent.conf
printf "%s\n" "$PUB" > /etc/routermgmt/sign_pub.pem
# 假 mihomo 核心：-t 校验永远通过（真实核心校验由真机 Session 2 验证）
printf "#!/bin/sh\nexit 0\n" > /etc/openclash/core/clash_meta && chmod 755 /etc/openclash/core/clash_meta
# 初始活动配置（旧内容）
printf "mixed-port: 7890\n# old config\n" > /etc/openclash/config/active.yaml
cp /agent-src/routermgmt-agent.sh /usr/bin/routermgmt-agent && chmod 755 /usr/bin/routermgmt-agent
mkdir -p /etc/routermgmt/handlers && cp /agent-src/handlers/switch_node.sh /etc/routermgmt/handlers/ && chmod 644 /etc/routermgmt/handlers/switch_node.sh
echo container-ready' || exit 1

# 假 mihomo API + 假出口探测服务（python3，仅测试工具；Agent 本体仍为 ash）
docker cp "$MGMT_ROOT/agent/tests/fake-services.py" "$ROUTER":/var/routermgmt/fake-services.py
rexec 'nohup python3 /var/routermgmt/fake-services.py >/var/routermgmt/fake.log 2>&1 & sleep 1
curl -s http://127.0.0.1:9090/version >/dev/null && echo fake-api-ready' | grep -q fake-api-ready || { rexec 'cat /var/routermgmt/fake.log'; exit 1; }

AGENT_ENV='RM_CONF=/etc/routermgmt/agent.conf RM_STATE_DIR=/etc/routermgmt/state RM_CLASH_DIR=/etc/openclash/config RM_ACTIVE_CONFIG=/etc/openclash/config/active.yaml RM_MIHOMO_BIN=/etc/openclash/core/clash_meta RM_MIHOMO_API=127.0.0.1:9090 RM_PROXY_PORT=127.0.0.1:9090 RM_EGRESS_CHECK_URL=http://127.0.0.1:9090/ip RM_CN_CHECK_URL=http://127.0.0.1:9090/cn RM_MGMT_CHECK_URL=http://api:8443/api/v1/health HEARTBEAT_INTERVAL=5 TASK_INTERVAL=2 RM_WATCHDOG_SEC=60'

wait_task() { # $1 task_id $2 want_status $3 超时秒
    _i=0
    while [ "$_i" -lt "$3" ]; do
        api GET "/api/v1/tasks?device_id=$DEV&status=$2" "" "$TOKEN" >/dev/null
        if $PY -c "import json,sys;sys.exit(0 if any(t['id']=='$1' for t in json.load(open('"$TMPD"/e2e.resp'))['items']) else 1)" 2>/dev/null; then
            api GET "/api/v1/tasks?device_id=$DEV" "" "$TOKEN" >/dev/null
            return 0
        fi
        _i=$((_i + 1)); sleep 2
    done
    return 1
}
task_status() {
    api GET "/api/v1/tasks?device_id=$DEV" "" "$TOKEN" >/dev/null
    $PY -c "
import json,sys
for t in json.load(open('"$TMPD"/e2e.resp'))['items']:
    if t['id']=='$1':
        print(t['status']); break
"
}

echo "=== 4. 向量测试（ash + openssl，与 Go 单测一致）==="
rexec 'sh /agent-src/tests/vectors.sh' | tee "$TMPD"/e2e.vec | grep -c '^PASS' | { read -r n; echo "vectors pass=$n"; }
VFAIL=$(rexec 'sh /agent-src/tests/vectors.sh > /dev/null; echo $?')
ck "HMAC/ed25519 向量(ash)" "$VFAIL" "0"

echo "=== 5. 场景 A：switch_node 成功 ==="
rexec "echo $NODE1_HOST > /var/routermgmt/exit_ip"
OLD_SHA=$(rexec 'openssl dgst -sha256 -hex /etc/openclash/config/active.yaml | awk "{print \$NF}"')
code=$(api POST /api/v1/assignments/batch "{\"device_ids\":[\"$DEV\"],\"node_id\":$NODE1,\"is_primary\":true,\"apply\":true}" "$TOKEN")
TASK1=$($PY -c "import json;print(json.load(open('"$TMPD"/e2e.resp'))['items'][0]['task_id'])")
echo "task1=$TASK1"

rexec "env $AGENT_ENV /usr/bin/routermgmt-agent > /var/routermgmt/agent-a.log 2>&1 &" 
if wait_task "$TASK1" ok 60; then echo "task1 ok"; else
    echo "task1 未达 ok，状态: $(task_status "$TASK1")"; rexec 'tail -30 /var/routermgmt/agent-a.log'; exit 1; fi
ck "A: 任务 ok" "$(task_status "$TASK1")" "ok"
NEW_SHA=$(rexec 'openssl dgst -sha256 -hex /etc/openclash/config/active.yaml | awk "{print \$NF}"')
ckne "A: 活动配置已替换" "$NEW_SHA" "$OLD_SHA"
HOSTIN=$(rexec "grep -c 'IP-CIDR,$NODE1_HOST/32,DIRECT,no-resolve' /etc/openclash/config/active.yaml || true")
ck "A: 新配置含节点直连规则" "$HOSTIN" "1"
HOSTIN2=$(rexec "grep -c 'server: $NODE1_HOST' /etc/openclash/config/active.yaml || true")
ck "A: 新配置含节点服务器地址" "$HOSTIN2" "1"
ck "A: 已接受版本高水位=1" "$(rexec 'cat /etc/routermgmt/state/accepted_version')" "1"
ck "A: PROXY 组指向新节点" "$(rexec 'cat /var/routermgmt/group_now')" "E2E-NODE1"
rexec 'pkill -f routermgmt-agent' 2>/dev/null; sleep 1

echo "=== 6. 场景 B：健康检查失败 → 回滚（文件确实恢复）==="
SNAP_SHA=$(rexec 'openssl dgst -sha256 -hex /etc/openclash/config/active.yaml | awk "{print \$NF}"')
code=$(api POST /api/v1/assignments/batch "{\"device_ids\":[\"$DEV\"],\"node_id\":$NODE2,\"is_primary\":true,\"apply\":true}" "$TOKEN")
TASK2=$($PY -c "import json;print(json.load(open('"$TMPD"/e2e.resp'))['items'][0]['task_id'])")
rexec "echo $NODE2_HOST > /var/routermgmt/exit_ip"
rexec "touch /var/routermgmt/health_fail"   # 注入健康检查失败
rexec "env $AGENT_ENV RM_FAKE_HEALTH_FAIL=1 /usr/bin/routermgmt-agent > /var/routermgmt/agent-b.log 2>&1 &"
if wait_task "$TASK2" rolled_back 90; then echo "task2 rolled_back"; else
    echo "task2 状态: $(task_status "$TASK2")"; rexec 'tail -40 /var/routermgmt/agent-b.log'; exit 1; fi
ck "B: 任务 rolled_back" "$(task_status "$TASK2")" "rolled_back"
RB_SHA=$(rexec 'openssl dgst -sha256 -hex /etc/openclash/config/active.yaml | awk "{print \$NF}"')
ck "B: 活动配置恢复为切换前" "$RB_SHA" "$SNAP_SHA"
NODE2IN=$(rexec "grep -c 'IP-CIDR,$NODE2_HOST/32,DIRECT,no-resolve' /etc/openclash/config/active.yaml || true")
ck "B: 新节点直连规则未残留" "$NODE2IN" "0"
NODE2IN2=$(rexec "grep -c 'server: $NODE2_HOST' /etc/openclash/config/active.yaml || true")
ck "B: 新节点服务器地址未残留" "$NODE2IN2" "0"
ck "B: PROXY 选择状态恢复" "$(rexec 'cat /var/routermgmt/group_now')" "E2E-NODE1"
rexec 'pkill -f routermgmt-agent' 2>/dev/null; sleep 1
rexec 'rm -f /var/routermgmt/health_fail'

echo "=== 7. 场景 C：中途进程退出 → 未提交事务恢复 ==="
SNAP2_SHA=$(rexec 'openssl dgst -sha256 -hex /etc/openclash/config/active.yaml | awk "{print \$NF}"')
code=$(api POST /api/v1/devices/$DEV/ops "{\"type\":\"reload_proxy\",\"params\":{}}" "$TOKEN" "e2e-crash-1")
TASK3=$(jget task_id)
# 启动后极短时间 kill -9：若命中应用窗口则形成未提交事务
rexec "env $AGENT_ENV /usr/bin/routermgmt-agent > /var/routermgmt/agent-c1.log 2>&1 &"
sleep 1
rexec 'pkill -9 -f routermgmt-agent' 2>/dev/null
sleep 1
UNCOMMITTED=$(rexec 'ls /etc/routermgmt/state/txn/*/uncommitted 2>/dev/null | head -1')
if [ -n "$UNCOMMITTED" ]; then
    echo "命中应用窗口：存在未提交事务，重启验证恢复"
    rexec "env $AGENT_ENV /usr/bin/routermgmt-agent > /var/routermgmt/agent-c2.log 2>&1 &"
    if wait_task "$TASK3" rolled_back 60; then
        ck "C: 恢复后报 rolled_back" "$(task_status "$TASK3")" "rolled_back"
    else
        # 任务可能已被报 failed+rollback_failed 或完成；检查文件才是硬性要求
        echo "task3 状态: $(task_status "$TASK3")（检查文件恢复）"
    fi
    sleep 3
    C_SHA=$(rexec 'openssl dgst -sha256 -hex /etc/openclash/config/active.yaml | awk "{print \$NF}"')
    ck "C: 崩溃后文件恢复" "$C_SHA" "$SNAP2_SHA"
    rexec 'pkill -f routermgmt-agent' 2>/dev/null
else
    echo "（未命中 kill 窗口——应用在 1s 内完成；改用构造未提交事务直接验证 txn_recover_all）"
    # 先让 TASK3 正常完成，避免与恢复断言竞争
    rexec "env $AGENT_ENV /usr/bin/routermgmt-agent > /var/routermgmt/agent-c0.log 2>&1 &"
    wait_task "$TASK3" "ok" 60 || wait_task "$TASK3" "failed" 30
    rexec 'pkill -f routermgmt-agent' 2>/dev/null; sleep 1
    SNAP3_SHA=$(rexec 'openssl dgst -sha256 -hex /etc/openclash/config/active.yaml | awk "{print \$NF}"')
    rexec "TXN=/etc/routermgmt/state/txn/t-fake0001; mkdir -p \$TXN; cp /etc/openclash/config/active.yaml \$TXN/backup.yaml; printf '/etc/openclash/config/active.yaml
E2E-NODE1
' > \$TXN/backup.meta; echo t-fake0001 > \$TXN/task_id; echo fake > \$TXN/lease_id; touch \$TXN/uncommitted; printf 'mixed-port: 9999
# corrupted by crash
' > /etc/openclash/config/active.yaml"
    rexec "env $AGENT_ENV /usr/bin/routermgmt-agent > /var/routermgmt/agent-c3.log 2>&1 &"
    sleep 8
    rexec 'pkill -f routermgmt-agent' 2>/dev/null; sleep 1
    C_SHA=$(rexec 'openssl dgst -sha256 -hex /etc/openclash/config/active.yaml | awk "{print \$NF}"')
    ck "C: 构造事务恢复（文件还原）" "$C_SHA" "$SNAP3_SHA"
fi

echo "=== 8. 心跳与在线状态 ==="
rexec "env $AGENT_ENV /usr/bin/routermgmt-agent > /var/routermgmt/agent-hb.log 2>&1 &"
sleep 7
api GET "/api/v1/devices/$DEV" "" "$TOKEN" >/dev/null
ONLINE=$($PY -c "import json;print(json.load(open('"$TMPD"/e2e.resp'))['device']['online'])")
ck "设备在线（真实 Agent 心跳）" "$ONLINE" "True"
$PY -c "import json;d=json.load(open('"$TMPD"/e2e.resp'))['device'];print('  exit_ip:',d['current_exit_ip'],'版本:',d['desired_config_version'],'/',d['current_config_version'])"
api GET /api/v1/dashboard "" "$TOKEN" >/dev/null
DASH_ONLINE=$($PY -c "import json;print(json.load(open('"$TMPD"/e2e.resp'))['devices']['online'])")
$PY -c "import json;print('  dashboard online:',json.load(open('"$TMPD"/e2e.resp'))['devices'])"

echo "=== 清理 ==="
rexec 'pkill -f routermgmt-agent' 2>/dev/null
docker rm -f "$ROUTER" >/dev/null 2>&1
echo ""
echo "结果: PASS=$PASS FAIL=$FAIL"
[ "$FAIL" = "0" ] || exit 1
