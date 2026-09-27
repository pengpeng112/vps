# 脚本与配置索引

2026-09-26 分类。为保持既有调用路径，本轮不移动运行脚本。名称含 test/diag/recon 不等于只读，执行前审查代码及凭据来源。

| 类别 | 文件/模式 | 使用说明 |
|---|---|---|
| 网络检查与本机刷机网卡 | check-net-state.ps1、flash-net-*.ps1、tftp_logger.py | flash-net 会改网络，不能用于普通只读探索 |
| 注册与部署 | provision_router.py、register_tx_node.py、deploy_*.py、migrate_tx_ip.py | 涉及远端写入；单次任务范围明确后使用 |
| 诊断及调用客户端 | router_recon.py、tx_node_diag.py、mgmt_api_test.py、ttyd_run.py、ubus_call.py、qf_client.py | 先检查具体调用是否写入/登录/下发命令 |
| 代理与网络源配置 | clash-config-*.yaml、mihomo_*.sh、snr1-*、init_openclash.*、init_dl.sh、oc_wrapper.sh | 含设备差异和敏感配置，不能跨机型套用 |
| 日志及备份维护 | wan_watch.*、vps-backup.sh、vps-harden.sh | harden 是变更；backup 也会创建文件，不属于纯查询 |
| 历史会话导出 | export_zcode_sessions.py | v2（2026-09-27 统一优化 M05）：脱敏默认开（关闭需 --no-redact --danger-confirm 双闸）、项目边界精确匹配、流式读取、统计输出；精确脱敏对运行时读 backups/.redact_pairs.json；回归测试 test_export_redact.py（全虚构样本） |
| 观测巡检 | webprobe.py、webprobe_report.py | PC 侧境外+gstatic+百度巡检（计划任务 VPS-WebProbe 每 2h，2026-09-27 M04 起）；报告脚本只读 CSV |
| 本地检查门 | ci-local.ps1 | 发布前本地检查（agent sh -n / py_compile / 脱敏回归 / go test）；-WithDB 连隔离 PG 跑集成 |
| 管理台 API 驱动 | mgmt_api_test.py | 2026-09-27 M05 起凭据运行时读台账、缺台账即失败；token 缓存 backups/.mgmt-token |
| 历史刷机排障 | gsw_monitor.py、gsw_monitor2.py | 待消费者清点后归档，不能继续默认执行 |
| 抓取/临时状态 | *.html、ubus_objs.json、menu.json、login_acl.json、run_back.json、run.yaml、oc_settings.json、ubus_token.txt | 有效性未知，可能敏感；不凭时间直接删除 |
| 本次整理工具 | organize-workspace-20260926.ps1 | 一次性脚本，已执行；拒绝覆盖目标，不能重复当日整理 |
| 缓存 | __pycache__/ | 可再生；2026-09-27 统一优化 M10 已清理 |

Agent 的当前主实现位于 `mgmt/agent/`。scripts 中类似文件是否仍为部署源，需要按 P05 做消费者与生产路径核对，不能仅因重名直接合并覆盖。
