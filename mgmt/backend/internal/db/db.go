// Package db 连接与版本化迁移（不依赖 GORM AutoMigrate 猜测生产结构，任务书 §19.7）。
package db

import (
	"embed"
	"errors"
	"fmt"
	"sort"
	"strconv"
	"strings"

	"gorm.io/driver/postgres"
	"gorm.io/gorm"
	"gorm.io/gorm/logger"
)

//go:embed migrations/*.sql
var migrationFS embed.FS

// Connect 打开 Postgres 连接（Warn 级：只打印出错 SQL，正常流量不输出）。
func Connect(dsn string) (*gorm.DB, error) {
	g, err := gorm.Open(postgres.Open(dsn), &gorm.Config{
		Logger: logger.Default.LogMode(logger.Warn),
	})
	if err != nil {
		return nil, err
	}
	return g, nil
}

// Migrate 按文件名顺序应用未执行的迁移，记录到 schema_migrations。
func Migrate(g *gorm.DB) error {
	if err := g.Exec(`CREATE TABLE IF NOT EXISTS schema_migrations (
		version BIGINT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())`).Error; err != nil {
		return fmt.Errorf("创建 schema_migrations: %w", err)
	}
	entries, err := migrationFS.ReadDir("migrations")
	if err != nil {
		return err
	}
	var names []string
	for _, e := range entries {
		if strings.HasSuffix(e.Name(), ".sql") {
			names = append(names, e.Name())
		}
	}
	sort.Strings(names)
	for _, name := range names {
		version, err := strconv.ParseInt(strings.SplitN(name, "_", 2)[0], 10, 64)
		if err != nil {
			return fmt.Errorf("迁移文件名不合法: %s", name)
		}
		var count int64
		if err := g.Raw(`SELECT COUNT(*) FROM schema_migrations WHERE version=?`, version).Scan(&count).Error; err != nil {
			return err
		}
		if count > 0 {
			continue
		}
		sqlBytes, err := migrationFS.ReadFile("migrations/" + name)
		if err != nil {
			return err
		}
		err = g.Transaction(func(tx *gorm.DB) error {
			// GRANT 语句不能进事务回滚也没关系；0002 在事务外执行亦可
			if err := tx.Exec(string(sqlBytes)).Error; err != nil {
				return err
			}
			return tx.Exec(`INSERT INTO schema_migrations(version) VALUES (?)`, version).Error
		})
		if err != nil {
			return fmt.Errorf("应用迁移 %s: %w", name, err)
		}
	}
	return nil
}

// EnsureCurrent 校验运行态库已达到最新迁移版本（serve 模式启动时调用）。
func EnsureCurrent(g *gorm.DB) error {
	var maxEmbedded int64
	entries, err := migrationFS.ReadDir("migrations")
	if err != nil {
		return err
	}
	for _, e := range entries {
		if v, err := strconv.ParseInt(strings.SplitN(e.Name(), "_", 2)[0], 10, 64); err == nil && v > maxEmbedded {
			maxEmbedded = v
		}
	}
	var applied int64
	if err := g.Raw(`SELECT COALESCE(MAX(version),0) FROM schema_migrations`).Scan(&applied).Error; err != nil {
		return errors.New("schema_migrations 不存在或不可读（请先运行迁移）")
	}
	if applied < maxEmbedded {
		return fmt.Errorf("数据库 schema 落后（applied=%d, latest=%d），请先运行 -migrate", applied, maxEmbedded)
	}
	return nil
}
