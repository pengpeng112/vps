#!/bin/sh
# 公司网/拦截 UDP 51820 的现场：用 SSH TCP 把管理心跳绕过去（家宽发货设备走 WG，不跑这个）。
# 2026-09-24 阶段1重构：生成 /etc/routermgmt/tunnel.conf（init 按白名单解析，
# 管理 -L 与排障 -R 拆双实例）；pin-hostkey 先核对指纹再用 dbclient 自写格式固化。
# 用法（路由器上）：
#   sh /usr/sbin/setup-mgmt-tunnel.sh [enable] [rssh端口] [rssh目标host:port]
#   sh /usr/sbin/setup-mgmt-tunnel.sh pin-hostkey SHA256:<指纹>   # 指纹经可信链路采集、人工核对后传入
# 可用环境变量覆盖默认值：
#   TUNNEL_VPS_HOST / TUNNEL_VPS_PORT / TUNNEL_LOCAL_PORT / TUNNEL_MGMT_TARGET
#   TUNNEL_RSSH_PORT / TUNNEL_RSSH_TARGET（排障 -R：VPS 回环端口与路由器 SSH 目标）
# 实测注意（2026-09-24，dropbear 2025.89/2020.81）：
#   - dbclient 的 known_hosts 固定读 /root/.ssh/known_hosts，不受 $HOME 影响
#   - 条目是纯主机名格式（不带 [host]:port），必须由 dbclient -y 自己写入才可靠
set -u
KEY=/etc/routermgmt/id_dropbear_mgmt
VPS=${TUNNEL_VPS_HOST:-159.223.36.119}
PORT=${TUNNEL_VPS_PORT:-28722}
LOCAL=${TUNNEL_LOCAL_PORT:-18443}
HUB=${TUNNEL_MGMT_TARGET:-10.10.0.1:8443}
KH=/root/.ssh/known_hosts

mode="${1:-setup}"
mkdir -p /etc/routermgmt

# ---- pin-hostkey SHA256:<指纹> ----
# 流程：无 -y 探测一次拿到服务端指纹（stdin EOF 自动退出）→ 与传入指纹比对 →
#       一致才清空重写（rm + 单次 -y 连接让 dbclient 自存）→ 无 -y 复连验证。
if [ "$mode" = "pin-hostkey" ]; then
    FP="${2:-}"
    case "$FP" in SHA256:*) ;; *) echo "用法: $0 pin-hostkey SHA256:<指纹>"; exit 2 ;; esac
    _fpv=${FP#SHA256:}
    case "$_fpv" in ''|*[!A-Za-z0-9+/=]*) echo "指纹字符集不合法"; exit 2 ;; esac
    mkdir -p /root/.ssh && chmod 700 /root/.ssh
    mv -f "$KH" "$KH.bak-$(date +%Y%m%d%H%M%S)" 2>/dev/null
    # 1) 探测指纹（无 -y，echo n 回答 y/n 提示使其打印指纹后退出）
    _out=$(echo n | /usr/bin/ssh -i "$KEY" -p "$PORT" -N -T root@"$VPS" 2>&1 | head -4)
    _seen=$(echo "$_out" | grep -o 'SHA256:[A-Za-z0-9+/=]*' | head -1)
    [ -n "$_seen" ] || { echo "未能取得服务端指纹：$_out"; exit 2; }
    if [ "$_seen" != "$FP" ]; then
        mv -f "$KH.bak-"* "$KH" 2>/dev/null
        echo "指纹不一致！服务端=$_seen 传入=$FP —— 疑似中间人，已中止并还原旧条目"
        exit 3
    fi
    # 2) TOFU 写入（dbclient 自写纯主机名格式）
    /usr/bin/ssh -y -i "$KEY" -p "$PORT" -N -T root@"$VPS" < /dev/null > /dev/null 2>&1 &
    _p=$!; sleep 4; kill $_p 2>/dev/null; wait $_p 2>/dev/null
    grep -q "^$VPS " "$KH" 2>/dev/null || { echo "TOFU 写入失败"; exit 2; }
    chmod 600 "$KH"
    # 3) 无 -y 复连验证
    /usr/bin/ssh -i "$KEY" -p "$PORT" -N -T root@"$VPS" < /dev/null > /dev/null 2>&1 &
    _p=$!; sleep 4; kill -0 $_p 2>/dev/null && _ok=1 || _ok=0; kill $_p 2>/dev/null; wait $_p 2>/dev/null
    [ "$_ok" = "1" ] || { echo "pin 后无 -y 验证失败"; exit 2; }
    echo "已固化 VPS host key（指纹 $_seen，无 -y 复连验证通过）。变更指纹将导致隧道拒连（fail-closed）"
    exit 0
