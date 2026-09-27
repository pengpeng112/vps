// routermgmt API 服务。
// 用法：
//   server            正常启动（要求 schema 已迁移到最新）
//   server -migrate   只执行数据库迁移（用 MIGRATE_DATABASE_URL 的迁移角色）
package main

import (
	"encoding/hex"
	"fmt"
	"log"
	"os"

	_ "time/tzdata" // Asia/Shanghai 月份口径（§19.5）

	"github.com/gin-gonic/gin"

	"routermgmt/internal/config"
	"routermgmt/internal/db"
	"routermgmt/internal/httpserver"
)

func main() {
	gin.SetMode(gin.ReleaseMode)
	cfg, err := config.Load()
	if err != nil {
		log.Fatalf("加载配置失败: %v", err)
	}
	masterKey, err := hex.DecodeString(cfg.MasterKeyHex)
	if err != nil || len(masterKey) != 32 {
		log.Fatal("MASTER_KEY 必须是 64 hex（32 字节）")
	}

	if len(os.Args) > 1 && os.Args[1] == "-migrate" {
		dsn := cfg.MigrateDBURL
		if dsn == "" {
			log.Fatal("-migrate 需要 MIGRATE_DATABASE_URL（迁移角色，区别于运行态账号）")
		}
		g, err := db.Connect(dsn)
		if err != nil {
			log.Fatalf("连接数据库失败: %v", err)
		}
		if err := db.Migrate(g); err != nil {
			log.Fatalf("迁移失败: %v", err)
		}
		log.Println("迁移完成")
		return
	}

	if cfg.DatabaseURL == "" {
		log.Fatal("缺少 DATABASE_URL")
	}
	g, err := db.Connect(cfg.DatabaseURL)
	if err != nil {
		log.Fatalf("连接数据库失败: %v", err)
	}
	if err := db.EnsureCurrent(g); err != nil {
		log.Fatalf("schema 校验失败: %v", err)
	}

	signKey, err := httpserver.NewEd25519SigningKey(cfg.DataDir)
	if err != nil {
		log.Fatalf("%v", err)
	}
	fmt.Println("签名公钥（Agent 引导包固定此值，勿从未验证的 meta 自动信任）：")
	fmt.Println(signKey.PublicPEM())

	srv := httpserver.New(cfg, g, masterKey, signKey)
	if err := srv.BootstrapAdmin(); err != nil {
		log.Fatalf("初始化管理员失败: %v", err)
	}
	if err := srv.Run(); err != nil {
		log.Fatalf("API 退出: %v", err)
	}
}
