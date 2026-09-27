package httpserver

import (
	"testing"
)

// 跨 Go/ash 的固定测试向量（仅测试密钥）。
// ash 侧向量见 mgmt/agent/tests/vectors.sh，两者必须一致（任务书 §7.1）。
const (
	vecKey      = "1122334455667788112233445566778811223344556677881122334455667788"
	vecDeviceID = "rtr-abc123"
	vecTS       = int64(1760000000)
	vecNonce    = "00112233445566778899aabbccddeeff"
	vecMethod   = "POST"
	vecPath     = "/api/v1/agent/heartbeat"
	vecBody     = `{"hello":"world"}`
	vecBodyHash = "93a23971a914e5eacbf0a8d25154cda309c3c1c72fbb9914d47c60f3cb681588"
	vecSig      = "fb03c4ca958fe9a5a64262eadc1d47f1515dc5130e0fd5e1f5387cb8918e6fda"

	vecEmptyBodyHash = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
)

func TestCanonicalVectorMatchesOpensSL(t *testing.T) {
	canonical := CanonicalString(vecDeviceID, vecTS, vecNonce, vecMethod, vecPath, vecBodyHash)
	got := SignCanonical(vecKey, canonical)
	if got != vecSig {
		t.Fatalf("HMAC 向量不匹配:\n got  %s\n want %s", got, vecSig)
	}
}

func TestVerifyDeviceHMACVector(t *testing.T) {
	if !VerifyDeviceHMAC(vecKey, vecDeviceID, vecTS, vecNonce, vecMethod, vecPath, []byte(vecBody), vecSig) {
		t.Fatal("合法签名应通过")
	}
	// 改一个 body 字节 → 拒绝
	if VerifyDeviceHMAC(vecKey, vecDeviceID, vecTS, vecNonce, vecMethod, vecPath, []byte(vecBody+" "), vecSig) {
		t.Fatal("改 body 后应拒绝")
	}
	// 换设备 → 拒绝
	if VerifyDeviceHMAC(vecKey, "rtr-other1", vecTS, vecNonce, vecMethod, vecPath, []byte(vecBody), vecSig) {
		t.Fatal("换设备应拒绝")
	}
	// 换 path → 拒绝
	if VerifyDeviceHMAC(vecKey, vecDeviceID, vecTS, vecNonce, vecMethod, "/api/v1/agent/tasks", []byte(vecBody), vecSig) {
		t.Fatal("换 path 应拒绝")
	}
	// 错误密钥 → 拒绝
	if VerifyDeviceHMAC(vecKey+"00", vecDeviceID, vecTS, vecNonce, vecMethod, vecPath, []byte(vecBody), vecSig) {
		t.Fatal("错误密钥应拒绝")
	}
}

func TestEmptyBodyHashVector(t *testing.T) {
	// GET 空 body 的 sha256hex("") 已知值
	canonical := CanonicalString(vecDeviceID, vecTS, vecNonce, "GET", "/api/v1/agent/tasks", vecEmptyBodyHash)
	got := SignCanonical(vecKey, canonical)
	if got == "" || len(got) != 64 {
		t.Fatal("GET 签名长度不合法")
	}
	if !VerifyDeviceHMAC(vecKey, vecDeviceID, vecTS, vecNonce, "GET", "/api/v1/agent/tasks", nil, got) {
		t.Fatal("GET 空 body 签名应自洽")
	}
}
