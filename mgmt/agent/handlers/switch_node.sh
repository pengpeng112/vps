#!/bin/sh
# handlers/switch_node.sh — 配置应用事务（§7.5，被 routermgmt-agent.sh source）
# 实现：下载→验签→校验→备份→看门狗→原子替换→强制 PROXY 指向目标→健康检查→提交/回滚
# 适配层全部可通过 RM_* 环境变量覆盖，供 alpine 容器用假服务/网络适配器测试。
#
# 这里同时实现 switch_node 与 reload_proxy（同一事务管线，后者不换节点）。

# ---- OpenClash 适配层 ----
oc_config_dir() {
    # 返回 mihomo -d 目录：必须与运行核心一致（含 Geo 数据文件），否则 GEOSITE/GEOIP 规则校验
    # 会因缺数据尝试在线下载而失败。运行核心为 /usr/bin/mihomo -d /etc/openclash。
    if [ -n "${RM_CLASH_DIR:-}" ]; then
        [ -d "$RM_CLASH_DIR" ] && { echo "$RM_CLASH_DIR"; return 0; }
        return 1
    fi
    for d in /etc/openclash /etc/mihomo /etc/openclash/config /etc/clash; do
        [ -d "$d" ] || continue
        for g in GeoSite.dat geosite.dat geoip.metadb Country.mmdb; do
            [ -f "$d/$g" ] && { echo "$d"; return 0; }
        done
    done
    for d in /etc/openclash /etc/mihomo /etc/openclash/config /etc/clash; do
        [ -d "$d" ] && { echo "$d"; return 0; }
    done
    return 1
}

