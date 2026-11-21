# Batch1与Batch2独立核查记录（Codex，2026-11-19）

用户任务：核查已宣称完成的两个批次是否存在问题。本轮不把旧执行卡当成新的部署指令。

## 范围与协作

- 21:19 起完成 session_guard status、AGENTS/总览/最近状态/治理规格和两批执行记录核对。
- 纯读生产：广州服务/二进制 SHA/只读数据库事务、R2 正式状态/计量/cron/CF delay、HK/BWG 明确 PoC 残留。
- 本地新增审查脚本、隔离负例与报告；未修改待审产品源码，未下发任务或正式配置，未启用 enforce。
- docs 锁：`lock-dac58540cce5`，session=`Codex-Batch12-review-20261119`，收尾释放；无生产写锁申请及生产写操作。

## 独立结果

**Batch1=PARTIAL；Batch2=PARTIAL/BLOCK_ENFORCE。** 正式业务正常，两个批次的“全部完成”不成立。

1. R2仍41/41/41、正式mihomo PID4880、PROXY=AUTO、BWG主用、DOWNLOAD=DIRECT，WAN noqueue；配置/DHCP/隧道 SHA 未变。
2. 两个新计量脚本远端与本地 SHA 完整一致，旧版备份存在，多轮cron HEALTH=ok。但最新抽样 partial=true/quota_eligible=false；午夜reboot cron仍在。
3. 真实23:57→00:02负例复现349700B整段计新日且资格=true；新增方向计数未知、日期/epoch不一致也错误放行。当前不能认证合格观察窗。
4. 原gate8项单测PASS；缺失的六个负例均复现失败：release后reload/set-rate复活限制、generation77→2、半关闭丢响应、原解除测试提前完成、坏状态重新带限。
5. 模板包测试PASS；裸 `build_gz_api.py --check-only` 因未审核dirty范围正确拒绝，不能冒称本轮全后端检查通过。
6. 上游省流证据混用层次/窗口、固定512KiB未完成，现测量驱动并发解析还存在tuple.split错误。省流NOT_PROVEN，按发布门禁FAIL_SAVE_TRAFFIC/BLOCK_ENFORCE。
7. 归档32/32 SHA一致，但维修工具引用的模块/JSON/加载路径已被移走；归档manifest自身被gitignore；AI入口10行不符15行原卡。
8. 本地rssh-probe env修复/py_compile/缺DSN退出2通过；广州部署版仍内嵌DSN（只记布尔与SHA，没有输出凭据），属另批生产遗留。
9. R2 PoC目录/监听无残留；HK临时目录/18443无残留；BWG所查INPUT/OUTPUT无poc-meter。R2 g204=204/1.094s、百度200/0.153s。
10. 本次R2两条CF delay均通；原Batch1 JSON实际6台×2节点×3轮=36/36，原报告“四台12/12”表述不准确。

## 交付与命令

- 完整核查和下一AI补修提示词：`review/Batch1与Batch2独立核查结论及整改提示词-20261119-Codex.md`。
- 生产证据：`review/Batch12独立复核-20261119-212420-Codex.json`、`review/Batch12服务器清理与平台只读补核-20261119-Codex.json`。
- 静态SHA/归档/入口/CF计数：`review/Batch12仓库静态核查-20261119-Codex.json`。
- 独立负例：`review/Batch12缺失负例隔离复现-20261119-213856-Codex.json`（断言版复跑退出1，旧213115证据保留）。
- 可复跑本地负例：`python review/batch12_regression_review_20261119.py`，副本目录唯一；当前应返回1，只有修复相应产品缺陷后六个Go和三个计量断言才应通过。历史坏coverage/API复合夹具不作为已隔离JSON验证结论。
- 已跑：rssh py_compile / 缺DSN隔离检查；gate go test -count=1 -v ./...；backend go test -count=1 ./internal/clash；build_gz_api.py --check-only（范围门禁拒绝）。

## 观察时间与下一动作

11-19 18:57是旧计量修复部署时刻，不是可信日账资格起点；11-20 18:57不能当合格24h放行。
先修计量日界/重启/覆盖、gate持久紧急锁/半关闭/测试，再独立PoC复验上游省流。记录可信T0后最早T0+24h复核滚动窗口；完整自然日另验午夜至次日结算。当前没有可保证的观察期满日期。
不进入正式Batch3发布，不回退已废弃的服务端执行器，不执行整条WAN限速。

## 回退与未验证

本轮生产零写，无生产回退对象。只新增本地核查产物及三件套；不清理其它会话dirty文件。
未重建真机PoC、未做新真实速度/上游省流测试、未做现场Wi-Fi验收、未验正式跨午夜资格，均不声称通过。
