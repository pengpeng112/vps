只读核查完成，未修改文件，也未连接现网。本次结论来自两份报告、仓库源码及官方资料；报告中的“实测”仍属于 ZCode 的采集记录。

**四个重点结论：TLS 论断有依据，但唯一根因未闭环；BWG 风险需要生效配置确认；R4“半开僵尸”证据不足；备份只能确认部分副本一致，不能直接认定恢复链健康。**

1. **TLS 与密码管理器：机制成立，“与 DOM 无关”说得过满。**  
   [bug报告:28](F:/vps/verify/round-1/bug报告_zcode.md:28) 的 SAN 确实不覆盖 `127.0.0.1`。Chromium 源码中，主页面存在证书错误会禁用填充，保存也依赖这一检查，因此该机制有直接依据。[Chromium 源码](https://chromium.googlesource.com/chromium/src/+/refs/heads/main/chrome/browser/password_manager/chrome_password_manager_client.cc)  
   但 [bug报告:30](F:/vps/verify/round-1/bug报告_zcode.md:30) 尚不能据此排除其他原因：本地 Vue 源码不等于线上渲染 DOM；[Login.vue:31](F:/vps/mgmt/frontend/src/views/Login.vue:31) 在密码通过后移除密码表单，随后才完成 TOTP，仍需验证浏览器对该流程的识别，以及保存设置、站点拒绝列表、浏览器版本。准确表述应是“证书错误是明确的阻断因素，是否还有其他因素待验”。

2. **BWG 应优先核实，但主配置文件不足以证明公网 root 密码登录实际开放。**  
   [证据:40](F:/vps/verify/round-1/证据_现网实查.md:40) 明确只查了主文件，未展示 `Include`、`Match`、启动参数和 `AuthenticationMethods`。应按实际监听端口、用户及来源执行 `sshd -T -C …`；该命令会展开对应的生效配置。[OpenSSH 手册](https://man.openbsd.org/sshd)  
   失败记录和 fail2ban 说明受到扫描，不能证明扫描者能进入密码认证；[证据:46](F:/vps/verify/round-1/证据_现网实查.md:46) 的 `last` 也不能排除无终端的远程命令、转发或其他入侵。另，[证据:32](F:/vps/verify/round-1/证据_现网实查.md:32) 的“`PasswordAuthentication no` 等价 `prohibit-password`”不准确：后者还禁止 root 的 keyboard-interactive，TX 未展示该项。[OpenSSH 配置说明](https://man.openbsd.org/sshd_config)

3. **R4 的“半开僵尸”定性不成立，当前只能确认备用 health 不通。**  
   [证据:62](F:/vps/verify/round-1/证据_现网实查.md:62) 的“进程存在＋curl `000`”无法区分 SSH 半开、认证尚未完成、转发被拒、目标端口关闭、温备未启动等情况。尤其 [温备记录:137](F:/vps/docs/plans/温备部署执行记录-20261001.md:137) 明确记录过：TX 温备待命时 `18444=000` 属预期。两小时无新认证日志也可能只是长连接正常存续。  
   缺失证据是实例完整参数、连接所到 sshd、两端 TCP 状态、本地监听、实际 `MGMT_TARGET`、目标机 8443 状态及客户端错误日志。[证据:63](F:/vps/verify/round-1/证据_现网实查.md:63) 检查 root 公钥前，也应先确认 28722 是否终止于该机。**无第四端点、无 discovery cron 是明确缺口；“从未补齐”则不能由当前快照证明。**

4. **备份“健康”结论覆盖范围过大，而且时间轴需要核对。**  
   [证据:12](F:/vps/verify/round-1/证据_现网实查.md:12) 支持所选三个包字节一致；[证据:13](F:/vps/verify/round-1/证据_现网实查.md:13) 对 DO 包只核大小、时间，尚未证明内容一致。两者都没有证明当期包完整、数据库能恢复、签名密钥及部署配置匹配、恢复脚本确实加载最新数据。历史上已有“恢复命令跑完但数据未更新”的先例：[当前状态:134](F:/vps/docs/current/当前状态.md:134)。  
   此外，既有记录是 DMIT **03:30 PDT**：[当前状态:81](F:/vps/docs/current/当前状态.md:81)。若时区未改，10-10 10:00 CST 时当天备份尚未到点，10-09 包是合理最新包；但 [证据:11](F:/vps/verify/round-1/证据_现网实查.md:11) 又声称已有 10-10 03:30 日志，需补服务器时区和日志原始时间。当前更准确的结论是“备份推送及部分副本一致性已验证，恢复能力未验证”。

5. **TLS 修复方案写错服务，且不能承诺心跳不受影响。**  
   [bug报告:45](F:/vps/verify/round-1/bug报告_zcode.md:45) 写的是 API 容器挂证书；实际仓库配置由 **web/Nginx** 终止 TLS：[compose.prod:11](F:/vps/mgmt/deploy/docker-compose.prod.yml:11)、[prod-nginx:7](F:/vps/mgmt/deploy/prod-nginx.conf:7)。仅重启 API 不会让 Nginx 加载新证书。重试也只意味着可能恢复，期间请求仍可能失败。重签还必须解决信任链，补 SAN 本身不会消除自签信任错误。

6. **遗漏的高风险：路由器隧道密钥可能具有 root 命令执行权限。**  
   [setup-mgmt-tunnel.sh:84](F:/vps/mgmt/agent/setup-mgmt-tunnel.sh:84) 生成的授权行只有 `no-pty`、转发白名单等，没有强制命令限制。**`no-pty` 不禁止执行无终端命令，`permitopen` 只约束转发。** 若现网沿用该模板且没有额外服务端限制，取得任一路由器隧道私钥就可能执行 VPS root 命令，而不只是建立隧道。[OpenSSH 授权密钥说明](https://man.openbsd.org/sshd)  
   这应核查每条设备授权行及对应生效配置；[证据:27](F:/vps/verify/round-1/证据_现网实查.md:27) 仅统计 `permitlisten` 行数不足以评估权限。

7. **主机密钥固化存在失败后放宽信任的路径。**  
   [setup-mgmt-tunnel.sh:34](F:/vps/mgmt/agent/setup-mgmt-tunnel.sh:34) 先移走整个 `known_hosts`，随后获取指纹失败在第 38 行直接退出，没有恢复；成功写入也没有保留其他主机条目。第 40 行通配恢复遇到多个历史备份还可能失败。  
   而 [mgmt-tunnel-v3:90](F:/vps/mgmt/agent/init/mgmt-tunnel-v3:90) 在条目缺失时自动使用 `-y`。因此一次 pin 失败可能让后续重连重新接受未知密钥。第 45 行接受密钥后，也未核对实际写入密钥的指纹是否仍是预期值。这是源码风险，现网是否触发未验证。

8. **选路与 watchdog 会把 HTTP 错误页面当健康。**  
   [mgmt-port-discovery.sh:16](F:/vps/mgmt/agent/mgmt-port-discovery.sh:16)、[tunnel-watchdog.sh:9](F:/vps/mgmt/agent/tunnel-watchdog.sh:9) 只判断 curl 退出码，未使用 `--fail` 或比较 HTTP 状态。返回 401/404/502/503 时 curl 通常仍成功退出，可能选中失效主端点，或停止自愈。可用隔离 HTTP 服务返回 503 复现，随后检查 discovery 是否仍写入该端口。`health=200` 的报告结论也不能替代该脚本逻辑检查。

9. **“协议面干净”遗漏了 Hysteria2 服务端身份验证缺口。**  
   [template.go:106](F:/vps/mgmt/backend/internal/clash/template.go:106) 固定生成 `skip-cert-verify: true`，未提供 CA 或证书指纹验证。密码验证的是客户端权限，不能替代客户端验证服务端身份；流量被引向伪服务端时存在凭据和代理流量风险。官方提供 CA、证书指纹等验证方式。[Hysteria TLS 配置](https://v2.hysteria.network/docs/advanced/Full-Client-Config/#tls)  
   这是明确的模板行为，当前设备是否应用该模板尚未核实；它已经足以否定 [证据:36](F:/vps/verify/round-1/证据_现网实查.md:36) 的“BWG 是唯一实质风险”。

10. **旧 setup 脚本可覆盖掉现有冗余配置，并与 v3 键名不兼容。**  
    [setup-mgmt-tunnel.sh:69](F:/vps/mgmt/agent/setup-mgmt-tunnel.sh:69) 直接重写配置，仅保留单端点，写的是 `VPS_SSH_PORT`；[mgmt-tunnel-v3:69](F:/vps/mgmt/agent/init/mgmt-tunnel-v3:69) 读取 `VPS_PORT`。再次运行该脚本会丢失备用端点，并可能触发默认 **DO 单通道降级**。这是可由源码直接复现的运维陷阱。

11. **模板还有未校验字符串进入 YAML 的风险。**  
    [nodes.go:33](F:/vps/mgmt/backend/internal/httpserver/nodes.go:33) 未校验 SNI/Flow/FP，而 [template.go:196](F:/vps/mgmt/backend/internal/clash/template.go:196) 将其直接插入裸 YAML。隔离输入例如 SNI=`www.microsoft.com\n    interface-name: lo`，会生成额外配置字段；其他输入也可能破坏配置。入口需要管理员权限，因此这是配置完整性和误操作风险，不是匿名攻击入口。

12. **舰队健康和 DO 退役判定仍有证据缺口。**  
    [证据:52](F:/vps/verify/round-1/证据_现网实查.md:52) 对 R2/R3 只展示两个端点，却标为四端点完整健康；[证据:77](F:/vps/verify/round-1/证据_现网实查.md:77) 已承认未直接核心跳新鲜度，查询成功本身也不证明时间持续推进。  
    [证据:85](F:/vps/verify/round-1/证据_现网实查.md:85) 据 DMIT 排障口即可勾销雅典娜迁移任务，同样过早：还需设备实际 API 地址、DMIT 心跳连续更新及 DO 停更等证据。R5 无监听也不能单独定位为 WAN/电源问题；[证据:57](F:/vps/verify/round-1/证据_现网实查.md:57) 的 R6“10-10 刚刷”还与 [当前状态:5](F:/vps/docs/current/当前状态.md:5) 的 10-09 接入记录冲突，应核对采集时点和设备身份。
