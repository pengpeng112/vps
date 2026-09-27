package httpserver

import (
	"crypto/subtle"
	"fmt"
	"log"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/golang-jwt/jwt/v5"
	"github.com/pquerna/otp"
	"github.com/pquerna/otp/totp"

	"routermgmt/internal/crypto"
	"routermgmt/internal/models"
)

const (
	ticketPurposeTOTPSetup  = "totp_setup"  // 首次绑定：仅可调 setup/confirm
	ticketPurposeTOTPVerify = "totp_verify" // 已绑定：换取完整 JWT
	jwtTTL                  = 12 * time.Hour
)

// ---- 一次性 ticket（内存，5 分钟，限用途）----

type ticket struct {
	UserID    int64
	Purpose   string
	ExpiresAt time.Time
}

type ticketStore struct {
	mu sync.Mutex
	m  map[string]*ticket
}

func newTicketStore() *ticketStore {
	return &ticketStore{m: map[string]*ticket{}}
}

func (t *ticketStore) issue(userID int64, purpose string) string {
	id, _ := crypto.GenerateHex(16)
	t.mu.Lock()
	defer t.mu.Unlock()
	// 顺带清理过期 ticket
	now := time.Now()
	for k, v := range t.m {
		if v.ExpiresAt.Before(now) {
			delete(t.m, k)
		}
	}
	t.m[id] = &ticket{UserID: userID, Purpose: purpose, ExpiresAt: now.Add(5 * time.Minute)}
	return id
}

// consume 校验并消费 ticket（一次性）。
func (t *ticketStore) consume(id, purpose string) (int64, bool) {
	t.mu.Lock()
	defer t.mu.Unlock()
	tk, ok := t.m[id]
	if !ok || tk.ExpiresAt.Before(time.Now()) || tk.Purpose != purpose {
		if ok {
			delete(t.m, id)
		}
		return 0, false
	}
	delete(t.m, id)
	return tk.UserID, true
}

// ---- 登录限流：同 IP 5 分钟 10 次 ----

type loginLimiter struct {
	mu sync.Mutex
	m  map[string][]time.Time
}

func newLoginLimiter() *loginLimiter { return &loginLimiter{m: map[string][]time.Time{}} }

func (l *loginLimiter) allow(ip string) bool {
	l.mu.Lock()
	defer l.mu.Unlock()
	now := time.Now()
	hits := l.m[ip][:0]
	for _, t := range l.m[ip] {
		if now.Sub(t) < 5*time.Minute {
			hits = append(hits, t)
		}
	}
	if len(hits) >= 10 {
		l.m[ip] = hits
		return false
	}
	hits = append(hits, now)
	l.m[ip] = hits
	return true
}

func clientIP(c *gin.Context) string {
	// WG 反代场景信任 X-Real-IP（web 容器同源反代会带原始地址）
	if v := c.GetHeader("X-Real-IP"); v != "" {
		return v
	}
	return c.ClientIP()
}

func (s *Server) loginRateLimit(c *gin.Context) {
	if !s.limiter.allow("login:" + clientIP(c)) {
		c.Header("Retry-After", "300")
		fail429(c, "登录尝试过于频繁，请 5 分钟后再试")
		return
	}
	c.Next()
}

func (s *Server) totpRateLimit(c *gin.Context) {
	if !s.limiter.allow("totp:" + clientIP(c)) {
		c.Header("Retry-After", "300")
		fail429(c, "验证码尝试过于频繁")
		return
	}
	c.Next()
}

// ---- 登录 / TOTP ----

type loginReq struct {
	Username string `json:"username" binding:"required"`
	Password string `json:"password" binding:"required"`
}

