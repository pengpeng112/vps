-- 0001: 初始 schema（与 internal/models 对应）
CREATE TABLE device_groups (
    id          BIGSERIAL PRIMARY KEY,
    name        VARCHAR(64) NOT NULL UNIQUE,
    description VARCHAR(255) NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE devices (
    id                       VARCHAR(32) PRIMARY KEY,
    name                     VARCHAR(128) NOT NULL DEFAULT '',
    group_id                 BIGINT REFERENCES device_groups(id),
    wg_ip                    VARCHAR(20) NOT NULL UNIQUE,
    wg_pubkey                VARCHAR(64) NOT NULL DEFAULT '',
    wg_priv_enc              TEXT NOT NULL DEFAULT '',
    agent_hmac_key_enc       TEXT NOT NULL DEFAULT '',
    sub_token_hash           VARCHAR(64) NOT NULL DEFAULT '',
    enroll_code_hash         VARCHAR(64) NOT NULL DEFAULT '',
    enroll_code_expires_at   TIMESTAMPTZ,
    hw_model                 VARCHAR(128) NOT NULL DEFAULT '',
    firmware_ver             VARCHAR(128) NOT NULL DEFAULT '',
    location_note            VARCHAR(255) NOT NULL DEFAULT '',
    lifecycle_status         VARCHAR(16) NOT NULL DEFAULT 'pending',
    last_heartbeat_at        TIMESTAMPTZ,
    desired_config_version   BIGINT NOT NULL DEFAULT 0,
    current_config_version   BIGINT NOT NULL DEFAULT 0,
    highest_accepted_version BIGINT NOT NULL DEFAULT 0,
    current_node_id          BIGINT,
    current_exit_ip          VARCHAR(64) NOT NULL DEFAULT '',
    rollback_streak          INT NOT NULL DEFAULT 0,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_devices_lifecycle ON devices(lifecycle_status);

CREATE TABLE nodes (
    id               BIGSERIAL PRIMARY KEY,
    name             VARCHAR(64) NOT NULL UNIQUE,
    host             VARCHAR(255) NOT NULL,
    port             INT NOT NULL,
    proto            VARCHAR(32) NOT NULL DEFAULT 'vless-reality',
    proto_params_enc TEXT NOT NULL DEFAULT '',
    priority         INT NOT NULL DEFAULT 0,
    enabled          BOOLEAN NOT NULL DEFAULT TRUE,
    health_status    VARCHAR(16) NOT NULL DEFAULT 'unknown',
    last_check_at    TIMESTAMPTZ,
    last_latency_ms  BIGINT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE node_assignments (
    device_id         VARCHAR(32) NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    node_id           BIGINT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    is_primary        BOOLEAN NOT NULL DEFAULT FALSE,
    failover_priority INT NOT NULL DEFAULT 0,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (device_id, node_id)
);
-- 每台设备最多一个 primary（数据库约束保证并发正确性）
CREATE UNIQUE INDEX idx_assignments_one_primary ON node_assignments(device_id) WHERE is_primary;

CREATE TABLE configs (
    id          BIGSERIAL PRIMARY KEY,
    device_id   VARCHAR(32) NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    kind        VARCHAR(32) NOT NULL DEFAULT 'clash_yaml',
    version     BIGINT NOT NULL,
    content_enc TEXT NOT NULL,
    sha256      VARCHAR(64) NOT NULL,
    signature   VARCHAR(256) NOT NULL,
    created_by  VARCHAR(128) NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (device_id, version)
);

CREATE TABLE tasks (
    id               VARCHAR(32) PRIMARY KEY,
    device_id        VARCHAR(32) NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    type             VARCHAR(32) NOT NULL,
    params_json      TEXT NOT NULL DEFAULT '{}',
    status           VARCHAR(16) NOT NULL DEFAULT 'pending',
    result_json      TEXT NOT NULL DEFAULT '',
    config_id        BIGINT,
    config_version   BIGINT,
    config_sha256    VARCHAR(64) NOT NULL DEFAULT '',
    lease_id         VARCHAR(64) NOT NULL DEFAULT '',
    lease_expires_at TIMESTAMPTZ,
    idempotency_key  VARCHAR(128),
    created_by       VARCHAR(128) NOT NULL DEFAULT '',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    claimed_at       TIMESTAMPTZ,
    executed_at      TIMESTAMPTZ,
    deadline         TIMESTAMPTZ NOT NULL
);
CREATE INDEX idx_tasks_device_status_created ON tasks(device_id, status, created_at);
-- 幂等键：同管理员+设备+key 唯一（§19.2）
CREATE UNIQUE INDEX idx_tasks_idempotency ON tasks(device_id, created_by, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE TABLE metrics (
    id            BIGSERIAL PRIMARY KEY,
    device_id     VARCHAR(32) NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    ts            TIMESTAMPTZ NOT NULL,
    cpu           DOUBLE PRECISION,
    mem_used_mb   BIGINT,
    mem_total_mb  BIGINT,
    temp_c        DOUBLE PRECISION,
    disk_free_mb  BIGINT,
    uptime_s      BIGINT,
    wan_ip        VARCHAR(64) NOT NULL DEFAULT '',
    exit_ip       VARCHAR(64) NOT NULL DEFAULT '',
    exit_node     VARCHAR(64) NOT NULL DEFAULT '',
    exit_latency_ms BIGINT,
    clash_alive   BOOLEAN,
    wg_alive      BOOLEAN,
    wan_rx_bytes  BIGINT,
    wan_tx_bytes  BIGINT,
    proxy_rx_bytes BIGINT,
    proxy_tx_bytes BIGINT,
    config_version BIGINT NOT NULL DEFAULT 0,
    sample_id     VARCHAR(64) NOT NULL,
    boot_id       VARCHAR(64) NOT NULL DEFAULT '',
    proxy_counter_epoch VARCHAR(80) NOT NULL DEFAULT '',
    proxy_measurement_status VARCHAR(16) NOT NULL DEFAULT 'unavailable'
);
CREATE INDEX idx_metrics_device_ts ON metrics(device_id, ts);
-- 心跳去重：(device_id, sample_id) 唯一（§19.5）
CREATE UNIQUE INDEX idx_metrics_device_sample ON metrics(device_id, sample_id);

CREATE TABLE monthly_traffic (
    id             BIGSERIAL PRIMARY KEY,
    device_id      VARCHAR(32) NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    year_month     VARCHAR(7) NOT NULL,
    wan_rx_bytes   BIGINT NOT NULL DEFAULT 0,
    wan_tx_bytes   BIGINT NOT NULL DEFAULT 0,
    proxy_rx_bytes BIGINT NOT NULL DEFAULT 0,
    proxy_tx_bytes BIGINT NOT NULL DEFAULT 0,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (device_id, year_month)
);

CREATE TABLE traffic_state (
    device_id       VARCHAR(32) PRIMARY KEY REFERENCES devices(id) ON DELETE CASCADE,
    boot_id         VARCHAR(64) NOT NULL DEFAULT '',
    counter_epoch   VARCHAR(80) NOT NULL DEFAULT '',
    wan_rx_bytes    BIGINT NOT NULL DEFAULT 0,
    wan_tx_bytes    BIGINT NOT NULL DEFAULT 0,
    proxy_rx_bytes  BIGINT NOT NULL DEFAULT 0,
    proxy_tx_bytes  BIGINT NOT NULL DEFAULT 0,
    has_baseline    BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE used_nonces (
    device_id  VARCHAR(32) NOT NULL,
    nonce      VARCHAR(32) NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (device_id, nonce)
);
CREATE INDEX idx_used_nonces_expires ON used_nonces(expires_at);

CREATE TABLE audit_log (
    id          BIGSERIAL PRIMARY KEY,
    actor_type  VARCHAR(16) NOT NULL,
    actor_id    VARCHAR(128) NOT NULL DEFAULT '',
    action      VARCHAR(64) NOT NULL,
    target      VARCHAR(128) NOT NULL DEFAULT '',
    detail_json TEXT NOT NULL DEFAULT '{}',
    ip          VARCHAR(64) NOT NULL DEFAULT '',
    ts          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_audit_ts ON audit_log(ts);

CREATE TABLE admin_users (
    id               BIGSERIAL PRIMARY KEY,
    username         VARCHAR(64) NOT NULL UNIQUE,
    pwd_hash         TEXT NOT NULL,
    totp_secret_enc  TEXT NOT NULL DEFAULT '',
    totp_bound       BOOLEAN NOT NULL DEFAULT FALSE,
    totp_last_period BIGINT NOT NULL DEFAULT 0,
    role             VARCHAR(32) NOT NULL DEFAULT 'admin',
    pwd_changed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE api_keys (
    id           BIGSERIAL PRIMARY KEY,
    name         VARCHAR(64) NOT NULL DEFAULT '',
    key_hash     VARCHAR(64) NOT NULL DEFAULT '',
    scope        VARCHAR(64) NOT NULL DEFAULT '',
    last_used_at TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE alerts (
    id         BIGSERIAL PRIMARY KEY,
    kind       VARCHAR(64) NOT NULL,
    device_id  VARCHAR(32),
    message    TEXT NOT NULL,
    level      VARCHAR(16) NOT NULL DEFAULT 'warning',
    dedup_key  VARCHAR(128) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    acked_at   TIMESTAMPTZ,
    acked_by   VARCHAR(128) NOT NULL DEFAULT ''
);
-- 去重：同一 dedup_key 只允许一条未确认告警（§19.5）
CREATE UNIQUE INDEX idx_alerts_dedup_active ON alerts(dedup_key) WHERE acked_at IS NULL;
CREATE INDEX idx_alerts_device_created ON alerts(device_id, created_at);

CREATE TABLE alert_deliveries (
    id            BIGSERIAL PRIMARY KEY,
    alert_id      BIGINT NOT NULL REFERENCES alerts(id) ON DELETE CASCADE,
    attempt       INT NOT NULL DEFAULT 0,
    status        VARCHAR(16) NOT NULL DEFAULT 'pending',
    last_error    VARCHAR(512) NOT NULL DEFAULT '',
    next_retry_at TIMESTAMPTZ,
    delivered_at  TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_alert_deliveries_alert ON alert_deliveries(alert_id);