oc_active_config() {
    if [ -n "${RM_ACTIVE_CONFIG:-}" ]; then echo "$RM_ACTIVE_CONFIG"; return 0; fi
    _dir=$(oc_config_dir) || return 1
    if command -v uci >/dev/null 2>&1; then
        _p=$(uci -q get openclash.config.config_path 2>/dev/null)
        [ -n "$_p" ] && [ -f "$_p" ] && { echo "$_p"; return 0; }
    fi
    _p=$(ls -t "$_dir"/*.yaml "$_dir"/*.yml 2>/dev/null | head -n1)
    [ -n "$_p" ] && { echo "$_p"; return 0; }
    return 1
}

mihomo_bin() {
    if [ -n "${RM_MIHOMO_BIN:-}" ] && [ -x "$RM_MIHOMO_BIN" ]; then echo "$RM_MIHOMO_BIN"; return 0; fi
    for b in /etc/openclash/core/clash_meta /etc/openclash/core/clash_tun; do
        [ -x "$b" ] && { echo "$b"; return 0; }
    done
    command -v clash 2>/dev/null && return 0
    command -v mihomo 2>/dev/null && return 0
    return 1
}

mihomo_validate() { # $1 配置文件；核心路径与命令在目标版本验证，不得猜测成功（§7.5）
    _bin=$(mihomo_bin) || return 1
    _dir=$(oc_config_dir 2>/dev/null || echo /tmp)
    # 诊断留痕：bin/dir/完整 stderr 落盘，失败时可远程读取定位（成功时清掉）
    "$_bin" -t -d "$_dir" -f "$1" >"$STATE_DIR/validate.log" 2>&1
    _rc=$?
    [ "$_rc" = "0" ] && rm -f "$STATE_DIR/validate.log"
    return $_rc
}

reload_effective() { # $1=待生效 yaml：其中声明的 proxies/groups 必须已存在于运行时
    # 热加载可能被核心的 home 路径限制静默拒绝（PUT 返回 400：-d /etc/openclash 时
    # 配置在 /etc/mihomo 会被拒），或 204 后异步重载失败保留旧配置——不能只信
    # HTTP 返回码，必须核对运行时组名单（2026-09-25 雅典娜"文件换了运行时没换"实锤）。
    _names=$(grep -E '^[[:space:]]*- name: "' "$1" 2>/dev/null | sed 's/.*"\(.*\)".*/\1/')
    [ -n "$_names" ] || return 1
    for _g in $_names; do
        curl $CURL_OPTS -s -m 3 "http://$MIHOMO_API/proxies/$_g" 2>/dev/null | grep -q '"name"' || return 1
    done
    return 0
}

oc_reload() {
    # 首选 external-controller 热加载，失败回退 init.d 重启
    _cfg="$1"
    if [ -n "${RM_FAKE_RELOAD_FAIL:-}" ]; then return 1; fi
    _code=$(curl $CURL_OPTS -o /dev/null -w '%{http_code}' -X PUT \
        -H 'Content-Type: application/json' \
        --data-binary "{\"path\":\"$_cfg\"}" \
        "http://$MIHOMO_API/configs?force=true" 2>/dev/null)
    if [ "$_code" = "204" ] || [ "$_code" = "200" ]; then
        wait_api_ready || true
        reload_effective "$_cfg" && return 0
    fi
    # 热加载不可用/未生效 → 完整重启核心。直跑脚本优先：openclash 包装层在自身
    # enable=0 时 restart 返回成功但不重启核心（雅典娜实锤），必须放后面并逐个核验
    for _init in "${RM_OPENCLASH_INIT:-}" /etc/init.d/mihomo /etc/init.d/openclash; do
        [ -n "$_init" ] || continue
        [ -x "$_init" ] || continue
        "$_init" restart >/dev/null 2>&1 || continue
        wait_api_ready || continue
        reload_effective "$_cfg" && return 0
    done
    return 1
}

oc_get_group() { # $1 组名 → 当前选择
    curl $CURL_OPTS "http://$MIHOMO_API/proxies/$1" 2>/dev/null | jsonfilter -e '$.now' 2>/dev/null
}

oc_set_group() { # $1 组名 $2 目标
    curl $CURL_OPTS -o /dev/null -X PUT -H 'Content-Type: application/json' \
        --data-binary "{\"name\":\"$2\"}" \
        "http://$MIHOMO_API/proxies/$1" 2>/dev/null
}

wait_api_ready() { # 最多等 30s
    _i=0
    while [ "$_i" -lt 30 ]; do
        curl $CURL_OPTS -o /dev/null "http://$MIHOMO_API/version" 2>/dev/null && return 0
        _i=$((_i + 1)); sleep 1
    done
    return 1
}

probe_egress() { # 经代理端口探测出口 IP（探测地址可配置）
    if [ -n "${RM_FAKE_HEALTH_FAIL:-}" ]; then return 1; fi
    _t=$(curl $CURL_OPTS -x "http://$PROXY_PORT" "$EGRESS_CHECK_URL" 2>/dev/null) || return 1
    # ip= 行优先（cloudflare trace）；否则按纯文本回显（本地 fake 探测服务）
    _ip=$(printf '%s\n' "$_t" | awk -F= '/^ip=/{print $2}' | head -n1)
    if [ -z "$_ip" ]; then
        _lines=$(printf '%s\n' "$_t" | grep -c .)
        [ "$_lines" = "1" ] && _ip="$_t"
    fi
    [ -n "$_ip" ] || return 1
    echo "$_ip"
}

probe_cn_direct() { # 国内直连（不经代理端口）
    # 真机注意：透明重定向只覆盖 LAN 客户端（prerouting br-lan），本机流量不进核心，
    # 而本机 DNS 会拿到 fake-ip（198.18.x.x 无路由）→ 本机 curl 国内站必失败。
    # 改用「向国内公共 DNS 解析」验证国内直连链路，公共 DNS IP 可用 RM_CN_DNS 覆盖。
    if [ -n "${RM_FAKE_HEALTH_FAIL:-}" ]; then return 1; fi
    nslookup "${CN_CHECK_HOST:-www.baidu.com}" "${RM_CN_DNS:-223.5.5.5}" >/dev/null 2>&1 && return 0
    curl $CURL_OPTS -o /dev/null "$CN_CHECK_URL" 2>/dev/null
}

probe_mgmt() { # 管理 HTTPS 连通（WG 面）
    _url="${RM_MGMT_CHECK_URL:-$MGMT_URL/api/v1/health}"
    curl $CURL_OPTS -o /dev/null "$_url" 2>/dev/null
}

# ---- 事务 ----
txn_dir_for() { echo "$STATE_DIR/txn/$1"; }

verify_config_signature() { # $1 yaml 文件 $2 版本 $3 签名 hex
    _yaml="$1"; _ver="$2"; _sig="$3"
    [ -s "${SIGN_PUB_FILE:-/etc/routermgmt/sign_pub.pem}" ] || return 1
    _hash=$(openssl dgst -sha256 -hex "$_yaml" | awk '{print $NF}')
    # 签名原文：字段间 LF、末尾无 LF（§7.5）
    printf '%s\n%s\n%s' "$_hash" "$DEVICE_ID" "$_ver" > "$STATE_DIR/sigpayload.$$"
    # hex→二进制：busybox awk 的 printf "%c",0 会丢弃 0x00 字节——Ed25519 签名约 1/4 概率含零字节，
    # 曾导致验签随机失败（2026-09-25 实锤：同管线 v2/v4 成功、v3 失败）。
    # 改用 ash printf \xHH 逐对生成（实测含 NUL 二进制安全，写文件重定向可靠）。
    _sighex=$(printf '%s' "$_sig" | tr 'A-F' 'a-f')
    : > "$STATE_DIR/sig.bin.$$"
    while [ -n "$_sighex" ]; do
        printf "\\x${_sighex%"${_sighex#??}"}" >> "$STATE_DIR/sig.bin.$$"
        _sighex="${_sighex#??}"
    done
    # 旧固件（如 SN-R1 的 OpenWrt 21.02）openssl 1.1.1 无 -rawin；部署了 openssl3 则优先用
    _ossl="${VERIFY_OPENSSL:-openssl}"
    [ -x /usr/bin/openssl-mgmt ] && { _ossl=/usr/bin/openssl-mgmt; export LD_LIBRARY_PATH="/usr/lib/mgmt-ssl:${LD_LIBRARY_PATH:-}"; }
    "$_ossl" pkeyutl -verify -pubin -inkey "${SIGN_PUB_FILE:-/etc/routermgmt/sign_pub.pem}" \
        -rawin -in "$STATE_DIR/sigpayload.$$" -sigfile "$STATE_DIR/sig.bin.$$" >/dev/null 2>&1
    _rc=$?
    unset LD_LIBRARY_PATH
    rm -f "$STATE_DIR/sigpayload.$$" "$STATE_DIR/sig.bin.$$"
    return $_rc
}

accepted_version_read() {
    [ -f "$STATE_DIR/accepted_version" ] && cat "$STATE_DIR/accepted_version" 2>/dev/null || echo 0
}

txn_rollback() { # $1 事务目录 → 恢复备份与选择状态；返回 0=恢复成功
    _d="$1"
    _active=$(sed -n 1p "$_d/backup.meta")
    _oldsel=$(sed -n 2p "$_d/backup.meta")
    [ -s "$_d/backup.yaml" ] && [ -n "$_active" ] || return 1
    cp "$_d/backup.yaml" "$_active" || return 1
    oc_reload "$_active" || return 1
    wait_api_ready || true
    [ -n "$_oldsel" ] && [ "$_oldsel" != "null" ] && oc_set_group PROXY "$_oldsel"
    # 恢复校验：配置文件哈希与备份一致
    _h1=$(openssl dgst -sha256 -hex "$_active" | awk '{print $NF}')
    _h2=$(openssl dgst -sha256 -hex "$_d/backup.yaml" | awk '{print $NF}')
    [ "$_h1" = "$_h2" ] || return 1
    return 0
}

txn_recover_all() {
    # 启动恢复：发现未提交事务 → 恢复备份并补报 rolled_back（§7.5 第 7 步）
    [ -d "$STATE_DIR/txn" ] || return 0
    for _d in "$STATE_DIR/txn"/*; do
        [ -d "$_d" ] || continue
        [ -f "$_d/uncommitted" ] || { rm -rf "$_d"; continue; }
        _tid=$(cat "$_d/task_id" 2>/dev/null)
        _lease=$(cat "$_d/lease_id" 2>/dev/null)
        log "recover: 发现未提交事务 $_tid，执行回滚"
        if txn_rollback "$_d"; then
            [ -n "$_tid" ] && persist_result "$_tid" "${_lease:-}" rolled_back '{"error":"agent restarted during apply, restored backup"}'
            echo "rolled_back" > "$_d/final"
        else
            [ -n "$_tid" ] && persist_result "$_tid" "${_lease:-}" failed '{"error":"agent restarted during apply","rollback_failed":true}'
            echo "rollback_failed" > "$_d/final"
        fi
        rm -f "$_d/uncommitted"
    done
    return 0
}

task_apply_config() { # $1 任务 JSON（switch_node / reload_proxy）
    _tj="$1"
    _tid=$(json_str "$_tj" '$.task_id')
    _type=$(json_str "$_tj" '$.type')
    _lease=$(json_str "$_tj" '$.lease_id')
    _ver=$(json_str "$_tj" '$.config_version')
    _sha=$(json_str "$_tj" '$.config_sha256')
    _sig=$(json_str "$_tj" '$.signature')
    _curl_url=$(json_str "$_tj" '$.config_url')
    _node=$(json_str "$_tj" '$.params.node_name')
    _expect=$(json_str "$_tj" '$.params.expect_exit_ip')
    _deadline=$(json_str "$_tj" '$.deadline')

    _fail() { persist_result "$_tid" "$_lease" failed "{\"error\":\"$1\"}"; mv -f "$(txn_dir_for "$_tid")" "$STATE_DIR/txn-debug" 2>/dev/null; return 0; }

    # 0. 幂等：同任务已有最终结果只重传（§7.5）
    if [ -f "$STATE_DIR/results/$_tid.json" ]; then
        log "task $_tid 已有结果，仅重传"
        return 0
    fi

    _dir=$(oc_config_dir) || { _fail "未找到 OpenClash 配置目录（尝试过 /etc/openclash/config /etc/clash；RM_CLASH_DIR 可覆盖）"; return 0; }
    _active=$(oc_active_config) || { _fail "未找到活动配置文件"; return 0; }

    # 防重放高水位：版本必须高于已接受版本（本地恢复允许回旧活动版本但保留最高已接受，§7.5）
    _acc=$(accepted_version_read)
    case "$_acc" in ''|*[!0-9]*) _acc=0 ;; esac
    if [ -n "$_ver" ] && [ "$_ver" -le "$_acc" ] 2>/dev/null; then
        persist_result "$_tid" "$_lease" ok "{\"skipped\":true,\"accepted_version\":$_acc}"
        return 0
    fi

    _txn=$(txn_dir_for "$_tid")
    rm -rf "$_txn"; mkdir -p "$_txn"
    echo "$_tid" > "$_txn/task_id"; echo "$_lease" > "$_txn/lease_id"

    # 1. 下载指定不可变版本（同文件系统临时文件 600）
    _rpath=$(printf '%s' "$_curl_url" | sed 's|^[a-zA-Z][a-zA-Z0-9+.-]*://[^/]*||')
    case "$_rpath" in /api/v1/agent/configs/*) ;; *) { _fail "非法配置下载路径"; return 0; } ;; esac
    : > "$_txn/new.yaml"
    chmod 600 "$_txn/new.yaml"
    _code=$(api_call GET "$_rpath" /dev/null "$_txn/new.yaml")
    [ "$_code" = "200" ] || { _fail "配置下载失败 http=$_code"; return 0; }
    [ -s "$_txn/new.yaml" ] || { _fail "配置内容为空"; return 0; }

    # 设备/版本/哈希/签名校验
    _gotsha=$(openssl dgst -sha256 -hex "$_txn/new.yaml" | awk '{print $NF}')
    { [ -z "$_sha" ] || [ "$_gotsha" = "$_sha" ]; } || { _fail "配置哈希不匹配"; return 0; }
    [ -n "$_ver" ] || { _fail "缺少 config_version"; return 0; }
    verify_config_signature "$_txn/new.yaml" "$_ver" "$_sig" || { _fail "配置签名验证失败（拒绝应用）"; return 0; }

    # 2. 核心校验
    mihomo_validate "$_txn/new.yaml" || { _fail "mihomo 配置校验失败"; return 0; }

    # 3. 备份当前配置 + 活动路径 + PROXY 选择状态；落盘事务并启动看门狗
    cp "$_active" "$_txn/backup.yaml"
    _oldsel=$(oc_get_group PROXY)
    printf '%s\n%s\n' "$_active" "$_oldsel" > "$_txn/backup.meta"
    echo "backup_done" > "$_txn/phase"
    touch "$_txn/uncommitted"
    (
        _wd=${RM_WATCHDOG_SEC:-100}
        sleep "$_wd"
        if [ -f "$_txn/uncommitted" ]; then
            log "watchdog: 事务 $_tid 超时未提交，回滚"
            txn_rollback "$_txn" && echo "rolled_back" > "$_txn/final" || echo "rollback_failed" > "$_txn/final"
            rm -f "$_txn/uncommitted"
        fi
    ) &
    echo $! > "$_txn/watchdog.pid"

    # 4. 原子替换 + 加载 + 强制 PROXY 指向目标节点（避免 AUTO/持久化旧选择）
    if ! cp "$_txn/new.yaml" "${_active}.routermgmt.tmp"; then
        kill "$(cat "$_txn/watchdog.pid" 2>/dev/null)" 2>/dev/null
        _fail "写临时配置失败"
        return 0
    fi
    chmod 644 "${_active}.routermgmt.tmp"
    if ! mv "${_active}.routermgmt.tmp" "$_active"; then
        kill "$(cat "$_txn/watchdog.pid" 2>/dev/null)" 2>/dev/null
        _fail "原子替换失败"
        return 0
    fi
    echo "replaced" > "$_txn/phase"
    oc_reload "$_active" || {
        _rb=1
    }
    wait_api_ready || { _rb=1; }
    sleep 3  # DNS/fake-ip 缓存重载后的收敛等待，避免探针误判
    if [ "${_rb:-0}" = "1" ]; then
        # 应用失败 → 回滚
        kill "$(cat "$_txn/watchdog.pid" 2>/dev/null)" 2>/dev/null
        if txn_rollback "$_txn"; then
            rm -f "$_txn/uncommitted"; echo "rolled_back" > "$_txn/final"
            persist_result "$_tid" "$_lease" rolled_back '{"error":"reload failed, restored backup"}'
        else
            rm -f "$_txn/uncommitted"; echo "rollback_failed" > "$_txn/final"
            persist_result "$_tid" "$_lease" failed '{"error":"reload failed","rollback_failed":true}'
        fi
        return 0
    fi
    if [ "$_type" = "switch_node" ] && [ -n "$_node" ]; then
        oc_set_group PROXY "$_node"
    fi

    # 5. 健康检查：出口（固定 IP 节点两次一致）+ 国内直连 + 管理面连通
    _healthy=1; _egress_ip=""
    if [ -n "$_expect" ]; then
        _p1=$(probe_egress) || _healthy=0
        _p2="" ; [ "$_healthy" = "1" ] && { _p2=$(probe_egress) || _healthy=0; }
        [ "$_healthy" = "1" ] && [ "$_p1" = "$_p2" ] && [ "$_p1" = "$_expect" ] || _healthy=0
        _egress_ip=$_p1
    else
        _egress_ip=$(probe_egress) || _healthy=0
    fi
    [ "$_healthy" = "1" ] && { probe_cn_direct || { sleep 3; probe_cn_direct || { _healthy=0; echo "cn_direct failed x2 $(date +%T)" > "$STATE_DIR/health.log"; }; }; }
    [ "$_healthy" = "1" ] && { probe_mgmt || { _healthy=0; echo "mgmt failed $(date +%T)" > "$STATE_DIR/health.log"; }; }

    if [ "$_healthy" != "1" ]; then
        # 7. 健康检查失败 → 回滚（先备份/选择状态恢复，验证成功才报 rolled_back）
        kill "$(cat "$_txn/watchdog.pid" 2>/dev/null)" 2>/dev/null
        if txn_rollback "$_txn"; then
            rm -f "$_txn/uncommitted"; echo "rolled_back" > "$_txn/final"
            persist_result "$_tid" "$_lease" rolled_back "{\"error\":\"health check failed\",\"exit_ip\":\"$_egress_ip\"}"
        else
            rm -f "$_txn/uncommitted"; echo "rollback_failed" > "$_txn/final"
            persist_result "$_tid" "$_lease" failed '{"error":"health check failed","rollback_failed":true}'
        fi
        return 0
    fi

    # 6. 提交：取消看门狗、更新最高已接受版本、持久化结果、回报 ok
    kill "$(cat "$_txn/watchdog.pid" 2>/dev/null)" 2>/dev/null
    rm -f "$_txn/uncommitted"
    echo "committed" > "$_txn/final"
    if [ -n "$_ver" ] && [ "$_ver" -gt "$_acc" ]; then
        echo "$_ver" > "$STATE_DIR/accepted_version"
    fi
	persist_result "$_tid" "$_lease" ok "{\"exit_ip\":\"$_egress_ip\",\"version\":$_ver}"
	log "task $_tid committed version=$_ver exit=$_egress_ip"
	reapply_runtime_modes
	return 0
}

# set_proxy_state：远程停用/启用 VPS 代理（不动配置文件，只切 PROXY 组选择）。
# 停用=PROXY→DIRECT（全部流量直连，VPS 闲置）；启用=PROXY→AUTO（恢复 url-test 自动选节点）。
# 选择经 profile.store-selected 持久化，核心重启后状态保持。
task_set_proxy_state() {
	_tj="$1"
	_tid=$(json_str "$_tj" '$.task_id'); _lease=$(json_str "$_tj" '$.lease_id')
	_en=$(json_str "$_tj" '$.params.enabled')
	case "$_en" in
		true|false) ;;
		*) persist_result "$_tid" "$_lease" failed '{"error":"invalid params.enabled"}'; return ;;
	esac
	_target="DIRECT"; [ "$_en" = "true" ] && _target="AUTO"
	oc_set_group PROXY "$_target"
	sleep 2
	_now=$(oc_get_group PROXY)
	if [ "$_now" = "$_target" ]; then
		persist_result "$_tid" "$_lease" ok "{\"group_now\":\"$_now\",\"enabled\":$_en}"
		log "task $_tid proxy enabled=$_en group=$_now"
	else
		persist_result "$_tid" "$_lease" failed "{\"error\":\"group not applied\",\"group_now\":\"$_now\",\"want\":\"$_target\"}"
		log "task $_tid FAILED group_now=$_now want=$_target"
	fi
	return 0
}

# agent_update：远程升级 agent 自身（2026-09-18）。
# 流程：HMAC 下载签名包 → Ed25519 验签（与配置验签同构）→ sh -n 语法门禁 →
#       备份当前文件 → 原子替换 → 上报 ok → 延迟重启服务（新进程由 procd 拉起）。
# 回滚：tar 还原 agent-bak-*.tar.gz 到 /etc/routermgmt 后 /etc/init.d/routermgmt restart。
task_agent_update() {
    _tj="$1"
    _tid=$(json_str "$_tj" '$.task_id'); _lease=$(json_str "$_tj" '$.lease_id')
    _ver=$(json_str "$_tj" '$.params.version')
    _af_fail() { persist_result "$_tid" "$_lease" failed "{\"error\":\"$1\",\"version\":\"$_ver\"}"; log "task $_tid agent_update FAILED: $1"; return 0; }
    case "$_ver" in ''|*[!0-9]*) _af_fail "invalid version" ; return ;; esac

    _bundle="$STATE_DIR/agent-$_ver.tar.gz"
    _hdr="$STATE_DIR/bundle-$_ver.hdr"
    rm -f "$_bundle" "$_hdr"
    RM_HDR_FILE="$_hdr" api_call GET "/api/v1/agent/bundles/$_ver" /dev/null "$_bundle" > "$STATE_DIR/bundle.code"
    _code=$(cat "$STATE_DIR/bundle.code" 2>/dev/null)
    [ "$_code" = "200" ] || { _af_fail "download http=$_code" ; return; }
    [ -s "$_bundle" ] || { _af_fail "empty bundle" ; return; }
    _sig=$(grep -i '^X-Signature:' "$_hdr" 2>/dev/null | tail -n1 | tr -d '\r' | awk '{print $2}')
    case "$_sig" in ''|*[!0-9a-fA-F]*) _af_fail "no signature header" ; return ;; esac

    verify_config_signature "$_bundle" "$_ver" "$_sig" || { _af_fail "signature verify failed" ; return; }

    _stage="/tmp/agentupd.$$"
    rm -rf "$_stage"; mkdir -p "$_stage"
    tar -xzf "$_bundle" -C "$_stage" 2>/dev/null || { rm -rf "$_stage"; _af_fail "extract failed" ; return; }
    _na="$_stage/routermgmt-agent.sh"; _nh="$_stage/handlers/switch_node.sh"; _ni="$_stage/init/routermgmt"
    { [ -f "$_na" ] && [ -f "$_nh" ]; } || { rm -rf "$_stage"; _af_fail "bundle layout unexpected" ; return; }
    sh -n "$_na" 2>/dev/null || { rm -rf "$_stage"; _af_fail "agent.sh syntax check failed" ; return; }
    sh -n "$_nh" 2>/dev/null || { rm -rf "$_stage"; _af_fail "handlers syntax check failed" ; return; }
    [ ! -f "$_ni" ] || sh -n "$_ni" 2>/dev/null || { rm -rf "$_stage"; _af_fail "init syntax check failed" ; return; }
    [ ! -f "$_stage/access-fw.sh" ] || sh -n "$_stage/access-fw.sh" 2>/dev/null || { rm -rf "$_stage"; _af_fail "access-fw syntax check failed" ; return; }

    _bak="/etc/routermgmt/agent-bak-$(date +%Y%m%d%H%M%S).tar.gz"
    tar -czf "$_bak" -C /etc/routermgmt routermgmt-agent.sh handlers access-fw.sh 2>/dev/null
    cp "$_na" /etc/routermgmt/routermgmt-agent.sh.new
    cp "$_nh" /etc/routermgmt/handlers/switch_node.sh.new
    chmod 700 /etc/routermgmt/routermgmt-agent.sh.new
    chmod 600 /etc/routermgmt/handlers/switch_node.sh.new
    sync
    mv -f /etc/routermgmt/routermgmt-agent.sh.new /etc/routermgmt/routermgmt-agent.sh
    mv -f /etc/routermgmt/handlers/switch_node.sh.new /etc/routermgmt/handlers/switch_node.sh
    cp /etc/routermgmt/routermgmt-agent.sh /usr/bin/routermgmt-agent && chmod 755 /usr/bin/routermgmt-agent
    if [ -f "$_stage/access-fw.sh" ]; then
        cp "$_stage/access-fw.sh" /etc/routermgmt/access-fw.sh && chmod 755 /etc/routermgmt/access-fw.sh
    fi
    if [ -f "$_ni" ]; then
        cp "$_ni" /etc/init.d/routermgmt.new && chmod 755 /etc/init.d/routermgmt.new             && mv -f /etc/init.d/routermgmt.new /etc/init.d/routermgmt
        /etc/init.d/routermgmt enable >/dev/null 2>&1
    fi
    rm -rf "$_stage"

    persist_result "$_tid" "$_lease" ok "{\"version\":\"$_ver\",\"backup\":\"$(basename "$_bak")\"}"
    log "task $_tid agent_update ok ver=$_ver backup=$(basename "$_bak"), restarting in 3s"
    flush_pending_results
    ( sleep 3; /etc/init.d/routermgmt restart ) >/dev/null 2>&1 &
    return 0
}

# repair_mgmt_tunnel：VPS 侧 mgmt-rssh-probe 探测到 -R 排障口丢失时下发的白名单修复任务
# （2026-09-24 阶段1C）。只允许重启本机 mgmt-tunnel 服务；不接受任何参数命令。
# 立即重启会切断本任务结果上报所走的 -L 心跳通道，故延迟 2s 后台执行。
task_repair_mgmt_tunnel() {
    _tj="$1"
    _tid=$(json_str "$_tj" '$.task_id'); _lease=$(json_str "$_tj" '$.lease_id')
    if [ ! -x /etc/init.d/mgmt-tunnel ]; then
        persist_result "$_tid" "$_lease" failed '{"error":"mgmt-tunnel not installed"}'
        return 0
    fi
    _before=$(pgrep -f 'id_dropbear_mgmt' 2>/dev/null | wc -l)
    ( sleep 2; /etc/init.d/mgmt-tunnel restart ) >/dev/null 2>&1 &
    persist_result "$_tid" "$_lease" ok "{\"restarted\":true,\"instances_before\":$_before}"
    log "task $_tid repair_mgmt_tunnel: restart dispatched (instances_before=$_before)"
    return 0
}

# ---- 策略落地：VPS 限速 / 定时重启 / 授权关停（国内直连不限）----
# 只处理 IPv4 点分地址；限速打在本机 input/output（mihomo 本地出站），不碰 LAN→CN。

_valid_v4() {
    case "$1" in
        *[!0-9.]*|'') return 1 ;;
    esac
    echo "$1" | awk -F. 'NF==4 && $1>=0 && $1<=255 && $2>=0 && $2<=255 && $3>=0 && $3<=255 && $4>=0 && $4<=255 {ok=1} END{exit ok?0:1}'
}

_valid_reboot_cron() {
    case "$1" in
        ''|'0 0 * * *'|'0 8 * * *'|'0 */8 * * *'|'0 */12 * * *') return 0 ;;
        *) return 1 ;;
    esac
}

apply_vps_qos_ipt() { # 无 nft 的设备（SN-R1）：只限制本机访问 VPS:443
    _act="$1"; _kbps="$2"; _host="$3"
    iptables -D OUTPUT -j VPS_QOS 2>/dev/null
    iptables -D INPUT -j VPS_QOS_IN 2>/dev/null
    iptables -F VPS_QOS 2>/dev/null
    iptables -X VPS_QOS 2>/dev/null
    iptables -F VPS_QOS_IN 2>/dev/null
    iptables -X VPS_QOS_IN 2>/dev/null
    _valid_v4 "$_host" || return 0
    iptables -N VPS_QOS
    iptables -N VPS_QOS_IN
    iptables -I OUTPUT 1 -j VPS_QOS
    iptables -I INPUT 1 -j VPS_QOS_IN
    if [ "$_act" = "false" ]; then
        iptables -A VPS_QOS -d "$_host" -p tcp --dport 443 -j DROP
        iptables -A VPS_QOS_IN -s "$_host" -p tcp --sport 443 -j DROP
        return 0
    fi
    case "$_kbps" in ''|0|*[!0-9]*) return 0 ;; esac
    _kbytes=$(( _kbps / 8 ))
    [ "$_kbytes" -lt 1 ] && _kbytes=1
    iptables -A VPS_QOS -d "$_host" -p tcp --dport 443 -m hashlimit \
        --hashlimit-name vpsup --hashlimit-mode dstip \
        --hashlimit-above ${_kbytes}kb/s --hashlimit-burst ${_kbytes}kb -j DROP
    iptables -A VPS_QOS_IN -s "$_host" -p tcp --sport 443 -m hashlimit \
        --hashlimit-name vpsdown --hashlimit-mode srcip \
        --hashlimit-above ${_kbytes}kb/s --hashlimit-burst ${_kbytes}kb -j DROP
}

