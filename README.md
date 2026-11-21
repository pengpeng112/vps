# VPS 与路由器集群管理系统

唯一工作目录：`F:\vps`（入口核对：**2026-11-21**）。历次归档映射见 [docs/archive/](docs/archive/README.md)（含 [20261121-manifest.md](docs/archive/20261121-manifest.md)）。

## 新会话从这里开始

1. **[AGENTS.md](AGENTS.md)**（唯一权威协作规则）：分层启动（T0/T1/T2）、三色授权边界、多 AI 锁、生产红线与交付规范。
2. **先查协作锁**：运行 `python scripts/session_guard.py status` 与 `git status --short`。
3. **[现势总览](docs/current/现势总览.md)**：稳态现势仪表盘（广州主台、出口四层防线、舰队设备表、未决事项、净版 14 条踩坑禁令）。
4. **[当前状态](docs/current/当前状态.md)**：近 30 天按时间倒序运行流水（顶部最新；2026-09~10 见归档）。
5. **[系统演进与升级需求总览](docs/reference/系统演进与升级需求总览.md)**：2026-09 至今历史升级需求脉络与 `.md`/`.docx`/`.xlsx` 见证文档索引。
6. **当前治理主线**：[docs/plans/governance-20261119/00-执行总纲与事实基线.md](docs/plans/governance-20261119/00-执行总纲与事实基线.md)（Batch 1→5 串行）。
7. **跨 AI 协作与全仓地图**：[docs/AI协作入口.md](docs/AI协作入口.md) 与 [docs/INDEX.md](docs/INDEX.md)。

## 顶层目录分工

| 位置 | 用途 |
|---|---|
| `AGENTS.md` + 6 份工具入口 | 唯一权威规则真源；`CLAUDE.md`/`GEMINI.md`/`.cursorrules`/`.windsurfrules`/`.clinerules`/`.github/copilot-instructions.md` 为全等极简指针 |
| `docs/current/` | 仅 2 份活档：`现势总览.md`（稳态仪表盘）+ `当前状态.md`（近 30 天运行流水） |
| `docs/operations/` | 现行权威 SOP（每类机型/场景仅留最新版）；[索引](docs/operations/README.md) |
| `docs/plans/` | 当前活跃治理计划（`governance-20261119/`）与近期执行记录 |
| `docs/reports/` | 历史时点只读诊断报告与证据 |
| `docs/reference/` | 原始需求、整体设计稿（含 `.docx`）与 [系统演进总览](docs/reference/系统演进与升级需求总览.md) |
| `docs/archive/` | 按期归档文档、旧月 `当前状态` 与逐笔 SHA-256 manifest；[说明](docs/archive/README.md) |
| `docs/会话导出/`、`docs/wg-peers/`、`docs/设备引导包-雅典娜.json` | 历史对话与密钥引导包（gitignore，禁止默认遍历或写入报告） |
| `mgmt/` | 管理系统源码（`backend/`、`frontend/`、`agent/`、`qos-gate/`、`deploy/`）；[开发入口](mgmt/README.md)。禁遍历 `frontend/node_modules` |
| `scripts/` | 现役运维脚本（含 `session_guard.py`、`build_gz_api.py`、[索引](scripts/README.md)）+ `archive/` |
| `review/` | 多方评审轮（`round-*/`）、现役审计脚本与按日归档证据（`evidence-*/`） |
| `verify/` | 历史四方代码核查原文（`round-1`/`round-2`）；[说明](verify/README.md) |
| `logs/`、`webprobe/` | 巡检与本地诊断生成物（gitignore）；[说明](logs/README.md) |
| `firmware/`、`京东云AX6600雅典娜/` | 现役刷机依赖与雅典娜救砖/现役固件（gitignore）；[索引](firmware/README.md) |
| `backups/` | 凭证档案、快照、构建产物与协作锁（整目录 gitignore）；[索引](backups/README.md) |
| `VPS远程工具包/` | 本机广州主台隧道入口：`管理台通道-广州.bat`（`https://127.0.0.1:8445`）；DMIT 美西为分叉温备勿日常操作 |
| 根目录 `登录信息与配置总表-20260917.md` | 凭证台账（gitignore），被 6 个现役脚本硬编码读取，**严禁挪动或打印内容** |
