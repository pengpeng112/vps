package crypto

import (
	"strings"
	"testing"
)

func TestEnvelopeRoundtrip(t *testing.T) {
	key := make([]byte, 32)
	for i := range key {
		key[i] = byte(i)
	}
	enc, err := EncryptString(key, "secret-value", "devices:rtr-x:agent_hmac_key")
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(enc, "secret-value") {
		t.Fatal("密文不得含明文")
	}
	got, err := DecryptString(key, enc, "devices:rtr-x:agent_hmac_key")
	if err != nil || got != "secret-value" {
		t.Fatalf("往返失败: %v %q", err, got)
	}
	// AAD 不匹配必须失败（密文不可挪用）
	if _, err := DecryptString(key, enc, "devices:rtr-y:agent_hmac_key"); err == nil {
		t.Fatal("AAD 不匹配应解密失败")
	}
	// 篡改密文
	tampered := enc[:len(enc)-4] + "AAA="
	if _, err := DecryptString(key, tampered, "devices:rtr-x:agent_hmac_key"); err == nil {
		t.Fatal("篡改密文应失败")
	}
	// 两次加密 nonce 不同
	enc2, _ := EncryptString(key, "secret-value", "devices:rtr-x:agent_hmac_key")
	if enc == enc2 {
		t.Fatal("随机 nonce 下两次密文不应相同")
	}
}

func TestConfigSignatureVector(t *testing.T) {
	// 签名原文：sha256(yaml)\n device \n version
	yaml := []byte("mixed-port: 7890\n")
	payload := ConfigSignaturePayload(yaml, "rtr-abc123", 18)
	if !strings.HasSuffix(string(payload), "\nrtr-abc123\n18") {
		t.Fatalf("payload 形态不符: %q", payload)
	}
	priv, pub, err := GenerateEd25519Key()
	if err != nil {
		t.Fatal(err)
	}
	sig, err := SignConfig(priv, yaml, "rtr-abc123", 18)
	if err != nil {
		t.Fatal(err)
	}
	if len(sig) != 128 {
		t.Fatalf("签名应为 128 hex，实际 %d", len(sig))
	}
	if !VerifyConfig(pub, yaml, "rtr-abc123", 18, sig) {
		t.Fatal("合法签名应通过")
	}
	// 改一个 YAML 字节
	if VerifyConfig(pub, append(yaml, ' '), "rtr-abc123", 18, sig) {
		t.Fatal("改 YAML 字节应拒绝")
	}
	// 换设备
	if VerifyConfig(pub, yaml, "rtr-other1", 18, sig) {
		t.Fatal("换设备应拒绝")
	}
	// 换版本
	if VerifyConfig(pub, yaml, "rtr-abc123", 19, sig) {
		t.Fatal("换版本应拒绝")
	}
}

func TestMaskRules(t *testing.T) {
	// uuid/pbk 前后 4 位，sid 前后 2 位（§9）
	if got := MaskNodeParam("uuid", "11111111-2222-3333-4444-555555555555"); got != "1111...5555" {
		t.Errorf("uuid 掩码: %s", got)
	}
	if got := MaskNodeParam("pbk", "TESTpbkValue_43chars_AAAAAAAAAAAAAAAAAAAA"); got != "TEST...AAAA" {
		t.Errorf("pbk 掩码: %s", got)
	}
	if got := MaskNodeParam("sid", "aabbccdd"); got != "aa...dd" {
		t.Errorf("sid 掩码: %s", got)
	}
	if got := MaskNodeParam("sid", "abcd"); got != "****" {
		t.Errorf("过短掩码: %s", got)
	}
}

func TestWGKeypair(t *testing.T) {
	priv, pub, err := GenerateWGKeypair()
	if err != nil {
		t.Fatal(err)
	}
	pub2, err := WGPubkeyFromPriv(priv)
	if err != nil || pub2 != pub {
		t.Fatalf("公私钥推导不一致: %v %s vs %s", err, pub2, pub)
	}
}