fi

if [ ! -f "$KEY" ]; then
    # SN-R1 等老 dropbear 只能生成 RSA；先生成 ed25519 失败再退 RSA
    dropbearkey -t ed25519 -f "$KEY" >/dev/null 2>&1 || dropbearkey -t rsa -s 2048 -f "$KEY" >/dev/null
    chmod 600 "$KEY"
fi
PUB=$(dropbearkey -y -f "$KEY" | grep -E '^ssh-(ed25519|rsa) ')

# 排障 -R 参数（enable 模式也可从位置参数取）
RSSH_PORT="${TUNNEL_RSSH_PORT:-${2:-}}"
RSSH_TARGET="${TUNNEL_RSSH_TARGET:-${3:-}}"

# 写 tunnel.conf（600，仅白名单键；init 逐行解析）
cat > /etc/routermgmt/tunnel.conf <<EOF
# mgmt-tunnel 配置（严格 key=value 白名单，未知键忽略；改动后 /etc/init.d/mgmt-tunnel restart）
VPS_HOST=$VPS
VPS_SSH_PORT=$PORT
MGMT_LOCAL_PORT=$LOCAL
MGMT_TARGET=$HUB
EOF
if [ -n "$RSSH_PORT" ] && [ -n "$RSSH_TARGET" ]; then
    printf 'RSSH_REMOTE_PORT=%s\nRSSH_TARGET=%s\n' "$RSSH_PORT" "$RSSH_TARGET" >> /etc/routermgmt/tunnel.conf
fi
chmod 600 /etc/routermgmt/tunnel.conf

echo "== tunnel.conf 已写入（RSSH: ${RSSH_PORT:-未配置} -> ${RSSH_TARGET:-} ）=="
if [ -n "$RSSH_PORT" ]; then
    echo "把下面这一行追加到 VPS /root/.ssh/authorized_keys ："
    echo "no-agent-forwarding,no-X11-forwarding,no-pty,permitopen=\"$HUB\",permitlisten=\"127.0.0.1:$RSSH_PORT\" $PUB rtr-mgmt-tunnel"
else
    echo "把下面这一行追加到 VPS /root/.ssh/authorized_keys ："
    echo "no-agent-forwarding,no-X11-forwarding,no-pty,permitopen=\"$HUB\" $PUB rtr-mgmt-tunnel"
fi
echo
echo "VPS sshd 需允许 root 转发（99-hardening.conf Match User root AllowTcpForwarding yes）"
echo "建议固化主机钥（指纹经可信链路采集人工核对后）："
echo "  sh /usr/sbin/setup-mgmt-tunnel.sh pin-hostkey SHA256:<指纹>"
echo "  （VPS 侧采集：ssh-keyscan -p $PORT $VPS 2>/dev/null | ssh-keygen -lf - 对应 ed25519 行）"

[ "$mode" = "enable" ] || exit 0

if [ ! -x /etc/init.d/mgmt-tunnel ]; then
    echo "缺少 /etc/init.d/mgmt-tunnel"
    exit 1
fi
/etc/init.d/mgmt-tunnel enable
/etc/init.d/mgmt-tunnel restart
sleep 3
code=$(curl -sk -m 5 -o /dev/null -w '%{http_code}' "https://127.0.0.1:$LOCAL/api/v1/health" || echo 000)
if [ "$code" != "200" ]; then
    echo "隧道健康检查失败 http=$code，未改 Agent URL"
    exit 2
fi
sed -i "s|^MGMT_URL=.*|MGMT_URL=https://127.0.0.1:$LOCAL|" /etc/routermgmt/agent.conf
/etc/init.d/routermgmt restart
echo "已启用 TCP 管理通道 MGMT_URL=https://127.0.0.1:$LOCAL （WG 仍保留，不接管上网）"
exit 0
