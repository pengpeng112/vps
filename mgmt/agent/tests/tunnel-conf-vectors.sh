#!/bin/sh
# tunnel-conf-vectors.sh — mgmt-tunnel tunnel.conf 白名单解析器固定向量（2026-09-24 阶段1B）
# 在 busybox ash 容器运行：sh tests/tunnel-conf-vectors.sh ；退出码 0 = 全部通过
# 直接 source 生产 init 脚本（其 shebang 行是注释，顶层只有变量与函数定义），
# 保证测试对象与部署对象是同一份代码。
set -u
FAIL=0
HERE=$(dirname "$0")
. "$HERE/../init/mgmt-tunnel"

ck() { if [ "$2" = "$3" ]; then echo "PASS $1"; else echo "FAIL $1: got=[$2] want=[$3]"; FAIL=1; fi; }

T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT

reset_tc() { # shellcheck disable=SC2034
    TC_VPS_HOST=""; TC_VPS_PORT=""; TC_MGMT_LOCAL_PORT=""; TC_MGMT_TARGET=""; TC_RSSH_REMOTE_PORT=""; TC_RSSH_TARGET=""; }

# 1) 完整合法配置
reset_tc
cat > "$T/c1" <<'EOF'
# comment
VPS_HOST=159.223.36.119
VPS_SSH_PORT=28722
MGMT_LOCAL_PORT=18443
MGMT_TARGET=10.10.0.1:8443
RSSH_REMOTE_PORT=18222
RSSH_TARGET=127.0.0.1:22
EOF
CONF="$T/c1" load_tunnel_conf
ck v1_rc "$?" "0"
ck v1_host "$TC_VPS_HOST" "159.223.36.119"
ck v1_rssh_port "$TC_RSSH_REMOTE_PORT" "18222"
ck v1_rssh_target "$TC_RSSH_TARGET" "127.0.0.1:22"

# 2) 缺文件 → rc=1
reset_tc
CONF="$T/not-exist" load_tunnel_conf
ck v2_missing_rc "$?" "1"
ck v2_host_stays_empty "$TC_VPS_HOST" ""

# 3) 非法值逐键跳过；核心键（含端口）一旦非法 → 整体无效 → init 降级默认（保心跳）
reset_tc
cat > "$T/c3" <<'EOF'
VPS_HOST=10.0.0.1
VPS_SSH_PORT=99999
MGMT_LOCAL_PORT=18443
MGMT_TARGET=10.10.0.1:8443
RSSH_REMOTE_PORT=abc
RSSH_TARGET=127.0.0.1
EOF
CONF="$T/c3" load_tunnel_conf
ck v3_rc "$?" "1"
ck v3_port_invalid_skipped "$TC_VPS_PORT" ""
ck v3_host_kept "$TC_VPS_HOST" "10.0.0.1"
ck v3_rssh_port_invalid_skipped "$TC_RSSH_REMOTE_PORT" ""
ck v3_rssh_target_noport_skipped "$TC_RSSH_TARGET" ""

# 4) 注入向量：未知键必须被忽略（不执行、不报错）
reset_tc
cat > "$T/c4" <<'EOF'
EVIL=;touch /tmp/pwned-marker
VPS_HOST=1.2.3.4;touch /tmp/pwned2
VPS_HOST=5.6.7.8
VPS_SSH_PORT=28722
MGMT_LOCAL_PORT=18443
MGMT_TARGET=10.10.0.1:8443
EOF
rm -f /tmp/pwned-marker /tmp/pwned2
CONF="$T/c4" load_tunnel_conf
ck v4_injected_host_rejected "$TC_VPS_HOST" "5.6.7.8"
if [ -e /tmp/pwned-marker ] || [ -e /tmp/pwned2 ]; then echo "FAIL v4_no_execution"; FAIL=1; else echo "PASS v4_no_execution"; fi

# 5) 只有核心键（无 RSSH，家宽默认形态）→ 有效但 RSSH 为空
reset_tc
cat > "$T/c5" <<'EOF'
VPS_HOST=example.com
VPS_SSH_PORT=22
MGMT_LOCAL_PORT=18443
MGMT_TARGET=10.10.0.1:8443
EOF
CONF="$T/c5" load_tunnel_conf
ck v5_rc "$?" "0"
ck v5_rssh_empty "$TC_RSSH_REMOTE_PORT" ""

# 6) 核心键缺失 → 整体无效（init 将降级默认）
reset_tc
printf 'VPS_HOST=1.1.1.1\nVPS_SSH_PORT=22\n' > "$T/c6"
CONF="$T/c6" load_tunnel_conf
ck v6_core_missing_rc "$?" "1"

# 7) 校验函数直接向量
_valid_host "a.b-9" && echo "PASS v7_host_ok" || { echo "FAIL v7_host_ok"; FAIL=1; }
if _valid_host "a b"; then echo "FAIL v7_host_space"; FAIL=1; else echo "PASS v7_host_space"; fi
if _valid_host ""; then echo "FAIL v7_host_empty"; FAIL=1; else echo "PASS v7_host_empty"; fi
_valid_port "65535" && echo "PASS v7_port_max" || { echo "FAIL v7_port_max"; FAIL=1; }
if _valid_port "65536"; then echo "FAIL v7_port_over"; FAIL=1; else echo "PASS v7_port_over"; fi
if _valid_port "0"; then echo "FAIL v7_port_zero"; FAIL=1; else echo "PASS v7_port_zero"; fi
_valid_target "192.168.132.1:22" && echo "PASS v7_target_ok" || { echo "FAIL v7_target_ok"; FAIL=1; }
if _valid_target "192.168.132.1"; then echo "FAIL v7_target_noport"; FAIL=1; else echo "PASS v7_target_noport"; fi

exit $FAIL
