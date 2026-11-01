# ZCode 独立初审（问题清单草案）— multi-verify round-2，2026-10-13

对象：①SN-R 刷机流程遗漏（flash-snr.py/flash-batch.py/SOP v2）②R7 不合规软件残留。
本清单为草案，待 Kimi/Grok/Codex 独立审查后进分歧表。每条带验证方法，无证据不裁决。

| 编号 | 严重度 | 位置 | 问题猜想 | 验证方法 |
|---|---|---|---|---|
| Z1 | 高 | flash-batch.py KNOWN_MAC_PREFIXES | OUI 前缀白名单（a4:d8:ca:14:/76:a7:c8:e0:4f:）会误跳过同批次出厂新设备（如 a4:d8:ca:14:xx 的新机被当现役→不刷）。应改精确 MAC 集合匹配（台账/平台） | 构造测试：模拟 wan=a4:d8:ca:14:aa:bb（不在台账）应判出厂，现逻辑判现役跳过 |
| Z2 | 高 | flash-snr.py push() | 文件推送无 md5 对拍；R7 实测 paramiko 通道写可能被 dropbear 即关——install-agent.sh 收到截断文件会装出半套栈且无告警 | 代码走查（push 无校验）+ R7 事故记录（backups/.r7-finish.log 已删，台账更新十四有记载） |
| Z3 | 高 | flash-snr.py init.d heredoc（REJECT-SELFHEAL 三行） | r-string 渲染出的 sh 片段未做 bash -n；若缩进/括号错则 mihomo 起不来=断网级 | 本地提取渲染结果 bash -n |
| Z4 | 中 | flash-snr.py 步骤2 run 块 | umount 失败（busy）仍执行 dd 写挂载中块设备；无前置成功条件 | 代码走查；R7 当时两次 umount 失败仍 dd（侥幸未炸） |
| Z5 | 中 | flash-snr.py 步骤0 | open_ports 算完未用；Tel() 无总超时，banner 不来挂死整轮（batch 并行拖死一台） | 代码走查 |
| Z6 | 中 | flash-snr.py API 调用群 | r.json() 无 status_code 检查；enroll 失败以 KeyError 崩（cron 无人值守难排障） | 代码走查 |
| Z7 | 低 | flash-snr.py alloc_rssh | 未经 batch 的多台并行有端口竞态（batch 已预分配规避） | 代码走查 |
| Z8 | 中 | R7 /usr/bin | rathole（内网穿透）/zcon-ng/zcon-edge-arm64 二进制仍在盘。进程杀+服务禁后属死文件，但字面"不合规软件"仍存在，误启可复活 | R7 实机 ls+md5 |
| Z9 | 中 | R7 暴露面 | uhttpd 0.0.0.0:443+80、dropbear 0.0.0.0:22、dnsmasq 绑 WAN 53——WAN 侧可达性未审计防火墙拦截 | R7 实机查 WAN 入站规则 |
| Z10 | 低 | R7 crontab | factory checkMem 厂商行仍在（无害遗留） | 实机 cat |
| Z11 | 中 | flash-batch.py telnet_fingerprint | 未验型号（任意开了 telnet+abc@1234 的设备会被当 JCG 刷机=变砖风险）；未排除老 SN-R 重置回归场景的双义性 | 代码走查+构造用例 |
| Z12 | 中 | flash-snr.py 步骤9 rc.local | sed '/# wifi-default-off/,$d' 删到文件尾——厂商尾部有他人内容会误删（R7 当前尾部=我们的钩子，安全；新机型风险） | 代码走查 |
| Z13 | 低 | SOP v2 vs 脚本 | 文档 11 关+下发双验与脚本检查项需逐条对齐（漂移风险） | 文档-代码对照 |
| Z14 | 低 | flash-batch.py 发现循环 | 串行 fingerprint 验证，多 IP 时拖慢（每台最多 ~11s） | 代码走查 |
| Z15 | 低 | R7 /etc/hotplug.d | 只封了 block/10-mount；iface/usb 等未审计厂商逻辑 | 实机 ls+grep 关键词 |