apply_vps_qos() { # $1=service_active(true/false) $2=kbps $3=vps_host
    _act="$1"; _kbps="$2"; _host="$3"
    if ! command -v nft >/dev/null 2>&1; then
        apply_vps_qos_ipt "$_act" "$_kbps" "$_host"
        return $?
    fi
    nft delete table inet vps_qos 2>/dev/null || true
    _valid_v4 "$_host" || return 0
    if [ "$_act" = "false" ]; then
        nft -f - <<EOF
table inet vps_qos {
  chain output {
    type filter hook output priority 0; policy accept;
    ip daddr $_host tcp dport 443 counter drop
  }
  chain input {
    type filter hook input priority 0; policy accept;
    ip saddr $_host tcp sport 443 counter drop
  }
}
EOF
        return 0
    fi
    case "$_kbps" in ''|0|*[!0-9]*) return 0 ;; esac
    [ "$_kbps" -gt 0 ] 2>/dev/null || return 0
    _kbytes=$(( _kbps / 8 ))
    [ "$_kbytes" -lt 1 ] && _kbytes=1
    nft -f - <<EOF
table inet vps_qos {
  chain output {
    type filter hook output priority 0; policy accept;
    ip daddr $_host tcp dport 443 limit rate over ${_kbytes} kbytes/second counter drop
  }
  chain input {
    type filter hook input priority 0; policy accept;
    ip saddr $_host tcp sport 443 limit rate over ${_kbytes} kbytes/second counter drop
  }
}
EOF
}

