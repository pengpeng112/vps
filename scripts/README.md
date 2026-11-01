# 脚本与配置索引

2026-10-12 更新分类。2026-10-02 起设备接入链以 DMIT 为管理面。历史脚本在 `scripts/archive/`，原因见 [20261001 清单](../docs/archive/20261001-manifest.md) 和 [20261012 清单](../docs/archive/20261012-manifest.md)。名称含 test/diag/recon 不等于只读，执行前看代码和凭据来源。

## 现役脚本

| 类别 | 文件 | 使用说明 |
|---|---|---|
| **设备接入四件套** | flash-snr.py、deploy-parallel-tunnel.py、fleet-netprobe-install.py、point-check.py | 新设备/上线/体检。口径见 [v3 标准流程](../docs/operations/设备接入与隧道升级标准流程-v3-20261002.md) 和 [同型号接入标准](../docs/operations/同型号设备接入标准-20261001.md)。管理连接以 DMIT 为准 |
| **已完成的一次性部署** | r4-onboard.py、athena-enroll.py、athena-upgrade.py | R4 与雅典娜管理面迁移已经做完。没有新的授权不要重跑：enroll 会创建管理台设备，upgrade 会向雅典娜写隧道 |
| 分流体检 | tx-split-check.py、node-site-probe.py | tx-split 会试切再回切。node-site-probe 不改节点，结果写 `logs/node-probe/` |
| 注册与部署 | provision_router.py、register_tx_node.py、deploy_mihomo.py、deploy_wan_watch.py | 涉及远端写入；单次任务范围明确后使用 |
| 管理台 API 驱动 | mgmt_api_test.py | 凭据运行时读台账（缺台账即失败）；token 缓存 backups/.mgmt-token；ops 需 Idempotency-Key |
| 舰队运维 | unify-pw.py、p0-lossguard.sh、vps-backup.sh、vps-harden.sh | harden/backup 是变更操作非纯查询 |
| 诊断客户端 | tx_node_diag.py、ubus_call.py、qf_client.py | 雅典娜局域网旧通道（quickfile / ubus），不是舰队反向 SSH。先看调用会不会登录或写配置。qf_app.html 无引用，已放 `archive/` |
| 代理与网络源配置 | clash-config-pc/router.yaml、mihomo_fw.sh、mihomo_init.sh、snr1-mihomo-ipt.sh、run.yaml、snr1-mihomo.init 已归档但 ipt 仍被 flash-snr 复用 | 含设备差异和敏感配置，不能跨机型套用；run.yaml 被 deploy_mihomo/provision_router 引用勿动 |
| 日志及看护 | wan_watch.*、network_watch.py、register_network_watch.ps1 | wan_watch 路由器侧；network_watch PC 侧 15 分钟观察 |
| 观测巡检 | webprobe.py、webprobe_report.py | PC 侧站点巡检（计划任务）；报告脚本只读 CSV |
| 历史会话导出 | export_zcode_sessions.py、test_export_redact.py | 脱敏默认开；回归测试全虚构样本 |
| 本地检查门 | ci-local.ps1 | 发布前本地检查（agent sh -n / py_compile / 脱敏回归 / go test） |

## 已归档（scripts/archive/，不再默认可执行）

竞斗云监测 ×2、ttyd（通道已坏）、tftp/router_recon（刷机期一次性）、migrate_tx_ip（已完成）、snr-webclean、deploy-r4-relay（被 r4-onboard 取代）、openclash 时代物料 ×8、老手工刷机网络配置 ps1 ×4、SN-R1 专用 ×2、organize-workspace-20260926.ps1、ubus_objs.json、qf_app.html（厂商 Quickfile 页面，无引用）。ubus_token.txt 已移 backups/（敏感区）。

Agent 的当前主实现位于 `mgmt/agent/`。scripts 中类似文件是否仍为部署源，需要按消费者与生产路径核对，不能仅因重名直接合并覆盖。
