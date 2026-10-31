# SN-R4 YouTube 修复：层1（单绑TX）+层2（Hysteria2）执行手册 — 2026-09-30

前置结论（当日诊断）：R4 部署于客户移动网点（小米路由后，公网 120.221.100.55），
TX 节点 ICMP 裸丢 20—30%、DO 节点 VLESS 握手失败 → YouTube 并发请求全超时。
设备配置全绿，属链路物理特性；Hysteria2(UDP+BBR) 是不动客户带宽前提下唯一候选。

## 状态记录

- 层2b 本地代码已完成并通过 ci-local（clash/httpserver/crypto 全绿）：
  - template.go：NodeRender+Proto/Password；hy2 出站渲染（skip-cert-verify）；MaskYAML 掩 password
  - nodes.go：nodeParams+password；validateForProto；create 白名单 vless-reality|hysteria2；PATCH 合并
  - configs.go：NodeRender 传递 Proto/Password；bindingRevision 指纹含协议+密码
  - crypto/wg.go：MaskNodeParam password 前后 2 位
- 凭据：F:\vps\backups\TX-HY2节点凭据-20260930.md（40 位密码，不入 git）
- TX 补丁脚本：本目录 tx_patch.py3（在 TX 上执行 apply/rollback/status）
- 执行中断事件：~10:35 起公司→DO TCP 全断（ICMP 丢 25%）、TX 新 SSH 连接 EOF（疑 fail2ban/平台防护/公司出口劣化）；已建 PC→TX→DO 的 18444 API 隧道存活并用其完成核查。所有执行动作等待 TX:22 或 DO:28722 直连恢复。

## 执行顺序（通道恢复后）

### P1 TX sing-box 加 hy2 入站
1. scp tx_patch.py3 → TX:/tmp/；`sudo HY2_PW=<档案密码> python3 /tmp/tx_patch.py3 status`（确认现网入站清单）
2. `apply`（自动：备份 config.json.bak-时间戳 → 自签证书 /etc/sing-box/hy2/ → 加 UDP/443 hysteria2 入站 → sing-box check → restart → 失败自动回滚）
3. `sudo ufw allow 443/udp`（若平台防火墙仍拦 UDP 443，需控制台放行——唯一可能需要用户操作的点）
4. 验收：vless 不受影响（R4 delay 探测 VPS-TX-SG-02 正常 + PC 经雅典娜 gstatic 204）；`ss -ulpn | grep :443`
5. 回滚：`python3 /tmp/tx_patch.py3 rollback` + `sudo ufw delete allow 443/udp`

### P2 后端部署（VPS /opt/mgmt）
1. 同步 4 个改动文件到 VPS 对应路径（先 cp 原件为 .bak-20260930）
2. `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build api`（web 不动）
3. 验收：18444 health 200；GET /api/v1/nodes 正常；设备心跳不中断
4. 回滚：恢复 .bak 原件 → 重新 build api

### P3 层1 解绑 DO（R4）
1. 备份行：`SELECT * FROM node_assignments WHERE device_id='rtr-cd473f'` 存档
2. `DELETE FROM node_assignments WHERE device_id='rtr-cd473f' AND node_id=1;`（DO=VPS-SG-01）
3. 不变量核验：R4 仍有且仅有一个 primary（VPS-TX-SG-02）
4. 回滚：INSERT 原行

### P4 层2c 建节点+发布
1. POST /nodes {name:"TX-HY2-SG-01", host:43.134.46.44, port:443, proto:"hysteria2", params:{password:…}}
2. POST /assignments/batch {device_ids:["rtr-cd473f"], node_id:<新>, is_primary:true, apply:false}
3. POST /devices/rtr-cd473f/configs/preview → 人工核验：NODE-PICK 成员 [TX-HY2-SG-01, VPS-TX-SG-02]，gstatic 规则位次不变，DO 直连规则仍在（WG endpoint）
4. POST /configs/apply → 等 agent 任务 ok
5. 点位验收（经 DO→13224 SSH）：
   - md5/版本变化、NODE-PICK now=TX-HY2-SG-01
   - delay 探测两节点
   - youtube generate_204 ×8（目标 ≥7/8 且 <2s）、gstatic ×5、google ×5、baidu ×5
   - 心跳在线、-R 隧道活（本会话自身）、WiFi 客户端连接走 PROXY 链
6. 回滚：assignments batch 把 VPS-TX-SG-02 设回 primary（或恢复 DO 绑定）→ apply；hy2 节点 disable

## 风险与不触碰清单
- 不触碰：设备 mgmt-tunnel/agent/access-fw.sh；VPS sshd/fail2ban/ufw；雅典娜与 R2/R3 的任何配置
- P2 重启 api 容器有几秒心跳空窗（<超时阈值，不触发离线判定）
- 公司→DO 中断期间雅典娜心跳会闪断（与本工作无关，通道恢复自愈）
- hy2 UDP 若被移动网/平台防火墙压死 → 层2 实测不达标则回滚 P4，保留层1 状态并明确告知