apply_reboot_cron() { # $1=crontab 五行或空
    _cron="$1"
    _valid_reboot_cron "$_cron" || return 1
    _cf=/etc/crontabs/root
    mkdir -p /etc/crontabs
    touch "$_cf"
    if [ -f "$_cf" ]; then
        grep -v '#routermgmt-reboot' "$_cf" > "$_cf.tmp" 2>/dev/null || : > "$_cf.tmp"
        mv -f "$_cf.tmp" "$_cf"
    fi
    [ -n "$_cron" ] && echo "$_cron /sbin/reboot #routermgmt-reboot" >> "$_cf"
    /etc/init.d/cron restart >/dev/null 2>&1 || true
    return 0
}

apply_proxy_mode() { # $1=rule|global|direct  $2=service_active
    _m="$1"; _sa="$2"
    [ "$_sa" = "false" ] && _m=direct
    case "$_m" in rule|global|direct) ;; *) _m=rule ;; esac
    printf '%s\n' "$_m" > "$STATE_DIR/proxy.mode"
    curl $CURL_OPTS -o /dev/null -X PATCH -H 'Content-Type: application/json' \
        --data-binary "{\"mode\":\"$_m\"}" "http://$MIHOMO_API/configs" 2>/dev/null || true
    for _f in /etc/mihomo/run.yaml /etc/mihomo/config.yaml; do
        [ -f "$_f" ] && sed -i "s/^mode: .*/mode: $_m/" "$_f" 2>/dev/null || true
    done
}

