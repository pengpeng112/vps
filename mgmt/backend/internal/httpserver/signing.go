package httpserver

import (
	"crypto/ed25519"
	"fmt"

	"routermgmt/internal/crypto"
)

// Ed25519SigningKey 实现 SigningKey（生产从 DATA_DIR 加载）。
type Ed25519SigningKey struct {
	Priv    ed25519.PrivateKey
	PubPEM  string
}

func NewEd25519SigningKey(dataDir string) (*Ed25519SigningKey, error) {
	priv, pubPEM, err := crypto.LoadOrCreateSigningKey(dataDir)
	if err != nil {
		return nil, fmt.Errorf("加载签名密钥: %w", err)
	}
	return &Ed25519SigningKey{Priv: priv, PubPEM: pubPEM}, nil
}

func (k *Ed25519SigningKey) Sign(yamlBytes []byte, deviceID string, version int64) (string, error) {
	return crypto.SignConfig(k.Priv, yamlBytes, deviceID, version)
}

func (k *Ed25519SigningKey) PublicPEM() string { return k.PubPEM }
