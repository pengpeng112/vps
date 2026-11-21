# 资料索引（2026-11-21 更新）

全仓导航地图。**权威规则**在 [AGENTS.md](../AGENTS.md)，**稳态现势**在 [current/现势总览.md](current/现势总览.md)，**近 30 天运行流水**在 [current/当前状态.md](current/当前状态.md) 顶部。本索引只做导航，不替代实时探针。

不要默认遍历：`mgmt/frontend/node_modules/`、`firmware/`、`京东云AX6600雅典娜/`、`backups/` 全文、`docs/会话导出/`、`VPS远程工具包/` 私钥、`docs/设备引导包*.json`、`docs/wg-peers/`。

## 一、必读与协作入口

| 文件 | 职责 |
|---|---|
| [../AGENTS.md](../AGENTS.md) | 唯一权威规则真源（分层启动 / 协作锁 / 三色授权 / 红线 / 熔断 / 收尾） |
| [../README.md](../README.md) | 项目总览与目录分工 |
| [current/现势总览.md](current/现势总览.md) | 稳态现势仪表盘（广州主台、出口防线、设备表、未决项、14 条踩坑禁令） |
| [current/当前状态.md](current/当前状态.md) | 近 30 天运行流水（2026-11 起；09–10 见 [当前状态-202609_10.md](archive/当前状态-202609_10.md)） |
| [reference/系统演进与升级需求总览.md](reference/系统演进与升级需求总览.md) | 2026-09 至今四阶段历史升级需求与 excel/docx/md 见证文档索引 |
| [AI协作入口.md](AI协作入口.md) / [plans/AI协作交接规范.md](plans/AI协作交接规范.md) | 多工具启动提示词、工具入口映射与交接字段 |

## 二、当前活跃治理与近期计划（`docs/plans/`）

- **总控主线**：[governance-20261119/00-执行总纲与事实基线.md](plans/governance-20261119/00-执行总纲与事实基线.md)（含 `01` 备用线/治理、`02` 计量与限速实施手册、`03` 分批提示词卡片）。
- **核心活跃记录（2026-11-18 ~ 11-21）**：
  - [R2计量覆盖补齐与重启结算验收-20261120-GLM.md](plans/R2计量覆盖补齐与重启结算验收-20261120-GLM.md) / [R2计量覆盖与重启补验-20261120-Codex.md](plans/R2计量覆盖与重启补验-20261120-Codex.md)
  - [Batch1与Batch2阻断修复执行记录-20261119-Codex.md](plans/Batch1与Batch2阻断修复执行记录-20261119-Codex.md) / [Batch2-qos-gate开发与R2隔离PoC执行记录-20261119-GLM.md](plans/Batch2-qos-gate开发与R2隔离PoC执行记录-20261119-GLM.md)
  - [备用域名上线与最小安全发布执行记录-20261118-GLM.md](plans/备用域名上线与最小安全发布执行记录-20261118-GLM.md) / [备用域名上线复核与计量修复执行记录-20261118-Codex.md](plans/备用域名上线复核与计量修复执行记录-20261118-Codex.md)
  - [雅典娜独享BWG与其它路由器香港主用分配方案-20261119-Codex.md](plans/雅典娜独享BWG与其它路由器香港主用分配方案-20261119-Codex.md) / [雅典娜切66云实测-20261120-Codex.md](plans/雅典娜切66云实测-20261120-Codex.md)
  - [东营国外访问慢诊断记录与处置建议-20261119-Codex.md](plans/东营国外访问慢诊断记录与处置建议-20261119-Codex.md) / [用户反馈问题与处理记录-20261116-18.md](plans/用户反馈问题与处理记录-20261116-18.md)
- **模板**：[任务执行记录模板.md](plans/任务执行记录模板.md)
- **历史已完结计划**：完整保留于 [archive/plans-202609_10/](archive/plans-202609_10/)、[archive/plans-202611-completed/](archive/plans-202611-completed/)（含旧 `governance-20261118/`）与 [archive/20261001-完结计划/](archive/20261001-完结计划/)。

## 三、现行权威 SOP（`docs/operations/`）

