// Package httpserver 管理台 REST API + Agent 接口 + 订阅接口。
// 错误统一 {code,message,request_id}；列表统一 {items,total,page,page_size}（§19.7）。
package httpserver

import (
	"context"
	"log"
	"net/http"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
	"gorm.io/gorm"

	"routermgmt/internal/alerts"
	"routermgmt/internal/config"
	"routermgmt/internal/db"
	"routermgmt/internal/traffic"
)

type Server struct {
	Cfg       *config.Config
	DB        *gorm.DB
	MasterKey []byte
	SignPubPEM string

	tickets *ticketStore
	limiter *loginLimiter
	traffic *traffic.Service
	alerts  *alerts.Dispatcher

	signing SigningKey

	shutdown func()
}

// SigningKey 抽象 ed25519 私钥（便于测试注入）。
type SigningKey interface {
	Sign(yamlBytes []byte, deviceID string, version int64) (string, error)
	PublicPEM() string
}

func New(cfg *config.Config, g *gorm.DB, masterKey []byte, sign SigningKey) *Server {
	s := &Server{
		Cfg:       cfg,
		DB:        g,
		MasterKey: masterKey,
		signing:   sign,
		SignPubPEM: sign.PublicPEM(),
		tickets:   newTicketStore(),
		limiter:   newLoginLimiter(),
		traffic:   traffic.New(),
		alerts:    alerts.New(g, cfg.AlertWebhookURL),
	}
	return s
}

// Run 启动 HTTP 服务与后台任务（离线扫描、超期清理、指标保留、webhook 投递）。
func (s *Server) Run() error {
	r := s.BuildEngine()

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	s.shutdown = cancel
	go s.sweeperLoop(ctx)
	go s.alerts.Worker(ctx)

	srv := &http.Server{Addr: s.Cfg.Listen, Handler: r, ReadHeaderTimeout: 10 * time.Second}
	log.Printf("API 监听 %s (env=%s)", s.Cfg.Listen, s.Cfg.Env)
	return srv.ListenAndServe()
}

// BuildEngine 构建完整路由（测试用 httptest 直接驱动）。
func (s *Server) BuildEngine() *gin.Engine {
	r := gin.New()
	_ = r.SetTrustedProxies(nil) // 不信任任意 X-Forwarded-For；反代用 X-Real-IP
	r.Use(gin.Recovery(), requestID(), maxBody(1<<20), accessLog())
	s.registerRoutes(r)
	return r
}

func (s *Server) Stop() {
	if s.shutdown != nil {
		s.shutdown()
	}
}

