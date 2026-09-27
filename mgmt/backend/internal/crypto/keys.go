// Package crypto 提供密钥信封、签名与掩码工具。
// - AES-256-GCM 信封：随机 nonce，AAD 绑定记录 ID/字段名
// - HMAC key：32 字节随机数编码为 64 个小写 hex 字符，HMAC 计算直接使用该
//   ASCII 字节串作 key（不解码）——与 Agent 侧 openssl -hmac 行为一致（任务书 §7.1）
// - ed25519 配置签名；WireGuard curve25519 密钥对；敏感字段掩码
package crypto

import (
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base64"
	"encoding/hex"
)

// GenerateHMACKeyHex 生成 Agent HMAC 密钥：64 个小写 hex 字符的字符串。
func GenerateHMACKeyHex() (string, error) { return GenerateHex(32) }

// GenerateSubToken 生成订阅 token（urlsafe base64，无填充，32 字节）。
func GenerateSubToken() (string, error) {
	b := make([]byte, 32)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return base64.RawURLEncoding.EncodeToString(b), nil
}

// GenerateEnrollCode 生成一次性注册码：32 字节随机数的 64 hex。
func GenerateEnrollCode() (string, error) { return GenerateHex(32) }

// GenerateHex 生成 n 字节随机数的 2n 个小写 hex 字符。
func GenerateHex(n int) (string, error) {
	b := make([]byte, n)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return hex.EncodeToString(b), nil
}

// GenerateNonceHex 生成防重放 nonce（16 字节 => 32 个小写 hex 字符）。
func GenerateNonceHex() (string, error) { return GenerateHex(16) }

// GenerateEd25519Key 生成 ed25519 签名密钥对，返回 (priv, pub)。
func GenerateEd25519Key() (ed25519.PrivateKey, ed25519.PublicKey, error) {
	pub, priv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		return nil, nil, err
	}
	return priv, pub, nil
}
