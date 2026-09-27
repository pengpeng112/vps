// Package config 环境变量配置加载。
// 密钥规则（见任务书 §8）：MASTER_KEY 为 64 hex 解码成 32 字节 AES-256-GCM；
// JWT_SECRET 独立；ed25519 签名私钥持久化在 DATA_DIR。
// 开发模式（APP_ENV=dev）下密钥缺失时生成并写入 DATA_DIR/secrets.json（0600）；
// 生产模式（APP_ENV=prod）缺失直接报错。
package config

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"time"
)

type Config struct {
	Env           string // dev | prod
	Listen        string
	DatabaseURL   string // 运行态账号（audit_log 仅 INSERT/SELECT）
	MigrateDBURL  string // 迁移账号
	DataDir       string
	JWTSecret     string
	MasterKeyHex  string
	BootstrapAdmin string
	BootstrapPassword string

	MgmtPublicURL   string // 例如 https://10.10.0.1:8443
	WGHubPublicKey  string // 未配置 => transport_mode=mock
	WGHubEndpoint   string

	AlertWebhookURL string
	TrafficAlertGB  float64 // 每设备每月节点流量阈值（十进制 GB），默认 80

	AgentBundleDir string // agent 升级包目录（deploy 侧构建，容器内只读挂载），默认 /srv/bundles

	// InternalToken：/api/v1/internal/* 共享密钥（VPS 本机探测器 mgmt-rssh-probe 用）。
	// 空 = 内部路由整体禁用（fail-closed）。2026-09-24 阶段1C
	InternalToken string

	HeartbeatTimeout time.Duration // 默认 90s，测试可用 env 调小
	OfflineScanEvery time.Duration // ≤10s
	MetricsKeepDays  int
}

func Load() (*Config, error) {
	c := &Config{
		Env:              env("APP_ENV", "dev"),
		Listen:           env("LISTEN", "127.0.0.1:8443"),
		DatabaseURL:      os.Getenv("DATABASE_URL"),
		MigrateDBURL:     os.Getenv("MIGRATE_DATABASE_URL"),
		DataDir:          env("DATA_DIR", "./data"),
		BootstrapAdmin:   os.Getenv("BOOTSTRAP_ADMIN"),
		BootstrapPassword: os.Getenv("BOOTSTRAP_PASSWORD"),
		MgmtPublicURL:    env("MGMT_PUBLIC_URL", "https://10.10.0.1:8443"),
		WGHubPublicKey:   os.Getenv("WG_HUB_PUBLIC_KEY"),
		WGHubEndpoint:    os.Getenv("WG_HUB_ENDPOINT"),
		AlertWebhookURL:  os.Getenv("ALERT_WEBHOOK_URL"),
		TrafficAlertGB:   80,
		AgentBundleDir:   env("AGENT_BUNDLE_DIR", "/srv/bundles"),
		InternalToken:    os.Getenv("INTERNAL_TOKEN"),
		HeartbeatTimeout: 90 * time.Second,
		OfflineScanEvery: 10 * time.Second,
		MetricsKeepDays:  90,
	}
	if v := os.Getenv("TRAFFIC_ALERT_GB"); v != "" {
		f, err := strconv.ParseFloat(v, 64)
		if err != nil || f <= 0 {
			return nil, errors.New("TRAFFIC_ALERT_GB 必须为正数")
		}
		c.TrafficAlertGB = f
	}
	if v := os.Getenv("HEARTBEAT_TIMEOUT_SECONDS"); v != "" {
		n, err := strconv.Atoi(v)
		if err != nil || n < 2 {
			return nil, errors.New("HEARTBEAT_TIMEOUT_SECONDS 必须为 >=2 的整数")
		}
		c.HeartbeatTimeout = time.Duration(n) * time.Second
	}
	if err := c.loadSecrets(); err != nil {
		return nil, err
	}
	return c, nil
}

// loadSecrets 处理 JWT_SECRET / MASTER_KEY：生产缺失报错；开发缺失则持久化生成。
func (c *Config) loadSecrets() error {
	c.JWTSecret = os.Getenv("JWT_SECRET")
	c.MasterKeyHex = os.Getenv("MASTER_KEY")
	if err := os.MkdirAll(c.DataDir, 0o700); err != nil {
		return fmt.Errorf("创建 DATA_DIR: %w", err)
	}
	path := filepath.Join(c.DataDir, "secrets.json")
	needWrite := false
	var stored struct {
		JWTSecret   string `json:"jwt_secret"`
		MasterKeyHex string `json:"master_key_hex"`
	}
	if raw, err := os.ReadFile(path); err == nil {
		if err := json.Unmarshal(raw, &stored); err != nil {
			return fmt.Errorf("解析 %s: %w", path, err)
		}
		// 数据库已有数据时禁止静默重建密钥：secrets.json 存在但 env 为空时沿用旧值
	}
	if c.JWTSecret == "" {
		if stored.JWTSecret != "" {
			c.JWTSecret = stored.JWTSecret
		} else if c.Env == "prod" {
			return errors.New("生产模式缺少 JWT_SECRET")
		} else {
			b := make([]byte, 32)
			if _, err := rand.Read(b); err != nil {
				return err
			}
			c.JWTSecret = hex.EncodeToString(b)
			stored.JWTSecret = c.JWTSecret
			needWrite = true
		}
	}
	if c.MasterKeyHex == "" {
		if stored.MasterKeyHex != "" {
			c.MasterKeyHex = stored.MasterKeyHex
		} else if c.Env == "prod" {
			return errors.New("生产模式缺少 MASTER_KEY")
		} else {
			b := make([]byte, 32)
			if _, err := rand.Read(b); err != nil {
				return err
			}
			c.MasterKeyHex = hex.EncodeToString(b)
			stored.MasterKeyHex = c.MasterKeyHex
			needWrite = true
		}
	}
	if len(c.MasterKeyHex) != 64 {
		return errors.New("MASTER_KEY 必须是 64 个 hex 字符（32 字节）")
	}
	if _, err := hex.DecodeString(c.MasterKeyHex); err != nil {
		return errors.New("MASTER_KEY 不是合法 hex")
	}
	if needWrite {
		raw, _ := json.MarshalIndent(stored, "", "  ")
		if err := os.WriteFile(path, raw, 0o600); err != nil {
			return fmt.Errorf("写入开发密钥文件: %w", err)
		}
	}
	return nil
}

func env(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}
