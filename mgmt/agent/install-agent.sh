#!/bin/sh
# install-agent.sh — 在 iStoreOS/OpenWrt 路由器上导入离线引导包并安装 Agent（§19.1/§14）
# 用法（可信 LAN 的 SSH 或本地介质）：
#   ./install-agent.sh /path/to/bootstrap-package.json
# 做的事：
#   1. 依赖探测与安装（curl openssl-util jsonfilter），Ed25519 自测向量
#   2. 检查 10.10.0.0/24 与现场 LAN 冲突（有冲突阻止安装并记录）
#   3. 写 /etc/routermgmt/agent.conf（600）与 sign_pub.pem
#   4. 安装 agent 主程序与 handlers、procd 服务
#   5. 探测 OpenClash 配置目录；校验管理面连通后启动服务
# 不做的事：不碰 uboot/分区；不开启 OpenClash 原生订阅自动更新（配置只由 Agent 应用）

set -u

PKG="${1:-}"
AGENT_ROOT=/etc/routermgmt
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

[ -n "$PKG" ] && [ -f "$PKG" ] || { echo "用法: $0 <bootstrap-package.json>"; exit 2; }

fail() { echo "install-agent: 错误: $*" >&2; exit 1; }

# ---- 1. 依赖 ----
need_cmd() { command -v "$1" >/dev/null 2>&1; }

if command -v opkg >/dev/null 2>&1; then
    for p in curl openssl-util jsonfilter; do
        if ! opkg list-installed 2>/dev/null | grep -q "^$p "; then
            echo "安装依赖 $p ..."
            opkg update >/dev/null 2>&1 || fail "opkg update 失败（检查路由器联网）"
            opkg install "$p" >/dev/null 2>&1 || fail "无法安装 $p"
        fi
    done
fi
for c in curl openssl jsonfilter; do
    need_cmd "$c" || fail "缺少 $c 且无法自动安装；请手动 opkg install curl openssl-util jsonfilter"
done

# Ed25519 能力自测（固定向量；缺能力明确阻止配置应用，§7.5）
TMP=$(mktemp -d) || fail "mktemp"
trap 'rm -rf "$TMP"' EXIT
cat > "$TMP/pub.pem" <<'EOF'
-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEASLgVgfoIZf/aWTq1EXGmvzHVG3PefoT1apwyfSiaXLQ=
-----END PUBLIC KEY-----
EOF
printf 'payload-for-ed25519-vector-test' > "$TMP/payload"
printf '\x7c\xbc\xce\x74\x50\xf9\x19\xbc\xe7\xca\xd0\x7f\x74\x5b\xe8\x37\x0a\xd8\x42\x65\x72\x8a\xd8\x43\x83\xb5\xd3\xa1\xb6\x3f\xf8\xe5\xd4\x72\x0a\x56\xaf\x80\x3a\x4b\x64\xae\x13\xed\x4c\xeb\xc2\xb5\x20\x3e\x94\x1d\x50\x2f\xda\x27\x4a\xd3\x40\x3b\x0e\xf5\xae\x05' > "$TMP/sig"
if ! openssl pkeyutl -verify -pubin -inkey "$TMP/pub.pem" -rawin -in "$TMP/payload" -sigfile "$TMP/sig" >/dev/null 2>&1; then
    fail "本机 OpenSSL 不支持 Ed25519 验签；升级 openssl-util 后重试（不得跳过验签）"
fi

# ---- 2. 网段冲突检查（§19.1）----
if ip route show 2>/dev/null | grep -q '^[0-9].* 10\.10\.0\.' || ip addr show 2>/dev/null | grep -q '10\.10\.0\.'; then
    fail "现场网络已存在 10.10.0.0/24 相关路由/地址，与管理 WG 网段冲突；请先调整地址规划"
fi

# ---- 3. 解析引导包（jsonfilter，不 eval）----
jget() { jsonfilter -s "$(cat "$PKG")" -e "$1" 2>/dev/null; }