apply_access_ctl() { # $1=mode $2=list
    _am="$1"; _al="$2"
    case "$_am" in all|whitelist|blacklist) ;; *) _am=all ;; esac
    printf '%s\n' "$_am" > "$STATE_DIR/access.mode"
    printf '%s\n' "$_al" > "$STATE_DIR/access.list"
    if [ -x /etc/routermgmt/access-fw.sh ]; then
        /etc/routermgmt/access-fw.sh
    fi
}

reapply_runtime_modes() {
    # 配置热替换/防火墙重装后把运行模式和访问控制再打一遍
    _sa=true
    [ -f "$STATE_DIR/license_forced_direct" ] && _sa=false
    [ -f "$STATE_DIR/proxy.mode" ] && apply_proxy_mode "$(cat "$STATE_DIR/proxy.mode")" "$_sa"
    [ -x /etc/routermgmt/access-fw.sh ] && /etc/routermgmt/access-fw.sh
}

apply_policy_fields() { # $1=active $2=kbps $3=cron $4=host $5=proxy_mode $6=access_mode $7=access_list
    _act="$1"; _kbps="$2"; _cron="$3"; _host="$4"
    _pmode="${5:-rule}"; _amode="${6:-all}"; _alist="${7:-}"
    [ -n "$_act" ] || return 0
    case "$_act" in true|false) ;; *) return 0 ;; esac
    case "$_kbps" in ''|*[!0-9]*) _kbps=0 ;; esac
    case "$_pmode" in rule|global|direct) ;; *) _pmode=rule ;; esac
    case "$_amode" in all|whitelist|blacklist) ;; *) _amode=all ;; esac
    _fp="$_act|$_kbps|$_cron|$_host|$_pmode|$_amode|$_alist"
    _old=$(cat "$STATE_DIR/policy.fp" 2>/dev/null || true)
    if [ "$_fp" = "$_old" ]; then
        return 0
    fi
    apply_vps_qos "$_act" "$_kbps" "$_host"
    apply_reboot_cron "$_cron" || true
    apply_proxy_mode "$_pmode" "$_act"
    apply_access_ctl "$_amode" "$_alist"
    if [ "$_act" = "false" ]; then
        echo 1 > "$STATE_DIR/license_forced_direct"
        oc_set_group PROXY DIRECT 2>/dev/null || true
    elif [ -f "$STATE_DIR/license_forced_direct" ]; then
        oc_set_group PROXY AUTO 2>/dev/null || true
        rm -f "$STATE_DIR/license_forced_direct"
    fi
    printf '%s\n' "$_fp" > "$STATE_DIR/policy.fp"
    log "policy applied active=$_act kbps=$_kbps mode=$_pmode access=$_amode cron='$_cron'"
}

