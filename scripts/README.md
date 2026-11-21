# 运维脚本索引（2026-11-21 更新）

管理主台为**广州（134.175.151.213:28722）**；历史与已完成的一次性脚本位于 `scripts/archive/`（映射见 `docs/archive/*-manifest.md`）。名称含 `test`/`diag`/`probe` 不天然只读，运行前先读入口与凭据来源。

## 现役核心脚本

| 类别 | 文件 | 说明 |
|---|---|---|
| **多 AI 协作与构建门禁** | `session_guard.py`、`build_gz_api.py`、`ci-local.ps1` | 会话资源锁（开工必跑 `status`）、广州后端隔离校验/构建、本地检查门 |
| **设备接入与刷机** | `flash-snr.py`、`flash-batch.py`、`glinet-onboard.py`、`glinet-mihomo-ipt.sh`、`deploy-parallel-tunnel.py`、`fleet-netprobe-install.py`、`point-check.py` | SN-R 自动化刷机依赖 `firmware/openclash/{mihomo-linux-arm64,geosite.dat,country.mmdb}` 与 `firmware/snr1-openssl3/` |
| **广州维护与巡检** | `gz-console-tunnel.py`、`gz-rssh-probe.py`、`gz-safe-maintenance.py`、`push-via-gz.py`、`fleet-inspect-device-v1.sh`、`fleet-inspect-gz-v1.sh`、`fleet-md-device.sh` | 经广州排灾口安全运维与只读巡检 |
| **R2 计量与限速工具链** | `scripts/qos-r2/`（`proxy-meter-v3.sh`、`proxy-meter-io.py`、`proxy-meter-endpoints.sh`、`proxy-meter-lifecycle.init`、`ratelimit-ctl-v2.sh` 等） | 现行限速路线为 `mgmt/qos-gate/`；`ratelimit-server-executor.sh` 已标 `DEPRECATED`（因审计脚本引用暂留原位） |
| **现场应急恢复包** | `scripts/emergency-kit/`（含 `fix-router*.bat`、`fix-cmds.txt`、`plink.exe`） | 站点失联时经向日葵应急恢复 |
| **管理台 API / 注册 / 分流** | `mgmt_api_test.py`、`provision_router.py`、`register_tx_node.py`、`deploy_mihomo.py`、`deploy_wan_watch.py`、`tx-split-check.py`、`node-site-probe.py` | `mgmt_api_test.py` / `glinet-onboard.py` / `provision_router.py` 运行时硬编码读取根目录 `登录信息与配置总表-20260917.md` |
| **代理与防火墙源配置** | `run.yaml`（gitignore）、`clash-config-*.yaml`、`mihomo_fw.sh`、`mihomo_init.sh`、`snr1-mihomo-ipt.sh`、`wan_watch.*` | `run.yaml` 被 `deploy_mihomo.py`/`provision_router.py` 引用勿动 |
| **观测、导出与维护** | `webprobe.py`、`webprobe_report.py`、`network_watch.py`、`register_network_watch.ps1`、`unify-pw.py`、`p0-lossguard.sh`、`vps-backup.sh`、`vps-harden.sh`、`export_zcode_sessions.py`、`test_export_redact.py`、`codex-reset-once.py`、`test_codex_reset_once.py` | Agent 源码主实现以 `mgmt/agent/` 为准 |

## 已归档（`scripts/archive/`）

包含竞斗云/OpenClash/手工刷机早期脚本，以及 2026-11-21 归档的 19 个已完成一次性脚本（`r4-onboard.py`、`athena-enroll.py`、`athena-upgrade.py`、`flash-r7-*`、`flash-r9-resume.py`、`glinet-onboard-20261111.py`、`fleet-matchdirect-20261118.py`、`bwg-ip-swap.py`、临时 `r*-loop.sh` 及顶层重复 `.bat`/`.ps1`）。
