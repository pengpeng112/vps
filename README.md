# VPS 与路由器项目

唯一工作目录：`F:\vps`。入口核对于 **2026-10-12**。移动记录见 [2026-10-01 清单](docs/archive/20261001-manifest.md) 和 [2026-10-12 清单](docs/archive/20261012-manifest.md)。

## 新会话从这里开始

1. [AGENTS.md](AGENTS.md)：态势速览、工作边界、高危坑。
2. [当前状态](docs/current/当前状态.md)：已知事实、来源日期、冲突与未验证项（顶部最新）。
3. [跨 AI 协作入口](docs/AI协作入口.md)：启动提示词、事实优先级和交接格式。
4. 设备接入/隧道升级看 [v3 标准流程](docs/operations/设备接入与隧道升级标准流程-v3-20261002.md)；进行中任务见 [资料索引 §二](docs/INDEX.md)。
5. 通过 [资料索引](docs/INDEX.md) 按需阅读，不要一次加载所有历史会话和凭证。

## 目录分工

| 位置 | 用途 |
|---|---|
| `docs/current/` | 唯一现状摘要；运行结论附日期和证据 |
| `docs/plans/` | 活跃/近期任务计划与执行记录（证据链） |
| `docs/operations/` | 接入标准、运维、刷机、通道手册 |
| `docs/reference/` | 原始需求、阶段计划、历史会话分析指南 |
| `docs/reports/` | 验收、变更记录、诊断证据（按日期） |
| `docs/archive/` | 历史归档；整理清单在 `20261001-manifest.md`、`20261012-manifest.md` |
| `docs/会话导出/` | 历史对话，不视为已脱敏或当前事实 |
| `docs/wg-peers/`、`docs/设备引导包-雅典娜.json` | WireGuard 与注册包，含密钥，不要写进索引正文 |
| `verify/` | 2026-10-10 核查原文；[说明](verify/README.md) |
| `logs/`、`webprobe/` | 巡检生成物；[日志说明](logs/README.md) |
| `mgmt/` | 管理系统源码；[开发入口](mgmt/README.md)。不要遍历 `frontend/node_modules` |
| `scripts/` | 现役运维脚本（[分类表](scripts/README.md)）+ `archive/` 历史脚本 |
| `firmware/`、`京东云AX6600雅典娜/` | 固件与工具；[固件索引](firmware/README.md) |
| `backups/` | 备份、凭据与现场证据（gitignore）；[备份索引](backups/README.md) |
| `VPS远程工具包/` | 本机进 DMIT 的入口：`管理台通道-DMIT.bat`（8444） |
| `.v2c/`、`.video_agent/` | 工具生成的定位文件，保留原位 |

凭证台账仍在根目录，且有脚本运行时依赖；只在具体任务需要时读取相关段落，不复制到报告或外发附件。R2/R3/R4 管理主台是 DMIT，本机入口为 `VPS远程工具包\管理台通道-DMIT.bat`（8444）；访问与恢复细节见 [当前状态](docs/current/当前状态.md)及 [v3 标准流程](docs/operations/设备接入与隧道升级标准流程-v3-20261002.md)。

当前出口涉及 BWG 东京、DMIT 美西、TX、DO 四节点；DO 仍承载部分历史依赖。设备与管理面完成范围见 [当前状态](docs/current/当前状态.md)，操作前核实现状。