| 文件 | 适用范围 |
|---|---|
| [Cloudflare多域名与源站应急切换台账-20261118.md](operations/Cloudflare多域名与源站应急切换台账-20261118.md) | 3 域名（含 7 位冷备）× 3 源站热切应急 SOP |
| [设备升级标准流程-20261031.md](operations/设备升级标准流程-20261031.md) | 已刷设备分级升级（S0–S4 / G1–G6） |
| [新SN-R1刷机录入SOP-自动化-20260928.md](operations/新SN-R1刷机录入SOP-自动化-20260928.md) | SN-R 自动化刷机权威手册（`flash-snr.py` / `flash-batch.py`） |
| [GL-iNet设备接入标准流程-20261113.md](operations/GL-iNet设备接入标准流程-20261113.md) | GL.iNet 通用版接入（`glinet-onboard.py`）及 [对照核查表](operations/GL-MT3600BE-与SN-R对照核查表-20261113.md) |
| [雅典娜设备接入标准-20261010.md](operations/雅典娜设备接入标准-20261010.md) | 雅典娜 nftables 设备专用，严禁套用 iptables 脚本 |
| [设备接入与隧道升级标准流程-v3-20261002.md](operations/设备接入与隧道升级标准流程-v3-20261002.md) | 隧道与 MAC 补齐参考（拓扑以 `现势总览.md` 广州主台为准） |

旧版/已被取代的手册已归档至 `docs/archive/operations-legacy/`。

## 四、设计原文、报告与评审证据

- **原始设计与演进**（`docs/reference/`）：[系统演进与升级需求总览.md](reference/系统演进与升级需求总览.md)、[路由器管理系统-开发执行计划.md](reference/路由器管理系统-开发执行计划.md)、[OpenWrt路由器翻墙整体设计方案-20261008修订版.docx](reference/OpenWrt路由器翻墙整体设计方案-20261008修订版.docx)、[VPS与路由器系统检查及完善执行计划-20260924.md](reference/VPS与路由器系统检查及完善执行计划-20260924.md)。
- **时点诊断报告**（`docs/reports/`）：保留 2026-09-24 ~ 11-17 全部原始诊断报告（如 [用户反馈问题与解决情况-20261102至20261117.md](reports/用户反馈问题与解决情况-20261102至20261117.md)、[全舰队游戏DNS无线与GIA流量核查-20261116-Codex.md](reports/全舰队游戏DNS无线与GIA流量核查-20261116-Codex.md)）。
- **多方评审与核查证据**：`verify/round-1~2/`、`review/round-*/`；近期审计脚本及 `governance-20261119` 引用的基线 JSON 保留于 `review/` 根部，历史中间证据已分桶归档至 `review/archive-20261101_17/`、`review/evidence-20261118/`、`review/evidence-20261119/`。

## 五、代码、脚本与硬件物料

- **管理系统源码**（`mgmt/`）：[mgmt/README.md](../mgmt/README.md) — `backend/`（Go API）、`frontend/`（Vue）、`agent/`（ash 脚本源头）、`qos-gate/`（arm64 令牌桶限速器）、`deploy/`（Docker/配置）。
- **运维脚本**（`scripts/`）：[scripts/README.md](../scripts/README.md) — 协作与构建门禁 `session_guard.py` / `build_gz_api.py`、接入工具 `flash-snr.py` / `flash-batch.py` / `glinet-onboard.py`、R2 计量包 `scripts/qos-r2/`、应急包 `scripts/emergency-kit/`；历史一次性脚本在 `scripts/archive/`。
- **固件与救砖物料**：[firmware/README.md](../firmware/README.md) — `firmware/openclash/{mihomo-linux-arm64,geosite.dat,country.mmdb}` 与 `firmware/snr1-openssl3/` 为 `flash-snr.py` 硬编码依赖，**禁挪禁删**；`京东云AX6600雅典娜/` 仅保留现役 LibWrt、原厂 r4533、USB 救砖与大分区工具。

## 六、敏感位置与历史归档清单（只记路径，禁抄内容）

- **敏感区（gitignore）**：根目录 `登录信息与配置总表-20260917.md`（6 个脚本运行时依赖，**严禁挪动**）、`backups/`（含 `GIA流量统计-20261116.xlsx`、凭证档案、`.cf-node-params.json`、`.session-locks/`）、`VPS远程工具包/`、`docs/设备引导包-雅典娜.json`、`docs/wg-peers/`、`mgmt/deploy/prod-certs/`。
- **归档台账**：[20261001-manifest.md](archive/20261001-manifest.md)、[20261012-manifest.md](archive/20261012-manifest.md)、[20261119-manifest.md](archive/20261119-manifest.md)、[20261121-manifest.md](archive/20261121-manifest.md)。
