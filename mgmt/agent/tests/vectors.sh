#!/bin/sh
# vectors.sh — Agent 侧固定测试向量（与 Go 单测 hmac_vector_test.go 必须一致，§7.1）
# 在 busybox ash + openssl 环境运行（alpine 容器）；退出码 0 = 全部通过
set -u
FAIL=0
ck() { if [ "$2" = "$3" ]; then echo "PASS $1"; else echo "FAIL $1: got=$2 want=$3"; FAIL=1; fi; }

KEY="1122334455667788112233445566778811223344556677881122334455667788"
BODY_HASH=$(printf '%s' '{"hello":"world"}' | openssl dgst -sha256 -hex | awk '{print $NF}')
ck body_hash "$BODY_HASH" "93a23971a914e5eacbf0a8d25154cda309c3c1c72fbb9914d47c60f3cb681588"

SIG=$(printf '%s\n%s\n%s\n%s\n%s\n%s' \
    "rtr-abc123" "1760000000" "00112233445566778899aabbccddeeff" "POST" "/api/v1/agent/heartbeat" "$BODY_HASH" \
    | openssl dgst -sha256 -hmac "$KEY" -hex | awk '{print $NF}')
ck hmac_vector "$SIG" "fb03c4ca958fe9a5a64262eadc1d47f1515dc5130e0fd5e1f5387cb8918e6fda"

# 空 body 哈希
EMPTY_HASH=$(printf '' | openssl dgst -sha256 -hex | awk '{print $NF}')
ck empty_body_hash "$EMPTY_HASH" "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

# 签名原文与期望不同字节必须不同（防把 \n 写成字面量）
BAD=$(printf '%s\n%s\n%s\n%s\n%s\n%s\n' \
    "rtr-abc123" "1760000000" "00112233445566778899aabbccddeeff" "POST" "/api/v1/agent/heartbeat" "$BODY_HASH" \
    | openssl dgst -sha256 -hmac "$KEY" -hex | awk '{print $NF}')
if [ "$BAD" != "$SIG" ]; then echo "PASS trailing_lf_differs"; else echo "FAIL trailing_lf_differs（末尾多了 LF 仍得到相同签名？）"; FAIL=1; fi

# Ed25519 验签能力（安装自测同款向量）
TMP=$(mktemp -d)
cat > "$TMP/pub.pem" <<'EOF'
-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEASLgVgfoIZf/aWTq1EXGmvzHVG3PefoT1apwyfSiaXLQ=
-----END PUBLIC KEY-----
EOF
printf 'payload-for-ed25519-vector-test' > "$TMP/payload"
printf '\x7c\xbc\xce\x74\x50\xf9\x19\xbc\xe7\xca\xd0\x7f\x74\x5b\xe8\x37\x0a\xd8\x42\x65\x72\x8a\xd8\x43\x83\xb5\xd3\xa1\xb6\x3f\xf8\xe5\xd4\x72\x0a\x56\xaf\x80\x3a\x4b\x64\xae\x13\xed\x4c\xeb\xc2\xb5\x20\x3e\x94\x1d\x50\x2f\xda\x27\x4a\xd3\x40\x3b\x0e\xf5\xae\x05' > "$TMP/sig"
if openssl pkeyutl -verify -pubin -inkey "$TMP/pub.pem" -rawin -in "$TMP/payload" -sigfile "$TMP/sig" >/dev/null 2>&1; then
    echo "PASS ed25519_verify"
else
    echo "FAIL ed25519_verify"
    FAIL=1
fi
rm -rf "$TMP"

# 配置签名 payload 构造（hex→bin 转换）与 openssl 验签一致（配合真实 ed25519 密钥由 e2e 覆盖）
exit $FAIL
