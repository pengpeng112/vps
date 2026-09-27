package httpserver

import (
	"github.com/alexedwards/argon2id"
)

// HashPassword 生成 argon2id 哈希（使用库默认强参数）。
func HashPassword(pwd string) (string, error) {
	return argon2id.CreateHash(pwd, argon2id.DefaultParams)
}

func verifyArgon2(pwd, hash string) (bool, error) {
	return argon2id.ComparePasswordAndHash(pwd, hash)
}