// handleLogin 密码验证通过后不发完整 JWT：
// 未绑 TOTP → 返回仅可用于绑定 TOTP 的 setup ticket；已绑 → 返回一次性 ticket 换 TOTP。
func (s *Server) handleLogin(c *gin.Context) {
	var req loginReq
	if err := c.ShouldBindJSON(&req); err != nil {
		fail422(c, "用户名或密码缺失")
		return
	}
	var user models.AdminUser
	if err := s.DB.Where("username = ?", req.Username).First(&user).Error; err != nil {
		s.audit(c, "admin", req.Username, "login_failed", "user", gin.H{"reason": "unknown_user"})
		fail401(c, "用户名或密码错误")
		return
	}
	ok, err := verifyArgon2(req.Password, user.PwdHash)
	if err != nil || !ok {
		s.audit(c, "admin", user.Username, "login_failed", fmt.Sprintf("admin:%d", user.ID), gin.H{"reason": "bad_password"})
		fail401(c, "用户名或密码错误")
		return
	}
	if !user.TOTPBound {
		ticket := s.tickets.issue(user.ID, ticketPurposeTOTPSetup)
		c.JSON(http.StatusOK, gin.H{"need_setup": true, "ticket": ticket})
		return
	}
	ticket := s.tickets.issue(user.ID, ticketPurposeTOTPVerify)
	c.JSON(http.StatusOK, gin.H{"need_totp": true, "ticket": ticket})
}

// handleTOTPSetup 仅凭 setup ticket：生成 otpauth URI（secret 加密存储）。
func (s *Server) handleTOTPSetup(c *gin.Context) {
	var req struct {
		Ticket string `json:"ticket" binding:"required"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		fail422(c, "ticket 缺失")
		return
	}
	uid, ok := s.tickets.peek(req.Ticket, ticketPurposeTOTPSetup)
	if !ok {
		fail401(c, "ticket 无效或已过期")
		return
	}
	var user models.AdminUser
	if err := s.DB.First(&user, uid).Error; err != nil {
		fail401(c, "用户不存在")
		return
	}
	if user.TOTPBound {
		fail409(c, "已绑定 TOTP，请走登录验证")
		return
	}
	key, err := totp.Generate(totp.GenerateOpts{Issuer: "router-mgmt", AccountName: user.Username, Period: 30, Digits: otp.DigitsSix})
	if err != nil {
		fail500(c, "生成 TOTP 失败")
		return
	}
	enc, err := crypto.EncryptString(s.MasterKey, key.Secret(), fmt.Sprintf("admin_users:%d:totp_secret", user.ID))
	if err != nil {
		fail500(c, "加密失败")
		return
	}
	if err := s.DB.Model(&user).Update("totp_secret_enc", enc).Error; err != nil {
		fail500(c, "保存失败")
		return
	}
	c.JSON(http.StatusOK, gin.H{"otpauth_url": key.URL(), "secret": key.Secret()})
}

// handleTOTPConfirm setup ticket + 验证码 → 完成绑定并发完整 JWT。
func (s *Server) handleTOTPConfirm(c *gin.Context) {
	var req struct {
		Ticket string `json:"ticket" binding:"required"`
		Code   string `json:"code" binding:"required"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		fail422(c, "ticket 或验证码缺失")
		return
	}
	uid, ok := s.tickets.peek(req.Ticket, ticketPurposeTOTPSetup)
	if !ok {
		fail401(c, "ticket 无效或已过期")
		return
	}
	if err := s.verifyTOTPAndConsume(c, uid, req.Code); err != nil {
		return // 已写响应
	}
	if err := s.DB.Model(&models.AdminUser{}).Where("id=?", uid).Update("totp_bound", true).Error; err != nil {
		fail500(c, "绑定失败")
		return
	}
	s.tickets.consume(req.Ticket, ticketPurposeTOTPSetup)
	var user models.AdminUser
	if err := s.DB.First(&user, uid).Error; err != nil {
		fail500(c, "加载用户失败")
		return
	}
	s.audit(c, "admin", user.Username, "totp_bound", fmt.Sprintf("admin:%d", user.ID), nil)
	c.JSON(http.StatusOK, gin.H{"token": s.issueJWT(&user), "username": user.Username, "role": user.Role})
}

