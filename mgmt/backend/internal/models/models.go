// Package models GORM 模型，与 internal/db/migrations 中 SQL 一一对应。
// 注意：所有表名显式声明；API 序列化一律走 httpserver DTO 白名单，
// 禁止直接序列化本文件中的结构体（任务书 §19.4）。
package models

import "time"

type DeviceGroup struct {
	ID          int64     `gorm:"primaryKey"`
	Name        string    `gorm:"size:64;uniqueIndex"`
	Description string    `gorm:"size:255"`
	CreatedAt   time.Time
}

func (DeviceGroup) TableName() string { return "device_groups" }

type Device struct {
	ID                  string `gorm:"primaryKey;size:32"`
	Name                string `gorm:"size:128"`
	GroupID             *int64
	WGIp                string `gorm:"column:wg_ip;size:20;uniqueIndex"`
	WGPubkey            string `gorm:"column:wg_pubkey;size:64"`
	WGPrivEnc           string `gorm:"column:wg_priv_enc"`
	AgentHMACKeyEnc     string `gorm:"column:agent_hmac_key_enc"`
	SubTokenHash        string `gorm:"column:sub_token_hash;size:64"`
	EnrollCodeHash      string `gorm:"column:enroll_code_hash;size:64"`
	EnrollCodeExpiresAt *time.Time
	HWModel             string `gorm:"column:hw_model;size:128"`
	FirmwareVer         string `gorm:"column:firmware_ver;size:128"`
	// 设备标签/主 MAC（人工维护，识别物理设备用；2026-09-28）
	MacAddr string `gorm:"column:mac_addr;size:32"`
	LocationNote        string `gorm:"column:location_note;size:255"`
	// pending|approved|revoked；online/offline 由 last_heartbeat_at 派生
	LifecycleStatus string `gorm:"size:16;index"`
	// 运维状态
	LastHeartbeatAt     *time.Time
	DesiredConfigVersion int64 `gorm:"column:desired_config_version;default:0"`
	CurrentConfigVersion int64 `gorm:"column:current_config_version;default:0"`
	HighestAcceptedVersion int64 `gorm:"column:highest_accepted_version;default:0"`
	CurrentNodeID       *int64 `gorm:"column:current_node_id"`
	CurrentExitIP       string `gorm:"column:current_exit_ip;size:64"`
	RollbackStreak      int    `gorm:"column:rollback_streak;default:0"`
	// 策略：VPS 通道限速（kbps，0=不限）；国内直连不受影响
	VpsLimitKbps int `gorm:"column:vps_limit_kbps;default:0"`
	// busybox crontab 五行；空=关闭。只允许白名单表达式（见 policy.go）
	RebootCron string `gorm:"column:reboot_cron;size:64"`
	// 高流量预警：WAN 下行小时阈值（MB，0=关闭）；心跳时评估，超标进 alerts（每小时内去重）
	TrafficAlertHourlyMB int `gorm:"column:traffic_alert_hourly_mb;default:0"`
	// 授权：enabled=false 立即停用；expires_at 为空表示永不过期
	LicenseEnabled   bool       `gorm:"column:license_enabled;default:true"`
	LicenseExpiresAt *time.Time `gorm:"column:license_expires_at"`
	// rule=策略分流 / global=全局代理 / direct=全部直连
	ProxyMode string `gorm:"column:proxy_mode;size:16;default:rule"`
	// all=全部设备 / whitelist=仅名单走代理 / blacklist=名单不走代理
	AccessMode string `gorm:"column:access_mode;size:16;default:all"`
	AccessList string `gorm:"column:access_list;type:text"`
	// 第一次上线（首次心跳），之后不自动改；管理台可手改
	FirstOnlineAt *time.Time `gorm:"column:first_online_at"`
	// 本轮授权起算时间；续期时更新；管理台可手改
	LicenseStartedAt *time.Time `gorm:"column:license_started_at"`
	// VPS 侧 mgmt-rssh-probe 上报的排障口(-R 回环口)健康（2026-09-24 阶段1C）；
	// nil=从未检测，区别于 false；与 Agent 心跳(管理 -L 通道)相互独立
	RemoteSSHAlive       *bool      `gorm:"column:remote_ssh_alive"`
	LastRemoteSSHCheckAt *time.Time `gorm:"column:last_remote_ssh_check_at"`
	CreatedAt           time.Time
	UpdatedAt           time.Time
}

func (Device) TableName() string { return "devices" }

// Lifecycle 状态常量
const (
	LifecyclePending  = "pending"
	LifecycleApproved = "approved"
	LifecycleRevoked  = "revoked"
)

type Node struct {
	ID             int64 `gorm:"primaryKey"`
	Name           string `gorm:"size:64;uniqueIndex"`
	Host           string `gorm:"size:255"`
	Port           int
	Proto          string `gorm:"size:32;default:vless-reality"`
	Purpose        string `gorm:"column:purpose;size:16;default:ordinary"` // ordinary|video：video 仅入 US-MEDIA，渲染隔离
	ProtoParamsEnc string `gorm:"column:proto_params_enc"`
	Priority       int    `gorm:"default:0"`
	Enabled        bool   `gorm:"default:true"`
	// unknown|up|down
	HealthStatus   string `gorm:"size:16;default:unknown"`
	LastCheckAt    *time.Time
	LastLatencyMs  *int64
	CreatedAt      time.Time
}