apply_policy_from_hb() {
    _raw=$(cat "$STATE_DIR/hb.resp" 2>/dev/null) || return 0
    _act=$(json_str "$_raw" '$.policy.service_active')
    [ -n "$_act" ] || return 0
    apply_policy_fields "$_act" \
        "$(json_str "$_raw" '$.policy.vps_limit_kbps')" \
        "$(json_str "$_raw" '$.policy.reboot_cron')" \
        "$(json_str "$_raw" '$.policy.vps_host')" \
        "$(json_str "$_raw" '$.policy.proxy_mode')" \
        "$(json_str "$_raw" '$.policy.access_mode')" \
        "$(json_str "$_raw" '$.policy.access_list')"
}

task_apply_policy() {
    _tj="$1"
    _tid=$(json_str "$_tj" '$.task_id'); _lease=$(json_str "$_tj" '$.lease_id')
    _act=$(json_str "$_tj" '$.params.service_active')
    _kbps=$(json_str "$_tj" '$.params.vps_limit_kbps')
    _cron=$(json_str "$_tj" '$.params.reboot_cron')
    _host=$(json_str "$_tj" '$.params.vps_host')
    _pmode=$(json_str "$_tj" '$.params.proxy_mode')
    _amode=$(json_str "$_tj" '$.params.access_mode')
    _alist=$(json_str "$_tj" '$.params.access_list')
    case "$_act" in true|false) ;; *) persist_result "$_tid" "$_lease" failed '{"error":"invalid service_active"}'; return ;; esac
    rm -f "$STATE_DIR/policy.fp"  # 强制落地
    apply_policy_fields "$_act" "$_kbps" "$_cron" "$_host" "$_pmode" "$_amode" "$_alist"
    persist_result "$_tid" "$_lease" ok "{\"service_active\":$_act,\"proxy_mode\":\"$_pmode\",\"access_mode\":\"$_amode\"}"
}

