package crypto

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"encoding/base64"
	"errors"
	"fmt"
)

// Encrypt 使用 MASTER_KEY(32 字节) 做 AES-256-GCM，输出 base64(nonce|ciphertext)。
// aad 绑定记录 ID/字段名，防止密文被挪用到其它记录（任务书 §8）。
func Encrypt(masterKey []byte, plaintext []byte, aad string) (string, error) {
	if len(masterKey) != 32 {
		return "", errors.New("master key 必须为 32 字节")
	}
	block, err := aes.NewCipher(masterKey)
	if err != nil {
		return "", err
	}
	gcm, err := cipher.NewGCM(block)
	if err != nil {
		return "", err
	}
	nonce := make([]byte, gcm.NonceSize())
	if _, err := rand.Read(nonce); err != nil {
		return "", err
	}
	ct := gcm.Seal(nil, nonce, plaintext, []byte(aad))
	return base64.StdEncoding.EncodeToString(append(nonce, ct...)), nil
}

// Decrypt 解开 Encrypt 的信封。
func Decrypt(masterKey []byte, enc string, aad string) ([]byte, error) {
	raw, err := base64.StdEncoding.DecodeString(enc)
	if err != nil {
		return nil, err
	}
	block, err := aes.NewCipher(masterKey)
	if err != nil {
		return nil, err
	}
	gcm, err := cipher.NewGCM(block)
	if err != nil {
		return nil, err
	}
	if len(raw) < gcm.NonceSize() {
		return nil, errors.New("密文长度不合法")
	}
	pt, err := gcm.Open(nil, raw[:gcm.NonceSize()], raw[gcm.NonceSize():], []byte(aad))
	if err != nil {
		return nil, fmt.Errorf("解密失败(aad=%s): %w", aad, err)
	}
	return pt, nil
}

// EncryptString / DecryptString 是字节版本的便捷封装。
func EncryptString(masterKey []byte, plaintext, aad string) (string, error) {
	return Encrypt(masterKey, []byte(plaintext), aad)
}

func DecryptString(masterKey []byte, enc, aad string) (string, error) {
	b, err := Decrypt(masterKey, enc, aad)
	return string(b), err
}