// handleTOTPLogin 密码阶段的 ticket + 验证码 → 完整 JWT。
func (s *Server) handleTOTPLogin(c *gin.Context) {
	var req struct {
		Ticket string `json:"ticket" binding:"required"`
		Code   string `json:"code" binding:"required"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		fail422(c, "ticket 或验证码缺失")
		return
	}
	uid, ok := s.tickets.peek(req.Ticket, ticketPurposeTOTPVerify)
	if !ok {
		fail401(c, "ticket 无效、已过期或已使用")
		return
	}
	var user models.AdminUser
	if err := s.DB.First(&user, uid).Error; err != nil {
		fail401(c, "用户不存在")
		return
	}
	if err := s.verifyTOTPAndConsume(c, uid, req.Code); err != nil {
		return
	}
	if _, ok := s.tickets.consume(req.Ticket, ticketPurposeTOTPVerify); !ok {
		fail401(c, "ticket 无效、已过期或已使用")
		return
	}
	s.audit(c, "admin", user.Username, "login_ok", fmt.Sprintf("admin:%d", user.ID), nil)
	c.JSON(http.StatusOK, gin.H{"token": s.issueJWT(&user), "username": user.Username, "role": user.Role})
}

// verifyTOTPAndConsume 验证 TOTP 并记录已消费时间步（防止同一验证码重复登录，§19.4）。
func (s *Server) verifyTOTPAndConsume(c *gin.Context, userID int64, code string) error {
	var user models.AdminUser
	if err := s.DB.First(&user, userID).Error; err != nil {
		fail401(c, "用户不存在")
		return errNotFound
	}
	if user.TOTPSecretEnc == "" {
		fail409(c, "请先调用 totp/setup 生成密钥")
		return errNoSecret
	}
	secret, err := crypto.DecryptString(s.MasterKey, user.TOTPSecretEnc, fmt.Sprintf("admin_users:%d:totp_secret", user.ID))
	if err != nil {
		fail401(c, "验证码错误")
		return err
	}
	now := time.Now()
	curPeriod := now.Unix() / 30
	// 窗口 ±1，且时间步必须大于上次已消费的（防重放）
	var matched int64 = -1
	for _, p := range []int64{curPeriod - 1, curPeriod, curPeriod + 1} {
		if p <= user.TOTPLastPeriod {
			continue
		}
		expect, err := totp.GenerateCodeCustom(secret, time.Unix(p*30, 0), totp.ValidateOpts{Period: 30, Skew: 0, Digits: otp.DigitsSix, Algorithm: otp.AlgorithmSHA1})
		if err != nil {
			continue
		}
		if subtle.ConstantTimeCompare([]byte(expect), []byte(code)) == 1 {
			matched = p
			break
		}
	}
	if matched < 0 {
		s.audit(c, "admin", user.Username, "totp_failed", fmt.Sprintf("admin:%d", user.ID), nil)
		fail401(c, "验证码错误或已使用")
		return errBadCode
	}
	if err := s.DB.Model(&models.AdminUser{}).Where("id=?", user.ID).Update("totp_last_period", matched).Error; err != nil {
		fail500(c, "记录验证状态失败")
		return err
	}
	return nil
}

var (
	errNotFound  = fmt.Errorf("not found")
	errNoSecret  = fmt.Errorf("no totp secret")
	errBadCode   = fmt.Errorf("bad code")
)

// peek 只检查不消费（setup/confirm 两步共用同一 ticket）。
func (t *ticketStore) peek(id, purpose string) (int64, bool) {
	t.mu.Lock()
	defer t.mu.Unlock()
	tk, ok := t.m[id]
	if !ok || tk.ExpiresAt.Before(time.Now()) || tk.Purpose != purpose {
		return 0, false
	}
	return tk.UserID, true
}

// ---- JWT ----

func (s *Server) issueJWT(u *models.AdminUser) string {
	now := time.Now()
	claims := jwt.MapClaims{
		"sub": fmt.Sprintf("%d", u.ID),
		"un":  u.Username,
		"iat": now.Unix(),
		"exp": now.Add(jwtTTL).Unix(),
		"pwd": u.PwdChangedAt.Unix(), // 改密后旧会话全部失效
	}
	token := jwt.NewWithClaims(jwt.SigningMethodHS256, claims)
	t, _ := token.SignedString([]byte(s.Cfg.JWTSecret))
	return t
}

type adminCtx struct {
	UserID   int64
	Username string
}

func (s *Server) adminJWT(c *gin.Context) {
	h := c.GetHeader("Authorization")
	if !strings.HasPrefix(h, "Bearer ") {
		fail401(c, "缺少 Bearer token")
		return
	}
	raw := strings.TrimPrefix(h, "Bearer ")
	claims := jwt.MapClaims{}
	_, err := jwt.ParseWithClaims(raw, claims, func(t *jwt.Token) (any, error) {
		if _, ok := t.Method.(*jwt.SigningMethodHMAC); !ok {
			return nil, fmt.Errorf("非 HS256")
		}
		return []byte(s.Cfg.JWTSecret), nil
	}, jwt.WithValidMethods([]string{"HS256"}))
	if err != nil {
		fail401(c, "token 无效或已过期")
		return
	}
	sub, _ := claims["sub"].(string)
	var uid int64
	fmt.Sscanf(sub, "%d", &uid)
	var user models.AdminUser
	if err := s.DB.First(&user, uid).Error; err != nil {
		fail401(c, "用户不存在")
		return
	}
	iat, _ := claims["iat"].(float64)
	// 密码修改时间晚于签发时间 → 会话失效
	if int64(iat) < user.PwdChangedAt.Unix() {
		fail401(c, "会话已失效，请重新登录")
		return
	}
	c.Set("admin", adminCtx{UserID: user.ID, Username: user.Username})
	c.Next()
}

func adminFrom(c *gin.Context) adminCtx {
	v, _ := c.Get("admin")
	a, _ := v.(adminCtx)
	return a
}

// ---- 其它 ----

func (s *Server) handleMe(c *gin.Context) {
	a := adminFrom(c)
	var user models.AdminUser
	if err := s.DB.First(&user, a.UserID).Error; err != nil {
		fail404(c, "用户不存在")
		return
	}
	c.JSON(http.StatusOK, gin.H{"username": user.Username, "role": user.Role, "totp_bound": user.TOTPBound})
}

// handlePasswordChange 验证旧密码 + 已绑定 TOTP；修改后使旧会话失效（§9）。
func (s *Server) handlePasswordChange(c *gin.Context) {
	a := adminFrom(c)
	var req struct {
		OldPassword string `json:"old_password" binding:"required"`
		NewPassword string `json:"new_password" binding:"required"`
		TOTPCode    string `json:"totp_code" binding:"required"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		fail422(c, "旧密码、新密码与 TOTP 验证码必填")
		return
	}
	var user models.AdminUser
	if err := s.DB.First(&user, a.UserID).Error; err != nil {
		fail404(c, "用户不存在")
		return
	}
	if !user.TOTPBound {
		fail409(c, "请先绑定 TOTP")
		return
	}
	if ok, _ := verifyArgon2(req.OldPassword, user.PwdHash); !ok {
		fail401(c, "旧密码错误")
		return
	}
	if len(req.NewPassword) < 10 {
		fail422(c, "新密码长度不足 10 位")
		return
	}
	if err := s.verifyTOTPAndConsume(c, user.ID, req.TOTPCode); err != nil {
		return
	}
	hash, err := HashPassword(req.NewPassword)
	if err != nil {
		fail500(c, "哈希失败")
		return
	}
	if err := s.DB.Model(&user).Updates(map[string]any{
		"pwd_hash":       hash,
		"pwd_changed_at": time.Now().UTC(),
	}).Error; err != nil {
		fail500(c, "保存失败")
		return
	}
	s.audit(c, "admin", user.Username, "password_changed", fmt.Sprintf("admin:%d", user.ID), nil)
	c.JSON(http.StatusOK, gin.H{"ok": true, "message": "密码已修改，旧会话全部失效"})
}

// BootstrapAdmin 首次启动创建管理员（仅当无任何管理员）。
func (s *Server) BootstrapAdmin() error {
	var count int64
	if err := s.DB.Model(&models.AdminUser{}).Count(&count).Error; err != nil {
		return err
	}
	if count > 0 {
		return nil
	}
	if s.Cfg.BootstrapAdmin == "" || s.Cfg.BootstrapPassword == "" {
		return fmt.Errorf("首次启动需要 BOOTSTRAP_ADMIN 与 BOOTSTRAP_PASSWORD 环境变量创建管理员")
	}
	if len(s.Cfg.BootstrapPassword) < 12 {
		return fmt.Errorf("BOOTSTRAP_PASSWORD 至少 12 位")
	}
	hash, err := HashPassword(s.Cfg.BootstrapPassword)
	if err != nil {
		return err
	}
	u := models.AdminUser{Username: s.Cfg.BootstrapAdmin, PwdHash: hash, Role: "admin", PwdChangedAt: time.Now().UTC()}
	if err := s.DB.Create(&u).Error; err != nil {
		return err
	}
	log.Printf("已创建 bootstrap 管理员 %s（首次登录需绑定 TOTP）", u.Username)
	return nil
}