func (Node) TableName() string { return "nodes" }

type NodeAssignment struct {
	DeviceID         string `gorm:"primaryKey;size:32"`
	NodeID           int64  `gorm:"primaryKey"`
	IsPrimary        bool   `gorm:"column:is_primary"`
	FailoverPriority int    `gorm:"column:failover_priority;default:0"`
	CreatedAt        time.Time
	UpdatedAt        time.Time
}

func (NodeAssignment) TableName() string { return "node_assignments" }

type Config struct {
	ID         int64 `gorm:"primaryKey"`
	DeviceID   string `gorm:"size:32;uniqueIndex:idx_configs_device_version"`
	Kind       string `gorm:"size:32;default:clash_yaml"`
	Version    int64 `gorm:"uniqueIndex:idx_configs_device_version"`
	ContentEnc string `gorm:"column:content_enc"`
	SHA256     string `gorm:"column:sha256;size:64"`
	Signature  string `gorm:"size:256"`
	CreatedBy  string `gorm:"size:128"`
	CreatedAt  time.Time
}

func (Config) TableName() string { return "configs" }

// 任务状态机（§19.2）：pending → running → ok|failed|rolled_back；
// 未领取超期 expired；设备吊销未执行 cancelled；结果不明 unknown。
const (
	TaskPending    = "pending"
	TaskRunning    = "running"
	TaskOK         = "ok"
	TaskFailed     = "failed"
	TaskRolledBack = "rolled_back"
	TaskExpired    = "expired"
	TaskCancelled  = "cancelled"
	TaskUnknown    = "unknown"
)

type Task struct {
	ID            string `gorm:"primaryKey;size:32"`
	DeviceID      string `gorm:"size:32;index:idx_tasks_device_status_created"`
	Type          string `gorm:"size:32"`
	ParamsJSON    string `gorm:"column:params_json"`
	Status        string `gorm:"size:16;index:idx_tasks_device_status_created"`
	ResultJSON    string `gorm:"column:result_json"`
	ConfigID      *int64 `gorm:"column:config_id"`
	ConfigVersion *int64 `gorm:"column:config_version"`
	ConfigSHA256  string `gorm:"column:config_sha256;size:64"`
	LeaseID       string `gorm:"column:lease_id;size:64"`
	LeaseExpiresAt *time.Time `gorm:"column:lease_expires_at"`
	IdempotencyKey string `gorm:"column:idempotency_key;size:128"`
	CreatedBy     string `gorm:"column:created_by;size:128"`
	CreatedAt     time.Time `gorm:"index:idx_tasks_device_status_created"`
	ClaimedAt     *time.Time `gorm:"column:claimed_at"`
	ExecutedAt    *time.Time `gorm:"column:executed_at"`
	Deadline      time.Time
}

func (Task) TableName() string { return "tasks" }

type Metric struct {
	ID           int64 `gorm:"primaryKey"`
	DeviceID     string `gorm:"size:32;index:idx_metrics_device_ts;uniqueIndex:idx_metrics_device_sample"`
	TS           time.Time `gorm:"column:ts;index:idx_metrics_device_ts"`
	CPU          *float64 `gorm:"column:cpu"`
	MemUsedMB    *int64   `gorm:"column:mem_used_mb"`
	MemTotalMB   *int64   `gorm:"column:mem_total_mb"`
	TempC        *float64 `gorm:"column:temp_c"`
	DiskFreeMB   *int64   `gorm:"column:disk_free_mb"`
	UptimeS      *int64   `gorm:"column:uptime_s"`
	WanIP        string   `gorm:"column:wan_ip;size:64"`
	ExitIP       string   `gorm:"column:exit_ip;size:64"`
	ExitNode     string   `gorm:"column:exit_node;size:64"`
	ExitLatencyMs *int64  `gorm:"column:exit_latency_ms"`
	ClashAlive   *bool    `gorm:"column:clash_alive"`
	WGAlive      *bool    `gorm:"column:wg_alive"`
	WanRxBytes   *int64   `gorm:"column:wan_rx_bytes"`
	WanTxBytes   *int64   `gorm:"column:wan_tx_bytes"`
	ProxyRxBytes *int64   `gorm:"column:proxy_rx_bytes"`
	ProxyTxBytes *int64   `gorm:"column:proxy_tx_bytes"`
	ConfigVersion int64   `gorm:"column:config_version"`
	SampleID     string   `gorm:"column:sample_id;size:64;uniqueIndex:idx_metrics_device_sample"`
	BootID       string   `gorm:"column:boot_id;size:64"`
	ProxyCounterEpoch string `gorm:"column:proxy_counter_epoch;size:80"`
	// available|unavailable|partial
	ProxyMeasurementStatus string `gorm:"column:proxy_measurement_status;size:16;default:unavailable"`
}

