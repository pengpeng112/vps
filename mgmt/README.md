# 路由器管理台（mgmt）

二三十台 OpenWrt/iStoreOS 路由器的后端管理系统：远程控制、重启、在线监控、改连接配置、多 VPS 节点切换。管理通道（WireGuard+HMAC）与代理通道（VLESS+Reality）物理分离。

- 新会话先读根目录 [工作入口](../AGENTS.md) 和 [当前状态](../docs/current/当前状态.md)。
- 架构与协议参考 [原开发设计](../docs/reference/路由器管理系统-开发执行计划.md)，实际实现以源码与验收为证。
- 本系统已有生产运行与真机验收记录；原“Session 2 未开始”摘要已过期。[开发进度](docs/开发进度.md) 已指向统一状态及后续任务。
- 以下启动、测试、部署命令仅在对应开发或变更任务中使用。数据库只读探索不得启动应用、运行迁移、测试或 compose up；运行态后台可能写库。

## 目录

```
mgmt/
  backend/    Go API（Gin+GORM+Postgres16，单二进制）
    cmd/server       API 服务（-migrate 只跑迁移）
    cmd/provision    离线引导包 CLI
    cmd/jsonfilter   OpenWrt jsonfilter 兼容工具（仅测试环境用，真机用 opkg 版）
    internal/        config/db/models/crypto/clash/httpserver/traffic/alerts
  frontend/   Vue3 + TS + Naive UI + ECharts（中文，深色运维风）
  agent/      busybox ash Agent（主循环 60s 心跳 + 10s 任务轮询；switch_node 带备份/看门狗/回滚/崩溃恢复）
  deploy/     docker-compose.yml（dev）+ docker-compose.prod.yml（TLS 覆盖）+ .env.example + db-init.sh
  docs/       openapi.yaml / 验收记录.md / 开发进度.md
  scripts/    mock-agent.sh / totp.py（验收工具）
```

## 快速启动（开发）

前置：Docker Desktop（含 compose）、可选 Go 1.26 与 Node 22（跑测试/前端开发）。

### PowerShell

```powershell
cd F:\vps\mgmt\deploy
copy .env.example .env
# 生成随机值并填入 .env（必填，无默认弱密码）：
#   JWT_SECRET：-join ((1..64) | ForEach-Object { '{0:x}' -f (Get-Random -Max 16) })
#   MASTER_KEY：必须 64 个 hex 字符 →  [regex]::Replace(-join((1..64)|%{'{0:x}'-f(Get-Random -Max 16)}),'.','') 之类
#   三个数据库密码 / BOOTSTRAP_PASSWORD 自定
docker compose up --build -d
# 等待就绪后打开
start http://127.0.0.1:8080
```

### POSIX shell（Git Bash / WSL）

```sh
cd /f/vps/mgmt/deploy
cp .env.example .env
# 生成：openssl rand -hex 32（JWT_SECRET、MASTER_KEY、三个库密码同理）
vi .env   # 本地跑 alpine e2e 时建议 MGMT_PUBLIC_URL=http://api:8443（容器网络内 Agent 下载配置用）
docker compose up --build -d
curl http://127.0.0.1:8080/api/v1/health   # {"status":"ok","db":true}
```

首次登录：用 `.env` 的 `BOOTSTRAP_ADMIN`/`BOOTSTRAP_PASSWORD` 登录 → 强制绑定 TOTP（显示 otpauth 与密钥）→ 输入验证码进入。bootstrap 账号只在库为空时创建。

### 首次体验闭环（验收演示步骤）

```sh
# 1. 浏览器创建设备（设备页 → 创建设备），复制 enroll_code；或：
TOKEN=<登录拿到的 JWT>
curl -X POST http://127.0.0.1:8080/api/v1/devices -H "Authorization: Bearer $TOKEN" \
     -H 'Content-Type: application/json' -d '{"name":"演示设备"}'
# 2. 代设备 enroll（provision CLI；也可在浏览器里用返回码手工 curl）
cd ../backend && go build -o /tmp/provision ./cmd/provision
/tmp/provision -server http://127.0.0.1:8080 -enroll-code <code> -out /tmp/pkg.json
# 3. 建节点（虚构测试节点）→ 设备详情绑定并发布配置
# 4. mock 设备心跳（宿主机 Git Bash）：
cd ../scripts && sh mock-agent.sh /tmp/pkg.json 3
# 5. Dashboard 看在线；停 90s+ 自动离线并告警
# 6. alpine 容器跑真实 Agent 事务（成功/回滚/崩溃恢复）：
cd ../agent/tests && sh run-e2e-alpine.sh
```

