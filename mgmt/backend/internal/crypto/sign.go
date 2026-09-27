package crypto

import (
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"path/filepath"
)

// ConfigSignaturePayload 按 §7.5 构造签名原文：
// lowercase_hex(SHA256(yaml)) + "\n" + device_id + "\n" + decimal_version
func ConfigSignaturePayload(yamlBytes []byte, deviceID string, version int64) []byte {
	sum := sha256.Sum256(yamlBytes)
	return []byte(hex.EncodeToString(sum[:]) + "\n" + deviceID + "\n" + fmt.Sprintf("%d", version))
}

// SignConfig 用 ed25519 私钥对配置签名，返回 128 个小写 hex 字符。
func SignConfig(priv ed25519.PrivateKey, yamlBytes []byte, deviceID string, version int64) (string, error) {
	if len(priv) != ed25519.PrivateKeySize {
		return "", errors.New("私钥长度不合法")
	}
	sig := ed25519.Sign(priv, ConfigSignaturePayload(yamlBytes, deviceID, version))
	return hex.EncodeToString(sig), nil
}

// VerifyConfig 用公钥验签（hex 签名）。
func VerifyConfig(pub ed25519.PublicKey, yamlBytes []byte, deviceID string, version int64, sigHex string) bool {
	sig, err := hex.DecodeString(sigHex)
	if err != nil || len(sig) != ed25519.SignatureSize {
		return false
	}
	return ed25519.Verify(pub, ConfigSignaturePayload(yamlBytes, deviceID, version), sig)
}

// LoadOrCreateSigningKey 从 DATA_DIR 加载 ed25519 私钥（PKCS8 PEM），不存在则生成。
// 返回私钥与公钥 PEM。容器重建后经持久卷保持同一密钥（任务书 §13）。
func LoadOrCreateSigningKey(dataDir string) (ed25519.PrivateKey, string, error) {
	privPath := filepath.Join(dataDir, "signing_ed25519.pem")
	if raw, err := os.ReadFile(privPath); err == nil {
		priv, err := parsePrivateKeyPEM(raw)
		if err != nil {
			return nil, "", fmt.Errorf("解析签名私钥 %s: %w", privPath, err)
		}
		pubPEM, err := publicKeyPEM(priv.Public().(ed25519.PublicKey))
		if err != nil {
			return nil, "", err
		}
		return priv, pubPEM, nil
	}
	priv, _, err := GenerateEd25519Key()
	if err != nil {
		return nil, "", err
	}
	der, err := marshalPrivateKeyPEM(priv)
	if err != nil {
		return nil, "", err
	}
	if err := os.WriteFile(privPath, der, 0o600); err != nil {
		return nil, "", err
	}
	pubPEM, err := publicKeyPEM(priv.Public().(ed25519.PublicKey))
	if err != nil {
		return nil, "", err
	}
	_ = os.WriteFile(filepath.Join(dataDir, "signing_ed25519.pub.pem"), []byte(pubPEM), 0o644)
	return priv, pubPEM, nil
}