func (Metric) TableName() string { return "metrics" }

type MonthlyTraffic struct {
	ID         int64  `gorm:"primaryKey"`
	DeviceID   string `gorm:"size:32;uniqueIndex:idx_monthly_device_ym"`
	YearMonth  string `gorm:"size:7;uniqueIndex:idx_monthly_device_ym"` // Asia/Shanghai 的 YYYY-MM
	WanRxBytes int64  `gorm:"column:wan_rx_bytes;default:0"`
	WanTxBytes int64  `gorm:"column:wan_tx_bytes;default:0"`
	ProxyRxBytes int64 `gorm:"column:proxy_rx_bytes;default:0"`
	ProxyTxBytes int64 `gorm:"column:proxy_tx_bytes;default:0"`
	UpdatedAt  time.Time
}

func (MonthlyTraffic) TableName() string { return "monthly_traffic" }

type TrafficState struct {
	DeviceID      string `gorm:"primaryKey;size:32"`
	BootID        string `gorm:"column:boot_id;size:64"`
	CounterEpoch  string `gorm:"column:counter_epoch;size:80"`
	WanRxBytes    int64  `gorm:"column:wan_rx_bytes"`
	WanTxBytes    int64  `gorm:"column:wan_tx_bytes"`
	ProxyRxBytes  int64  `gorm:"column:proxy_rx_bytes"`
	ProxyTxBytes  int64  `gorm:"column:proxy_tx_bytes"`
	HasBaseline   bool   `gorm:"column:has_baseline;default:false"`
	UpdatedAt     time.Time
}

func (TrafficState) TableName() string { return "traffic_state" }

type UsedNonce struct {
	DeviceID  string    `gorm:"primaryKey;size:32"`
	Nonce     string    `gorm:"primaryKey;size:32"`
	ExpiresAt time.Time `gorm:"index"`
}

func (UsedNonce) TableName() string { return "used_nonces" }

type AuditLog struct {
	ID         int64  `gorm:"primaryKey"`
	ActorType  string `gorm:"column:actor_type;size:16"` // admin|device|system
	ActorID    string `gorm:"column:actor_id;size:128"`
	Action     string `gorm:"column:action;size:64"`
	Target     string `gorm:"column:target;size:128"`
	DetailJSON string `gorm:"column:detail_json"`
	IP         string `gorm:"column:ip;size:64"`
	TS         time.Time `gorm:"column:ts;index"`
}

func (AuditLog) TableName() string { return "audit_log" }

type AdminUser struct {
	ID            int64  `gorm:"primaryKey"`
	Username      string `gorm:"size:64;uniqueIndex"`
	PwdHash       string `gorm:"column:pwd_hash"`
	TOTPSecretEnc string `gorm:"column:totp_secret_enc"`
	TOTPBound     bool   `gorm:"column:totp_bound;default:false"`
	TOTPLastPeriod int64 `gorm:"column:totp_last_period;default:0"`
	Role          string `gorm:"column:role;size:32;default:admin"`
	PwdChangedAt  time.Time `gorm:"column:pwd_changed_at"`
	CreatedAt     time.Time
}

func (AdminUser) TableName() string { return "admin_users" }

type APIKey struct {
	ID         int64  `gorm:"primaryKey"`
	Name       string `gorm:"size:64"`
	KeyHash    string `gorm:"column:key_hash;size:64"`
	Scope      string `gorm:"column:scope;size:64"`
	LastUsedAt *time.Time `gorm:"column:last_used_at"`
	CreatedAt  time.Time
}

func (APIKey) TableName() string { return "api_keys" }

type Alert struct {
	ID        int64  `gorm:"primaryKey"`
	Kind      string `gorm:"column:kind;size:64"`
	DeviceID  *string `gorm:"column:device_id;size:32;index:idx_alerts_device_created"`
	Message   string `gorm:"column:message"`
	Level     string `gorm:"column:level;size:16"` // info|warning|critical
	DedupKey  string `gorm:"column:dedup_key;size:128"`
	CreatedAt time.Time `gorm:"index:idx_alerts_device_created"`
	AckedAt   *time.Time `gorm:"column:acked_at"`
	AckedBy   string   `gorm:"column:acked_by;size:128"`
}

func (Alert) TableName() string { return "alerts" }

type AlertDelivery struct {
	ID          int64  `gorm:"primaryKey"`
	AlertID     int64  `gorm:"column:alert_id;index"`
	Attempt     int    `gorm:"default:0"`
	Status      string `gorm:"column:status;size:16"` // pending|delivered|failed
	LastError   string `gorm:"column:last_error;size:512"`
	NextRetryAt *time.Time `gorm:"column:next_retry_at"`
	DeliveredAt *time.Time `gorm:"column:delivered_at"`
	CreatedAt   time.Time
}

func (AlertDelivery) TableName() string { return "alert_deliveries" }

type SchemaMigration struct {
	Version    int64 `gorm:"primaryKey"`
	AppliedAt  time.Time `gorm:"column:applied_at"`
}

func (SchemaMigration) TableName() string { return "schema_migrations" }