DEVICE_ID=$(jget '$.device_id') || fail "引导包缺少 device_id"
HMAC_KEY=$(jget '$.hmac_key') || fail "引导包缺少 hmac_key"
MGMT_URL=$(jget '$.mgmt_url')
SUB_URL=$(jget '$.sub_url')
SIGN_PUB=$(jget '$.sign_public_key')
TRANSPORT=$(jget '$.transport_mode')
[ -n "$DEVICE_ID" ] && [ -n "$HMAC_KEY" ] && [ -n "$SIGN_PUB" ] || fail "引导包字段不完整"
[ -n "$MGMT_URL" ] || MGMT_URL="${MGMT_URL_OVERRIDE:-}"
[ -n "$MGMT_URL" ] || fail "引导包缺少 mgmt_url 且未设置 MGMT_URL_OVERRIDE"

case "$TRANSPORT" in
    mock) echo "注意：transport_mode=mock（管理面尚未启用 WireGuard），Agent 直接连接 $MGMT_URL" ;;
    wireguard) echo "transport_mode=wireguard" ;;
    *) fail "引导包 transport_mode 未知: $TRANSPORT" ;;
esac

# ---- 4. 写配置 ----
mkdir -p "$AGENT_ROOT" "$AGENT_ROOT/state" "$AGENT_ROOT/handlers"
chmod 700 "$AGENT_ROOT"
cat > "$AGENT_ROOT/agent.conf" <<EOF
DEVICE_ID=$DEVICE_ID
HMAC_KEY=$HMAC_KEY
MGMT_URL=$MGMT_URL
SUB_URL=${SUB_URL:-}
SIGN_PUB=installed
EOF
chmod 600 "$AGENT_ROOT/agent.conf"
printf '%s\n' "$SIGN_PUB" > "$AGENT_ROOT/sign_pub.pem"
chmod 644 "$AGENT_ROOT/sign_pub.pem"

install -m 755 "$SCRIPT_DIR/routermgmt-agent.sh" /usr/bin/routermgmt-agent
cp "$SCRIPT_DIR/routermgmt-agent.sh" "$AGENT_ROOT/routermgmt-agent.sh" && chmod 700 "$AGENT_ROOT/routermgmt-agent.sh"
install -m 644 "$SCRIPT_DIR/handlers/switch_node.sh" "$AGENT_ROOT/handlers/switch_node.sh"
if [ -f "$SCRIPT_DIR/access-fw.sh" ]; then
    install -m 755 "$SCRIPT_DIR/access-fw.sh" "$AGENT_ROOT/access-fw.sh"
fi
if [ -f "$SCRIPT_DIR/init/routermgmt" ]; then
    install -m 755 "$SCRIPT_DIR/init/routermgmt" /etc/init.d/routermgmt
fi
# WAN 分层日志（重启不丢）
if [ -f "$SCRIPT_DIR/wan-watch.sh" ]; then
    install -m 755 "$SCRIPT_DIR/wan-watch.sh" /usr/sbin/wan-watch.sh
    mkdir -p /etc/hotplug.d/iface /etc/routermgmt/logs
    [ -f "$SCRIPT_DIR/init/wan-watch" ] && install -m 755 "$SCRIPT_DIR/init/wan-watch" /etc/init.d/wan-watch
    [ -f "$SCRIPT_DIR/wan-watch.hotplug" ] && install -m 755 "$SCRIPT_DIR/wan-watch.hotplug" /etc/hotplug.d/iface/98-wan-watch
    /etc/init.d/wan-watch enable >/dev/null 2>&1 || true
fi
# TCP 管理隧道脚本只安装、默认不启用（家宽走 WG；公司网/拦 UDP 再按文档启用）
if [ -f "$SCRIPT_DIR/init/mgmt-tunnel" ]; then
    install -m 755 "$SCRIPT_DIR/init/mgmt-tunnel" /etc/init.d/mgmt-tunnel
fi
# 隧道配置模板：不自动启用；现场按设备复制为 /etc/routermgmt/tunnel.conf（600）
if [ -f "$SCRIPT_DIR/tunnel.conf.example" ]; then
    install -m 600 "$SCRIPT_DIR/tunnel.conf.example" /etc/routermgmt/tunnel.conf.example