_diag_trim() { # stdin → 单行，截断
    tr '\n' '|' | head -c 2000
}

task_collect_diag() {
    _tj="$1"
    _tid=$(json_str "$_tj" '$.task_id'); _lease=$(json_str "$_tj" '$.lease_id')
    _up=$(cut -d. -f1 /proc/uptime 2>/dev/null)
    _mem=$(awk '/^MemAvailable:/{print int($2/1024)}' /proc/meminfo 2>/dev/null)
    _wan=$(ip -4 -o addr show dev wan 2>/dev/null | awk '{print $4}' | tr '\n' ',')
    _wg=$(wg show wg0 2>/dev/null | _diag_trim)
    _mh=dead
    curl -sS -m 2 -o /dev/null http://127.0.0.1:9090/version 2>/dev/null && _mh=ok
    _ww=$(tail -5 /etc/routermgmt/logs/wan-watch.log 2>/dev/null | _diag_trim)
    _fp=$(cat "$STATE_DIR/policy.fp" 2>/dev/null)
    _nft=$(nft list table inet vps_qos 2>/dev/null | _diag_trim)
    _np=$(tail -12 /etc/routermgmt/logs/netprobe.log 2>/dev/null)
    persist_result "$_tid" "$_lease" ok "$(printf '{"uptime_s":%s,"mem_avail_mb":%s,"wan":"%s","mihomo":"%s","wg":"%s","wan_watch":"%s","policy_fp":"%s","vps_qos":"%s","netprobe":"%s"}' \
        "${_up:-0}" "${_mem:-0}" "$(printf '%s' "$_wan" | sed 's/"/\\"/g')" "$_mh" \
        "$(printf '%s' "$_wg" | sed 's/"/\\"/g')" "$(printf '%s' "$_ww" | sed 's/"/\\"/g')" \
        "$(printf '%s' "$_fp" | sed 's/"/\\"/g')" "$(printf '%s' "$_nft" | sed 's/"/\\"/g')" \
        "$(printf '%s' "$_np" | tr '\n' '|' | sed 's/"/\\"/g')")"
}

