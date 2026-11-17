# 资料索引（2026-10-31 更新）

给后来的 AI 用。先看本页，再按任务打开对应文件。现网事实以 [current/当前状态.md](current/当前状态.md) 顶部为准，本索引是地图，不是实时探针。

> ⚡ **2026-10-31 首读物**：[一周重大变更与现势总览](current/一周重大变更与现势总览-20261025_1031.md)——一周多会话变更的权威汇总（现势终态/时间线/教训禁令/未决事项/**会话记录义务**）。任何会话开工先读它，变更后必须按其 §7 完成记录。

阅读顺序：`README.md` → `AGENTS.md` → [一周总览](current/一周重大变更与现势总览-20261025_1031.md) → [当前状态](current/当前状态.md) 顶部 → [AI协作入口](AI协作入口.md) → 本页按需下钻。

不要默认打开：`mgmt/frontend/node_modules/`、`firmware/` 里的镜像、`京东云AX6600雅典娜/` 里的固件、`backups/` 全文、`docs/会话导出/`、`VPS远程工具包/` 里的私钥、`docs/设备引导包*.json`、`docs/wg-peers/`。这些目录要么巨大，要么含密钥。

## 一、入口

| 文件 | 作用 |
|---|---|
| [../AGENTS.md](../AGENTS.md) | 工作边界、授权、高危坑、短态势 |
| [../README.md](../README.md) | 项目总览和目录分工 |
| [current/当前状态.md](current/当前状态.md) | 唯一持续维护的现网摘要，顶部最新 |
| [AI协作入口.md](AI协作入口.md) | 跨工具启动提示词和交接格式 |
| [plans/AI协作交接规范.md](plans/AI协作交接规范.md) | 交接要写的字段 |

## 二、进行中（2026-10-12）

- **2026-10-29 晚远程保护修复**：[执行记录](plans/远程不断线修复执行记录-20261029.md)。R1/R3/R5 现场热修复已做，后端二进制/agent 已加载函数未重启激活；重发平台配置前先看记录，防止覆盖热修复。

操作前再看当前状态顶部。计划里的“下一步”不是本轮授权。

- **两节点收敛，未到停机日**：[两节点收敛与R3无线执行计划-20261002](plans/两节点收敛与R3无线执行计划-20261002.md)。DO 与腾讯保留到 **2026-11-02**，闸门有证据之前不得停机。记录见 [执行记录](plans/两节点收敛与R3无线执行记录-20261002.md)。
- **DO 仍是旧主台**：管理库冻结勿写。医院跳板、WireGuard hub 仍在 DO。雅典娜**管理面**已于 10-10 迁到 DMIT，代理绑定另说，以当前状态为准。
- **R5 / R6 已接入，现场电源未恢复**：R5 `13225` 自 10-09 21:11 起无心跳；R6 `13226` 自 10-10 约 07:12 起无心跳。不是“还没刷机”。
- **R1 已于 10-12 重新上线**并补了 discovery / 开机钩子 / connlog，有线 MAC 已固定。见当前状态顶部。
- **10-10 核查与完善已做完**：[verify/round-1/核查报告.md](../verify/round-1/核查报告.md)、[一次性完善执行记录-20261010](plans/一次性完善执行记录-20261010.md)。不要把这两份再当成待办队列。
- **一次性脚本不要重跑**：`scripts/r4-onboard.py`、`scripts/athena-enroll.py`、`scripts/athena-upgrade.py`。前两个会创建或改写设备，第三个会向雅典娜部署。

### 不要再沿用的旧说法

| 旧说法 | 2026-10-12 的位置 |
|---|---|
| R5/R6 未刷 | 10-09 已接入 DMIT，当前是现场离线 |
| 雅典娜只在 DO、排障口 18222 | 管理面在 DMIT `13228`，设备 `rtr-5e1a8d`；DO 上旧档 `rtr-1eff66` 冻结 |
| R4 待接入 | 10-02 已完成，`r4-onboard.py` 只留档 |
| R1 仍处于 10-09 计划下线 | 10-12 约 09:46 重新上电 |
| 引导包在 `docs/operations/` | 已移回 `docs/设备引导包-雅典娜.json`，见 [20261012 清单](archive/20261012-manifest.md) |
| `docs/wg-*.conf` 在 docs 根目录 | 在 `docs/wg-peers/` |

## 三、顶层目录

| 路径 | 用途 | 先读 | 含密钥或超大 |
|---|---|---|---|
| `docs/current/` | 现网摘要 | 当前状态顶部 | 否 |
| `docs/plans/` | 计划与执行记录 | 本页 §二、§六 | 个别段落可能提到路径，不抄口令 |
| `docs/operations/` | 仍可参考的手册 | [operations/README.md](operations/README.md) | 否（引导包已移出） |
| `docs/reports/` | 时点证据，不改写原结论 | 本页 §七 | 旧报告可能夹带当时的地址 |
| `docs/reference/` | 设计原文 | [reference/README.md](reference/README.md) | 否 |
| `docs/archive/` | 完结计划与整理清单 | [archive/README.md](archive/README.md) | 旧文不一定脱敏 |
| `docs/wg-peers/` | DO 上的 WireGuard peer | 不要打开，除非任务就是 WG | 是 |
| `docs/会话导出/` | 历史对话线索 | 不要当事实 | 未脱敏 |
| `docs/设备引导包-雅典娜.json` | 雅典娜注册包 | 不要打开 | 是。`provision_router.py` 仍往 `docs/` 写新包 |
| `mgmt/` | 管理台源码 | [mgmt/README.md](../mgmt/README.md) | `frontend/node_modules` 约 2.6 万文件，不要遍历；`deploy/prod-certs/` 是证书 |
| `scripts/` | 现役运维脚本 | [scripts/README.md](../scripts/README.md) | `clash-config-*.yaml`、`run.yaml`、`provision_router.py` 被 gitignore |
| `scripts/archive/` | 一次性旧脚本 | 脚本 README 的归档段 | `qf_app.html` 是厂商页面快照 |
| `verify/round-1/` | 2026-10-10 四方核查原文 | [verify/README.md](../verify/README.md) | 否 |
| `logs/node-probe/` | 巡检输出 | [logs/README.md](../logs/README.md) | 生成物，gitignore |
| `webprobe/` | PC 侧网站巡检 CSV | 脚本 README | 生成物 |
| `firmware/` | 核心与镜像 | [firmware/README.md](../firmware/README.md) | 大文件，gitignore |
| `京东云AX6600雅典娜/` | 雅典娜刷机物料 | 固件 README | 大文件，含 exe，gitignore |
| `backups/` | 备份、口令档、现场包 | [backups/README.md](../backups/README.md) | 是，整目录 gitignore |
| `VPS远程工具包/` | 本机进 DMIT 的隧道和密钥 | `使用说明.txt` | 是。入口是 `管理台通道-DMIT.bat`，本机端口 8444 |
| 根目录 `登录信息与配置总表-20260917.md` | 管理台登录台账 | 只在脚本要读时打开 | 是。`scripts/mgmt_api_test.py` 运行时依赖此路径，不能挪 |

## 四、设备速查（导航，不是心跳）

排障口都在 DMIT `127.0.0.1` 上，只绑回环。管理 SSH 先进 `179.255.137.64:28722`。SN-R 用 iptables，雅典娜用 nftables，脚本不能互换。R1 的反向目标保持 `192.168.132.1:22`。

| 称呼 | ID | 口 | 2026-10-12 导航 |
|---|---|---|---|
| R1 临朐网警 | `rtr-d3847d` | 13222 | 10-12 已回；WAN MAC `76:a7:c8:e0:4f:21`，br-lan `…:31` |
| R2 家里 | `rtr-a9bcba` | 13223 | DMIT 管理面；WAN `…:19` / br-lan `…:1a`（早于统一公式） |
| R3 安丘网警 | `rtr-38aa54` | 13227 | WAN `…:23` / br-lan `…:33` |
| R4 坊子网警 | `rtr-cd473f` | 13224 | WAN `…:24` / br-lan `…:34` |
| R5 | `rtr-c46a25` | 13225 | 已接入，10-09 21:11 起离线 |
| R6 | `rtr-673568` | 13226 | 已接入，10-10 约 07:12 起离线 |
| 雅典娜 AX6600 | `rtr-5e1a8d` | 13228 | 10-10 起管理面在 DMIT；nftables |
| 雅典娜旧档 | `rtr-1eff66` | 原 18222 | 只在 DO 旧库，冻结勿写 |

有线 MAC 公式在 `scripts/flash-snr.py`：WAN `76:a7:c8:e0:4f:{0x20+N}`，br-lan `{0x30+N}`。R2 是例外。R5/R6 的 25/35、26/36 是预留值，现场是否已生效要回线后再核。

## 五、手册（operations/）

新设备或换隧道，先看 [v3 标准流程](operations/设备接入与隧道升级标准流程-v3-20261002.md)。SN-R 细节在 [同型号接入标准](operations/同型号设备接入标准-20261001.md)，雅典娜走 [雅典娜设备接入标准-20261010](operations/雅典娜设备接入标准-20261010.md)，**GL.iNet 系走 [GL-iNet设备接入标准流程-20261113](operations/GL-iNet设备接入标准流程-20261113.md)**（通用版脚本 glinet-onboard.py --name/--rssh/--host；含 8 坑+幂等 guard+豁免修复+蜂窝预案）。**SN-R 批量/自动刷机（2026-10-13 定稿）**：flash-snr.py 单台（11关+下发双验）+ flash-batch.py 并行自动发现，权威手册=刷机 SOP 末章「统一 SN 刷机流程 v2」。冲突时以当前状态和 v3 为准。

| 文件 | 何时打开 |
|---|---|
| [GL-iNet设备接入标准流程-20261113.md](operations/GL-iNet设备接入标准流程-20261113.md) | GL.iNet 系新设备接入（通用版，一键脚本）|
| [设备升级标准流程-20261031.md](operations/设备升级标准流程-20261031.md) | 已刷设备升级（S0-S4 分级+G1-G6）|
| [设备接入与隧道升级标准流程-v3-20261002.md](operations/设备接入与隧道升级标准流程-v3-20261002.md) | 接入、升级隧道、补 MAC |
| [同型号设备接入标准-20261001.md](operations/同型号设备接入标准-20261001.md) | AX3000 验收项、WiFi dat、终验 |
| [雅典娜设备接入标准-20261010.md](operations/雅典娜设备接入标准-20261010.md) | nftables 设备，禁止套 iptables |
| [新SN-R1刷机录入SOP-自动化-20260928.md](operations/新SN-R1刷机录入SOP-自动化-20260928.md) | `flash-snr.py` 的现场口径；含 10-08/10-09 增补 |
| [新SN-R1刷机录入SOP-20260928.md](operations/新SN-R1刷机录入SOP-20260928.md) | 自动化稿之前的手工版 |
| [服务器访问通道总览-20260923.md](operations/服务器访问通道总览-20260923.md) | 通道背景；恢复步骤要对照当前状态 |
| [刷机发货流程SOP-雅典娜AX6600-20260917.md](operations/刷机发货流程SOP-雅典娜AX6600-20260917.md) | 早期发货，不用于 SN-R |
| [SN-R1-AX3000-记录与刷机准备-20260923.md](operations/SN-R1-AX3000-记录与刷机准备-20260923.md) | 机型识别和工位依赖 |
| [管理台远程策略与刷机补充-20260920.md](operations/管理台远程策略与刷机补充-20260920.md) | 早期策略 |
| [路由器代理配置交接-20260917.md](operations/路由器代理配置交接-20260917.md) | 早期代理交接 |
| [电脑网络观察小程序-20260928.md](operations/电脑网络观察小程序-20260928.md) | PC 侧观察任务 |

手册里的旧路径用 [迁移清单](reports/整理-20260926/迁移清单.csv) 和两份 manifest 查，不要把当时的命令直接拿去跑。

## 六、计划与执行记录（plans/）

| 文件 | 状态 |
|---|---|
| [两节点收敛与R3无线执行计划-20261002.md](plans/两节点收敛与R3无线执行计划-20261002.md) | 活跃。停机日 2026-11-02 |
| [两节点收敛与R3无线执行记录-20261002.md](plans/两节点收敛与R3无线执行记录-20261002.md) | 配套记录 |
| [DMIT主台迁移收尾计划-20261002.md](plans/DMIT主台迁移收尾计划-20261002.md) | 收尾清单。D2 雅典娜管理面迁移已在 10-10 完成；其余闸门以计划原文和当前状态核对 |
| [一次性完善执行计划-20261010.md](plans/一次性完善执行计划-20261010.md) | 计划已执行 |
| [一次性完善执行记录-20261010.md](plans/一次性完善执行记录-20261010.md) | 10-10 证据 |
| [R2上线升级执行记录-20261001.md](plans/R2上线升级执行记录-20261001.md) | 已完成的证据 |
| [温备部署计划-20261001.md](plans/温备部署计划-20261001.md)、[温备部署执行记录-20261001.md](plans/温备部署执行记录-20261001.md) | 已演练 |
| [搬瓦工东京接入执行计划-20260930.md](plans/搬瓦工东京接入执行计划-20260930.md)、[执行记录](plans/搬瓦工东京接入执行记录-20260930.md) | 东京节点已接入 |
| [三方管理冗余方案-20260930.md](plans/三方管理冗余方案-20260930.md) | 已实施的方案说明 |
| [OpenWrt路由器翻墙整体设计方案-20261008修订版.docx](plans/OpenWrt路由器翻墙整体设计方案-20261008修订版.docx) | 设计稿，不是操作步骤 |
| [文档入口与AI协作整理执行记录-20261002.md](plans/文档入口与AI协作整理执行记录-20261002.md) | 上一轮入口整理 |
| [文档入口与目录索引整理执行记录-20261012.md](plans/文档入口与目录索引整理执行记录-20261012.md) | 本轮目录索引 |
| [任务执行记录模板.md](plans/任务执行记录模板.md) | 新执行记录的空表 |
| [AI协作交接规范.md](plans/AI协作交接规范.md) | 交接格式 |

0926–0927 的四份旧计划在 [archive/20261001-完结计划/](archive/20261001-完结计划/)，不是当前队列。

## 七、报告（reports/，按日，保留原文）

| 日期 | 文件 |
|---|---|
| 10-10 | [../verify/round-1/](../verify/round-1/)（核查报告、四方意见、现网实查） |
| 10-02 | [外部AI复核包-DMIT迁移-20261002](reports/外部AI复核包-DMIT迁移-20261002.md)、[服务器路由器带宽实测-20261002](reports/服务器路由器带宽实测-20261002.md) |
| 10-01 | [三出口网站访问测试](reports/三出口网站访问测试-20261001.md)、[数据安全与抗识别审查](reports/数据安全与抗识别审查-20261001.md)、[外部AI复审包](reports/外部AI复审包-20261001.md) |
| 09-30 | [路由器配置问题分析与整改](reports/路由器配置问题分析与整改-20260930.md)、[线路情况与延迟汇总](reports/线路情况与延迟汇总-20260930.md)、[精品线路选型论证](reports/精品线路选型论证-20260930.md)、[精品线路复核](reports/精品线路复核-20260930.md) |
| 09-28 | [网络复核](reports/网络复核-20260928/诊断报告.md)、[WiFi远程控制打通](reports/变更记录-20260928-WiFi远程控制打通.md)、[管理台MAC字段](reports/变更记录-20260928-管理台MAC字段.md) |
| 09-27 | [统一优化](reports/统一优化-20260927/)、[网络稳定性诊断](reports/网络稳定性诊断-20260927/诊断报告.md)、[网络稳定性分析材料](reports/网络稳定性分析材料-20260927.md)、[医疗直连规则变更](reports/变更记录-20260927-医疗直连规则与SNR1隧道修复.md) |
| 09-24–26 | [阶段0](reports/阶段0执行报告-20260924.md)、[阶段1](reports/阶段1执行报告-20260925.md)、[腾讯第二节点](reports/变更记录-20260925-腾讯第二节点上线与双线容灾.md)、[整理-20260926](reports/整理-20260926/) |

设计原文在 [reference/](reference/README.md)：[路由器管理系统-开发执行计划](reference/路由器管理系统-开发执行计划.md)、[09-24 检查计划](reference/VPS与路由器系统检查及完善执行计划-20260924.md)、[历史会话分析指南](reference/AI分析历史会话指南.md)。

## 八、代码与脚本

`mgmt/` 布局以 [mgmt/README.md](../mgmt/README.md) 为准：`backend/` Go API，`frontend/` Vue，`agent/` 是路由器上的脚本源头，`deploy/` 是 compose。`backend/jsonfilter.exe` 是本地编译产物，由 `mgmt/.gitignore` 忽略，不是要下发的路由器程序。

现役脚本分类见 [scripts/README.md](../scripts/README.md)。和索引有关的几点：

- 接入四件套：`flash-snr.py`、`deploy-parallel-tunnel.py`、`fleet-netprobe-install.py`、`point-check.py`。
- 只读巡检：`node-site-probe.py` 写 `logs/node-probe/`，不改节点。
- 管理台 API：`mgmt_api_test.py`。默认设备是 R1。会登录，不是纯本地读文件。
- 已完成勿重跑：`r4-onboard.py`、`athena-enroll.py`、`athena-upgrade.py`。
- 雅典娜局域网旧通道：`qf_client.py`、`provision_router.py`、`ubus_call.py`。这不是舰队 SSH。`qf_app.html` 无引用，已放进 `scripts/archive/`。
- Agent 以 `mgmt/agent/` 为准。`scripts/` 里同名 shell 是否仍是部署源，要看调用者，不能因重名覆盖。

## 九、敏感位置（只记路径）

| 路径 | 原因 |
|---|---|
| `登录信息与配置总表-20260917.md` | 管理台口令台账，脚本按此路径读取 |
| `backups/.snrN-pw`、`backups/.new-snr-pw`、`backups/SN-R系列凭证总览.md`、`backups/SN-R*-凭证档案-*.md` | 路由器口令 |
| `backups/.athena-enroll.json` | 雅典娜录入包 |
| `backups/DMIT美西-凭据档案-20261001.md`、`backups/TX-HY2节点凭据-20260930.md`、`backups/搬瓦工东京-*.md` | 节点凭据 |
| `docs/设备引导包-雅典娜.json` | 注册包。gitignore：`docs/设备引导包*.json` |
| `docs/wg-peers/` | WireGuard。`wg-pc.conf` 与 `wg-电脑.conf` 的 SHA-256 相同，先不要删其中一份 |
| `VPS远程工具包/id_*` | 本机 SSH 私钥 |
| `mgmt/deploy/prod-certs/` | 证书 |

报告和索引里不抄这些文件的内容。

## 十、归档与本轮移动

- [archive/20261001-manifest.md](archive/20261001-manifest.md)：2026-10-01 脚本归档和计划搬迁。
- [archive/20261012-manifest.md](archive/20261012-manifest.md)：本轮两处移动和 gitignore 补洞。
- [archive/README.md](archive/README.md)：更早的交接和已停止的 R619AC。
- [会话导出/](会话导出/)：五份历史对话，只作线索。

## 维护规则

- 当前状态只写 `docs/current/当前状态.md`。任务进度写 `docs/plans/`。报告保持原时点，勘误另起段落。
- 新手册放 `docs/operations/`，新证据放 `docs/reports/<主题>-日期/`。
- 移动前先搜引用。记录旧路径、新路径、SHA-256，不覆盖已有目标。归档不是删除，也不是脱敏。
- 改完索引后，看 `AGENTS.md` 顶部的态势是否还把已完成的事写成待办。