fi
# 链路质量探针：国内基线+VPS段丢包/延迟，每5分钟，诊断字段 netprobe（2026-09-24）
if [ -f "$SCRIPT_DIR/netprobe.sh" ]; then
    install -m 755 "$SCRIPT_DIR/netprobe.sh" /etc/routermgmt/netprobe.sh
    mkdir -p /etc/crontabs
    grep -q routermgmt-netprobe /etc/crontabs/root 2>/dev/null || \
        echo '*/5 * * * * /etc/routermgmt/netprobe.sh #routermgmt-netprobe' >> /etc/crontabs/root
    /etc/init.d/cron restart >/dev/null 2>&1 || true
fi
if [ -f "$SCRIPT_DIR/setup-mgmt-tunnel.sh" ]; then
    install -m 755 "$SCRIPT_DIR/setup-mgmt-tunnel.sh" /usr/sbin/setup-mgmt-tunnel.sh
fi

# ---- 5. WG（wireguard 模式才配置；mock 跳过，§7.2）----
WG_PRIV=$(jget '$.wg.private_key')
WG_ADDR=$(jget '$.wg.address')
WG_PEER=$(jget '$.wg.peer_public_key')
WG_EP=$(jget '$.wg.endpoint')
if [ "$TRANSPORT" = "wireguard" ] && [ -n "$WG_PRIV" ] && [ -n "$WG_PEER" ] && [ -n "$WG_EP" ]; then
    echo "配置 WireGuard 接口 wg0（仅路由管理网段，不接管默认路由）..."
    mkdir -p /etc/wireguard
    umask 077
    cat > /etc/wireguard/wg0.conf <<EOF
[Interface]
PrivateKey = $WG_PRIV
Address = $WG_ADDR
[Peer]
PublicKey = $WG_PEER
Endpoint = $WG_EP
AllowedIPs = 10.10.0.0/24
PersistentKeepalive = 25
EOF
    uci -q set network.wg0=interface
    uci -q set network.wg0.proto='wireguard'
    uci -q set network.wg0.addresses="$WG_ADDR"
    uci -q add_list network.wg0.addresses='' 2>/dev/null || true
    uci -q commit network
    echo "（Session 2 将由宿主机 Hub 侧自动放行该 Peer；当前用 wg0.conf + network.wg0）"
else
    echo "跳过 WireGuard 配置（mock 模式或字段缺失）"
fi

# ---- 6. OpenClash 目录探测（找不到则任务会失败并上报路径列表，不瞎写）----
CLASH_DIR=""
for d in /etc/openclash/config /etc/clash; do
    [ -d "$d" ] && CLASH_DIR="$d" && break
done
if [ -z "$CLASH_DIR" ]; then
    echo "警告：未找到 OpenClash 配置目录（/etc/openclash/config /etc/clash）"
    echo "      Agent 会安装，但 switch_node/reload_proxy 任务将失败并上报路径列表"
else
    echo "OpenClash 配置目录: $CLASH_DIR"
fi
# 禁用 OpenClash 原生订阅自动更新（正式配置只能由 Agent 验签应用，§19.3）
if command -v uci >/dev/null 2>&1 && uci -q get openclash.config >/dev/null 2>&1; then
    uci -q set openclash.config.auto_update='0' 2>/dev/null || true
    uci -q set openclash.config.auto_update_week='0' 2>/dev/null || true
    uci -q commit openclash 2>/dev/null || true
    echo "已禁用 OpenClash 订阅自动更新"
fi

# ---- 7. 启动 ----
if [ -x /etc/init.d/routermgmt ]; then
    /etc/init.d/routermgmt enable >/dev/null 2>&1
    /etc/init.d/routermgmt restart
    echo "Agent 已安装并启动（procd: routermgmt）"
else
    echo "无 procd（测试环境），手动运行: /usr/bin/routermgmt-agent"
fi
echo "完成。引导包临时副本请手动清理: $PKG"
