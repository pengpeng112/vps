package httpserver

// 设备策略：VPS 限速、定时重启、授权到期。
// 心跳每次带回完整 policy，Agent 与本地状态比对后落地（漏任务/重启也能自愈）。

import (
	"net"
	"regexp"
	"strings"
	"time"

	"github.com/gin-gonic/gin"

	"routermgmt/internal/models"
)

var (
	reIPv4 = regexp.MustCompile(`^((25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$`)
	reMAC  = regexp.MustCompile(`^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$`)
)

// 允许的定时重启 crontab（busybox cron）。禁止任意字符串，避免注入。
var allowedRebootCron = map[string]string{
	"":            "关闭",
	"0 0 * * *":   "每天 0 点",
	"0 8 * * *":   "每天 8 点",
	"0 */8 * * *": "每 8 小时",
	"0 */12 * * *": "每 12 小时",
}

func validRebootCron(s string) bool {
	_, ok := allowedRebootCron[s]
	return ok
}

func validKbps(n int) bool {
	return n >= 0 && n <= 1_000_000
}

func validProxyMode(s string) bool {
	switch s {
	case "rule", "global", "direct":
		return true
	default:
		return false
	}
}

func validAccessMode(s string) bool {
	switch s {
	case "all", "whitelist", "blacklist":
		return true
	default:
		return false
	}
}

// normalizeAccessList 把换行/逗号/分号名单收成逗号分隔的 IP 或 MAC，最多 32 条。
func normalizeAccessList(raw string) (string, error) {
	raw = strings.ReplaceAll(raw, ";", ",")
	raw = strings.ReplaceAll(raw, "\r", "\n")
	raw = strings.ReplaceAll(raw, "\n", ",")
	parts := strings.Split(raw, ",")
	seen := map[string]bool{}
	out := make([]string, 0, len(parts))
	for _, p := range parts {
		p = strings.TrimSpace(p)
		if p == "" {
			continue
		}
		if reMAC.MatchString(p) {
			p = strings.ToLower(p)
		} else if reIPv4.MatchString(p) {
			// keep
		} else {
			return "", errAccessItem
		}
		if seen[p] {
			continue
		}
		seen[p] = true
		out = append(out, p)
		if len(out) > 32 {
			return "", errAccessTooMany
		}
	}
	return strings.Join(out, ","), nil
}

var (
	errAccessItem    = errStr("access_list 只允许 IPv4 或 MAC（aa:bb:cc:dd:ee:ff），逗号/换行分隔")
	errAccessTooMany = errStr("access_list 最多 32 条")
	errAccessEmpty   = errStr("白名单不能为空（否则没有设备能走代理）")
)

type errStr string

func (e errStr) Error() string { return string(e) }

// deviceServiceActive 授权是否仍有效（人工关闭或过期都不让用 VPS）。
func deviceServiceActive(dev *models.Device, now time.Time) bool {
	if dev == nil || !dev.LicenseEnabled {
		return false
	}
	if dev.LicenseExpiresAt != nil && !dev.LicenseExpiresAt.After(now) {
		return false
	}
	return true
}

func (s *Server) devicePolicy(dev *models.Device) gin.H {
	vpsHost := ""
	if dev.CurrentNodeID != nil {
		var n models.Node
		if err := s.DB.First(&n, *dev.CurrentNodeID).Error; err == nil {
			if ip := net.ParseIP(n.Host); ip != nil && ip.To4() != nil {
				vpsHost = n.Host
			}
		}
	}
	var exp any
	if dev.LicenseExpiresAt != nil {
		exp = dev.LicenseExpiresAt.UTC().Format(time.RFC3339)
	} else {
		exp = nil
	}
	return gin.H{
		"service_active":     deviceServiceActive(dev, time.Now().UTC()),
		"vps_limit_kbps":     dev.VpsLimitKbps,
		"reboot_cron":        dev.RebootCron,
		"license_enabled":    dev.LicenseEnabled,
		"license_expires_at": exp,
		"vps_host":           vpsHost,
		"proxy_mode":         orDefault(dev.ProxyMode, "rule"),
		"access_mode":        orDefault(dev.AccessMode, "all"),
		"access_list":        dev.AccessList,
	}
}

func orDefault(s, d string) string {
	if s == "" {
		return d
	}
	return s
}

// licenseDaysLeft 剩余整天数；永久返回 nil；已过期为负数。
func licenseDaysLeft(dev *models.Device, now time.Time) *int {
	if dev == nil || dev.LicenseExpiresAt == nil {
		return nil
	}
	d := int(dev.LicenseExpiresAt.Sub(now).Hours() / 24)
	if dev.LicenseExpiresAt.After(now) && d == 0 {
		d = 1 // 不到 24h 也算还剩 1 天
	}
	return &d
}