# set_wifi：远程开关 Wi-Fi（2026-09-23）。关闭时布防 5 分钟自动恢复兜底：
# 管理心跳走有线/WAN 隧道不受影响；300 秒内无新的 set_wifi enabled=true 确认，
# cron 每分钟巡检超时后自动重新启用并解除布防（防"远程关掉后联系不上"）。
wifi_restore_disarm() {
    rm -f "$STATE_DIR/wifi.disabled"
    sed -i '/#routermgmt-wifi-restore/d' /etc/crontabs/root 2>/dev/null
    [ -x /etc/init.d/cron ] && /etc/init.d/cron restart 2>/dev/null
    return 0
}

wifi_restore_arm() {
    date +%s > "$STATE_DIR/wifi.disabled"
    cat > /etc/routermgmt/wifi_restore.sh <<'EOS'
#!/bin/sh
# routermgmt Wi-Fi 自动恢复：布防 300 秒无确认则自动开回
_f="/etc/routermgmt/state/wifi.disabled"
[ -f "$_f" ] || exit 0
_now=$(date +%s); _ts=$(cat "$_f" 2>/dev/null || echo 0)
[ $((_now - _ts)) -lt 300 ] && exit 0
for _r in $(uci show wireless 2>/dev/null | sed -n 's/^\(wireless\.[^.=]*\)=wifi-device$/\1/p'); do
    uci set "$_r.disabled=0"
done
uci commit wireless
wifi up 2>/dev/null || /sbin/wifi up 2>/dev/null
rm -f "$_f"
sed -i '/#routermgmt-wifi-restore/d' /etc/crontabs/root 2>/dev/null
[ -x /etc/init.d/cron ] && /etc/init.d/cron restart 2>/dev/null
logger -t routermgmt-agent "wifi auto-restored after timeout"
EOS
    chmod +x /etc/routermgmt/wifi_restore.sh
    grep -q '#routermgmt-wifi-restore' /etc/crontabs/root 2>/dev/null || \
        echo '* * * * * /etc/routermgmt/wifi_restore.sh #routermgmt-wifi-restore' >> /etc/crontabs/root
    [ -x /etc/init.d/cron ] && /etc/init.d/cron restart 2>/dev/null
    return 0
}

task_set_wifi() {
    _tj="$1"
    _tid=$(json_str "$_tj" '$.task_id'); _lease=$(json_str "$_tj" '$.lease_id')
    _en=$(json_str "$_tj" '$.params.enabled')
    case "$_en" in
        true|false) ;;
        *) persist_result "$_tid" "$_lease" failed '{"error":"invalid params.enabled"}'; return ;;
    esac
    _n=0
    for _r in $(uci show wireless 2>/dev/null | sed -n 's/^\(wireless\.[^.=]*\)=wifi-device$/\1/p'); do
        if [ "$_en" = "true" ]; then uci set "$_r.disabled=0"; else uci set "$_r.disabled=1"; fi
        _n=$((_n+1))
    done
    [ "$_n" -gt 0 ] || { persist_result "$_tid" "$_lease" failed '{"error":"no wifi-device found"}'; return 0; }
    uci commit wireless
    if [ "$_en" = "true" ]; then
        wifi up 2>/dev/null || /sbin/wifi up 2>/dev/null
        wifi_restore_disarm
        persist_result "$_tid" "$_lease" ok "{\"enabled\":true,\"radios\":$_n}"
        log "task $_tid wifi enabled radios=$_n"
    else
        wifi down 2>/dev/null || /sbin/wifi down 2>/dev/null
        wifi_restore_arm
        persist_result "$_tid" "$_lease" ok "{\"enabled\":false,\"radios\":$_n,\"auto_restore_s\":300}"
        log "task $_tid wifi disabled radios=$_n auto_restore=300s"
    fi
    return 0
}