func (s *Server) registerRoutes(r *gin.Engine) {
	r.GET("/api/v1/health", s.health)
	r.GET("/api/v1/meta", s.meta)

	auth := r.Group("/api/v1/auth")
	{
		auth.POST("/login", s.loginRateLimit, s.handleLogin)
		auth.POST("/totp", s.totpRateLimit, s.handleTOTPLogin)
		auth.POST("/totp/setup", s.totpRateLimit, s.handleTOTPSetup)
		auth.POST("/totp/confirm", s.totpRateLimit, s.handleTOTPConfirm)
		auth.GET("/me", s.adminJWT, s.handleMe)
		auth.POST("/password", s.adminJWT, s.handlePasswordChange)
	}

	agent := r.Group("/api/v1/agent", s.deviceHMAC)
	{
		agent.POST("/heartbeat", s.handleHeartbeat)
		agent.GET("/tasks", s.handleAgentTasks)
		agent.POST("/tasks/:id/result", s.handleAgentTaskResult)
		agent.GET("/configs/:version", s.handleAgentConfig)
		agent.GET("/bundles/:version", s.handleAgentBundle)
	}

	// VPS 本机探测器专用内部路由（X-Internal-Token；未配置 INTERNAL_TOKEN 时 404）
	internal := r.Group("/api/v1/internal", s.internalToken)
	{
		internal.POST("/tunnel-health", s.handleTunnelHealthReport)
	}

	r.GET("/sub/:id", s.handleSubscription)

	// 设备 enroll：无 JWT、无 HMAC，凭一次性 enroll_code（§7.2）
	r.POST("/api/v1/devices/enroll", s.handleEnroll)

	api := r.Group("/api/v1", s.adminJWT)
	{
		api.POST("/devices", s.handleDeviceCreate)
		api.GET("/devices", s.handleDeviceList)
		api.GET("/devices/:id", s.handleDeviceGet)
		api.PATCH("/devices/:id", s.handleDevicePatch)
		api.POST("/devices/:id/revoke", s.handleDeviceRevoke)
		api.POST("/devices/:id/rotate", s.handleDeviceRotate)
		api.POST("/devices/:id/enroll-reset", s.handleEnrollReset)
		api.POST("/devices/:id/ops", s.handleDeviceOps)
		api.GET("/devices/:id/metrics", s.handleDeviceMetrics)
		api.GET("/devices/:id/config", s.handleDeviceConfigMasked)
		api.POST("/devices/:id/configs/preview", s.handleConfigPreview)
		api.POST("/devices/:id/configs/apply", s.handleConfigApply)

		api.GET("/groups", s.handleGroupList)
		api.POST("/groups", s.handleGroupCreate)

		api.GET("/nodes", s.handleNodeList)
		api.POST("/nodes", s.handleNodeCreate)
		api.PATCH("/nodes/:id", s.handleNodePatch)
		api.DELETE("/nodes/:id", s.handleNodeDelete)
		api.POST("/nodes/:id/probe", s.handleNodeProbe)

		api.POST("/assignments/batch", s.handleAssignmentsBatch)

		api.GET("/tasks", s.handleTaskList)
		api.GET("/alerts", s.handleAlertList)
		api.PATCH("/alerts/:id", s.handleAlertAck)
		api.GET("/audit", s.handleAuditList)
		api.GET("/dashboard", s.handleDashboard)
	}
}

func (s *Server) health(c *gin.Context) {
	ok := true
	if sqlDB, err := s.DB.DB(); err == nil {
		ok = sqlDB.Ping() == nil
	}
	if !ok {
		c.JSON(http.StatusServiceUnavailable, gin.H{"status": "degraded", "db": false})
		return
	}
	c.JSON(http.StatusOK, gin.H{"status": "ok", "db": true})
}

type metaResp struct {
	SignPublicKey string `json:"sign_public_key"`
	TransportMode string `json:"transport_mode"` // wireguard | mock
	WGHubPublicKey string `json:"wg_hub_public_key,omitempty"`
	WGHubEndpoint  string `json:"wg_hub_endpoint,omitempty"`
	MgmtPublicURL  string `json:"mgmt_public_url"`
}

func (s *Server) meta(c *gin.Context) {
	mode := "wireguard"
	if s.Cfg.WGHubPublicKey == "" || s.Cfg.WGHubEndpoint == "" {
		mode = "mock"
	}
	c.JSON(http.StatusOK, metaResp{
		SignPublicKey: s.SignPubPEM,
		TransportMode: mode,
		WGHubPublicKey: s.Cfg.WGHubPublicKey,
		WGHubEndpoint:  s.Cfg.WGHubEndpoint,
		MgmtPublicURL:  s.Cfg.MgmtPublicURL,
	})
}

// ---- 中间件 ----

func requestID() gin.HandlerFunc {
	return func(c *gin.Context) {
		id := uuid.NewString()[:8]
		c.Set("request_id", id)
		c.Header("X-Request-Id", id)
		c.Next()
	}
}

func maxBody(n int64) gin.HandlerFunc {
	return func(c *gin.Context) {
		c.Request.Body = http.MaxBytesReader(c.Writer, c.Request.Body, n)
		c.Next()
	}
}

func accessLog() gin.HandlerFunc {
	return func(c *gin.Context) {
		start := time.Now()
		c.Next()
		// 不打印 URL query（订阅 token、票据不出现在日志）
		log.Printf("%s %s -> %d (%s) rid=%s",
			c.Request.Method, c.Request.URL.Path, c.Writer.Status(), time.Since(start).Round(time.Millisecond), c.GetString("request_id"))
	}
}

// EnsureDBReadable 启动时校验 schema 版本。
func EnsureDBReadable(g *gorm.DB) error { return db.EnsureCurrent(g) }