## 测试

```sh
# 后端单测 + Postgres 集成测试（自动起临时库容器见下）
docker run -d --name mgmt-test-pg -e POSTGRES_USER=mgmt -e POSTGRES_PASSWORD=m \
  -e POSTGRES_DB=mgmt_test -p 127.0.0.1:55432:5432 postgres:16-alpine
cd backend
ROUTERMGMT_TEST_DSN='postgres://mgmt:m@127.0.0.1:55432/mgmt_test?sslmode=disable' go test ./... -count=1
# 未设置 DSN 时集成测试自动跳过，单测（黄金规则/HMAC 向量/加密/签名）照常运行

# 前端类型检查与构建
cd frontend && npm install && npm run build

# Agent e2e（需 compose 栈运行中；alpine + 假适配器）
cd agent/tests && sh run-e2e-alpine.sh     # 15 项断言（向量/成功/回滚/崩溃恢复/在线）
```

## 生产部署参考（执行前核对现网与完整 prod overlay）

```sh
cd deploy
# 证书（含 IP SAN）：
mkdir -p prod-certs && openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes \
  -keyout prod-certs/privkey.pem -out prod-certs/fullchain.pem -days 825 \
  -subj "/CN=10.10.0.1" -addext "subjectAltName=IP:10.10.0.1"
# 用 compose override【替换】开发端口映射，绑定 WG 内网 10.10.0.1:8443 终止 TLS：
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

要点：API/DB 不发布宿主机端口；`web` 仅绑 `10.10.0.1:8443`；WG 接口建立后再启动；`.env` 设 `APP_ENV=prod`（密钥缺失直接拒绝启动）。

## 数据库迁移 / 停止 / 备份

```sh
docker compose run --rm api -migrate        # 单独执行迁移（mgmt_migrate 角色）
docker compose down                         # 停止（保留数据）
docker compose down -v                      # 停止并清空（危险：含密钥与凭据）
# 备份（含加密数据的解密材料 = MASTER_KEY/JWT_SECRET/签名私钥，须一起保存）：
docker compose exec -T db pg_dump -U postgres routermgmt > backup-$(date +%F).sql
# 恢复：psql 导入 + 保持同一 DATA_DIR 卷（签名公钥不变，设备凭据仍可验签）
```

## 密钥与安全要点

- `MASTER_KEY`（64 hex）AES-256-GCM 信封加密 HMAC key / WG 私钥 / 节点参数 / 配置快照 / TOTP secret，AAD 绑定记录。
- `JWT_SECRET` 独立；ed25519 签名私钥持久化在 DATA_DIR 卷，容器重建不失效。
- 订阅 token / enroll_code 只存 sha256；明文凭据只在创建响应出现一次。
- Agent HMAC：canonical `device_id\nts\nnonce\nMETHOD\npath\nsha256(body)`，key 为 64 hex ASCII 字节，±120s 窗口，nonce 持久化防重放（Go/ash 固定向量互通）。
- 运行态库账号对 audit_log 仅 INSERT/SELECT；审计无删除 API。
- 日志/前端/错误信息不输出 uuid/pbk/sid 全文（掩码：前后 4 / 前后 2）。

## 已知边界（Session 1）

- 无真 WireGuard Hub（`transport_mode=mock`，Agent 直连 API）；生产 API 只应存在于 WG 内网。
- 节点 host 仅 IPv4 字面量（域名/IPv6 需先实现解析与直连规则）。
- 出口探测依赖外部服务（cloudflare trace），Agent 侧可配置；探测失败=回滚。
- iStoreOS 真机未刷成，Agent 相关验收基于 alpine（busybox ash）容器 + 假适配器；真机项见开发进度「待真机验收」。
