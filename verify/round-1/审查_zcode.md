# ZCode 独立初审（问题清单草案）— multi-verify round-1

对象：系统/路由器功能核查（冗余备份、远程管理、代理出口安全）+ bug 验证（8444 登录密码保存）。
证据基础：`证据_现网实查.md`（2026-10-10 实查）、`bug报告_zcode.md`。
本清单为草案，待 Kimi/Grok/Codex 独立审查后进分歧表。

| 编号 | 严重度 | 位置 | 问题猜想 | 证据 | 建议修复 | 阻断? |
|---|---|---|---|---|---|---|
| P1 | 高 | BWG 74.82.203.175 /etc/ssh/sshd_config | root 密码登录开启（PasswordAuthentication yes + PermitRootLogin yes），且持续被字典爆破（lastb 1023 条，10-10 01:16 仍在试），仅 fail2ban 兜底 | 证据 C 段 | 改 `PasswordAuthentication no` + `PermitRootLogin prohibit-password`；前置：确认 DMIT→BWG 密钥链路（已验）+ 用户本机是否依赖密码 SSH BWG（需用户确认）；改后保留现有会话验证再退出 | 建议（非阻断提交，但属本轮最高风险） |
| P2 | 高 | R4 rtr-cd473f 隧道 | 管理隧道冗余降级为单点：TX/BWG 备用 -L 隧道半开僵尸（18444/18445=000）、conf 无 VPS4 兜底端点、discovery 是三端口旧版且无 cron 条目 | 证据 D/E 段 | ① `/etc/init.d/mgmt-tunnel restart`（主链路 18443 稳定，闪断<30s）；② conf 补 VPS4=DO；③ discovery 升级四端口版+补 cron（照 R2 现网文件拉齐）；④ 重验四端点 200 | 是（冗余目标未达成） |
| P3 | 中 | 管理台 TLS 证书 / Login.vue | 登录凭据浏览器无法保存：证书 SAN 仅 10.10.0.1，访问 127.0.0.1:8444 永久证书错误，Chromium 在绕过证书错误的页面禁用密码保存气泡；10-02 只修了 DOM 层无效 | bug报告_zcode.md | 重签证书 SAN=127.0.0.1+10.10.0.1+localhost，替换+重启 api 容器，用户导入信任 CA，验收绿锁+弹保存 | 是（用户本轮明确要求处理） |
| P4 | 中 | R3/R4 WAN MAC | 未固定，每次重启随机漂移（R3 今晨已漂移实证 02:e7:5d…→E2:3B:70…）；上级现无绑定暂无实害，但档案失真+未来加绑定即踩坑 | 证据 H1 | 按 flash-snr 规则 uci 固定：R3=WAN 76:a7:c8:e0:4f:23/br-lan :33，R4=:24/:34；ifup wan 生效（闪断<1min） | 否（等用户点头，上轮已给方案） |
| P5 | 低 | DMIT /opt/mgmt-standby/latest-backup.tar.gz | 温备时期残留的 10-01 旧恢复点（11M），与现行备份链无关，存在误恢复风险 | 证据 A 注 | 重命名（如 standby-legacy-20261001.tar.gz）或删除；同步检查 restore-latest.sh 的取用路径 | 否 |
| P6 | 低 | TX sshd | PermitRootLogin yes（密码认证已关，等价 prohibit-password）——语义不严谨 | 证据 B 段 | 改 `prohibit-password` 显式化 | 否 |
| P7 | 低 | DMIT /opt/r4-watch.sh（*/5 cron） | 只读监控 R4 但 ping 旧 WAN 119.190.40.236（10-08 已换 120.221.100.7），监控失效 | 证据 H6 | 更新目标 IP 或退役（R4 已稳定在 DMIT 管理面近一周） | 否 |
| P8 | 低 | R6 排障口归属 | R6（10-10 刚刷）主排障口未核（13226 无监听，可能在 TX），本轮未查证——标注未验证 | 证据 D 段 | 补查 TX 侧 13226 或经管理台 API 核对 R6 心跳与端点 | 否 |
| N1 | 非问题 | DO 22 ufw DENY | 刻意配置（仅留 28722），非故障——本机 22 超时的正确解释 | 证据 B 段 | 无需动作，记录备考 | — |
| N2 | 非问题 | 备份链 | DMIT→TX/BWG 每日 03:30 双推 ok，三处 md5 一致；DO→三处 do-legacy 改道生效 | 证据 A 段 | 无需动作 | — |
| N3 | 非问题 | 雅典娜迁移 | 已迁 DMIT（13228=rtr-5e1a8d），DO 侧 18222 释放、医院口 18322/18422 保留 | 证据 B/D 段 | DO 退役前置清单勾销 D2 | — |

## 未验证项（如实标注）

- R6 主排障口与心跳新鲜度（未直接核）。
- sing-box 版本号与已知漏洞匹配（未查版本）。
- 心跳表精确新鲜度（metrics 列名不符未再查；以排障口监听+18443=200 佐证）。
- "浏览器对证书错误页禁用密码保存"未做自动化二次实证（修复后验收即终验）。

## 验证方法备注

- P1 修复验证：改配置后新开会话密码登录应被拒（Permission denied）、密钥登录正常、现有会话不掉。
- P2 修复验证：R4 上四端口 health 全 200、ps 5 实例、active=18443、排障口 13224 回来、DMIT 侧 13224 监听。
- P3 修复验证：本机浏览器 https://127.0.0.1:8444 出现锁形图标，登录后弹保存提示。
