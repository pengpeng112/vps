package crypto

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"regexp"
	"strings"

	"golang.org/x/crypto/curve25519"
)

var wgBasepoint = [32]byte{9}

// GenerateWGKeypair 生成 WireGuard curve25519 密钥对（base64 标准编码）。
func GenerateWGKeypair() (privB64, pubB64 string, err error) {
	var priv [32]byte
	if _, err = rand.Read(priv[:]); err != nil {
		return
	}
	// RFC7748：clamp 由 curve25519.X25519 内部处理
	pub, err := curve25519.X25519(priv[:], wgBasepoint[:])
	if err != nil {
		return
	}
	privB64 = base64.StdEncoding.EncodeToString(priv[:])
	pubB64 = base64.StdEncoding.EncodeToString(pub)
	return
}

// WGPubkeyFromPriv 由私钥推导公钥（校验导入的私钥用）。
func WGPubkeyFromPriv(privB64 string) (string, error) {
	raw, err := base64.StdEncoding.DecodeString(privB64)
	if err != nil || len(raw) != 32 {
		return "", errBadWGKey
	}
	pub, err := curve25519.X25519(raw, wgBasepoint[:])
	if err != nil {
		return "", err
	}
	return base64.StdEncoding.EncodeToString(pub), nil
}

type wgErr struct{}

func (wgErr) Error() string { return "WireGuard 私钥必须为 32 字节 base64" }

var errBadWGKey = wgErr{}

// SHA256Hex 计算 sha256 的 hex。
func SHA256Hex(b []byte) string {
	sum := sha256.Sum256(b)
	return hex.EncodeToString(sum[:])
}

var (
	reHex64  = regexp.MustCompile(`^[0-9a-f]{64}$`)
	reUUID   = regexp.MustCompile(`^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$`)
	rePBK    = regexp.MustCompile(`^[A-Za-z0-9_-]{43}$`) // Reality public key: base64url 43 字符
	reSID    = regexp.MustCompile(`^[0-9a-fA-F]{0,16}$`) // short-id: 偶数长度 hex
)

// ValidEnrollCodeHash 校验 enroll_code 的 sha256 hex 形态。
func ValidEnrollCodeHash(s string) bool { return reHex64.MatchString(s) }

// ValidUUID 校验节点 uuid 参数。
func ValidUUID(s string) bool { return reUUID.MatchString(s) }

// ValidPBK 校验 Reality public key 参数。
func ValidPBK(s string) bool { return rePBK.MatchString(s) }

// ValidSID 校验 Reality short id 参数。
func ValidSID(s string) bool {
	return len(s)%2 == 0 && reSID.MatchString(s)
}

// MaskMid 掩码：保留前 keepHead 后 keepTail 个字符，中间用 ... 代替。
// 过短的字符串直接返回 "****"。
func MaskMid(s string, keepHead, keepTail int) string {
	if s == "" {
		return ""
	}
	if len(s) <= keepHead+keepTail+2 {
		return "****"
	}
	return s[:keepHead] + "..." + s[len(s)-keepTail:]
}

// MaskNodeParam 按 §9 掩码规则：uuid/pbk 前后 4 位，sid 前后 2 位。
// 同时接受字段名（uuid/pbk/sid）与 YAML 键名（public-key/short-id）。
func MaskNodeParam(name, value string) string {
	switch strings.ToLower(name) {
	case "sid", "short-id":
		return MaskMid(value, 2, 2)
	case "uuid", "pbk", "public-key":
		return MaskMid(value, 4, 4)
	default:
		return value
	}
}
