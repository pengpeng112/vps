**这份分析覆盖了三个问题的表面结构，但没有完整、忠实地回答原需求；初审“无臆测项”的声明不成立。** 较稳妥的判断是：按仓库记录，R4 的 REALITY 主链路当时可用，CF 链路曾打通；尚不足以认定它能防封号，或在 66云被封后可靠、自动地恢复业务。

本次全程只读，未修改文件、未运行仓库脚本、未登录生产设备。以下现网判断均来自标注时间的仓库记录；外部机制和产品政策另核查了一手资料。

1. **原需求中的“封号”被擅自缩窄成了“封 IP”。**

   方案第 7 行直接把第二问改述为“能否防止 IP 被封”，初审只在第三问末尾补充服务商封号。它遗漏了另一种重要含义：用户访问的网站或平台封禁账号。REALITY、HY2、CF 中转都不能据此提供账号安全保证；必须先区分目标平台账号、66云账号、CF 账号，以及网络层 IP 封锁。

   因而，“三个问题都有对应章节”不等于忠实满足需求。出处：[原需求及其改述](F:/vps/review/round-cf-1030/方案.md:3)、[初审对封号的处理](F:/vps/review/round-cf-1030/审查_zcode.md:27)。

2. **CF 被误称为“异出口”；按文中拓扑，它改变的是进入源站的路径。**

   当前链路是“R4 → CF → GIA → 目标网站”。如果 GIA 的出站仍为直连，目标网站看到的仍是 GIA 的公网出口，**不会因为前面套了 CF 就变成 CF 出口 IP**。

   所以需要区分：

   - 中国至 GIA 的直接路径被阻断：CF 路径可能绕开。
   - 目标网站拒绝 GIA 出口 IP：当前 CF 链路不能据拓扑解决。
   - GIA 服务器停机、被停服或出站故障：REALITY、同机 HY2、CF 源站同时受影响。

   这是依据方案拓扑的推断；本次没有查询源站实际出站配置。出处：[节点及 CF 拓扑](F:/vps/review/round-cf-1030/方案.md:17)、[初审“唯一异出口”表述](F:/vps/review/round-cf-1030/审查_zcode.md:14)。

3. **“管理面完全独立、66云故障不影响管理”的表述过宽，且与下午执行记录冲突。**

   下午记录明确写出心跳/API 链路：

   `设备 → 广州 → GIA → DMIT`

   并记载 GIA 上中继仍指向旧 DMIT IP，导致全舰队心跳失败。这个具体故障证据，比架构图中“广州运行 Go API + PostgreSQL”的笼统声明更能说明当时依赖。

   广州反向 SSH 救援口可以具有独立性，但这不能外推为网页、心跳、任务下发、数据库全部独立。若 66云只是被大陆直连封锁，国外中继段可能继续工作；若 66云停服，依赖它的 API 中继就可能一并中断。出处：[下午执行记录 §二](F:/vps/docs/plans/CF试点与全链路故障修复执行记录-20261030.md:19)、[架构标准](F:/vps/docs/current/舰队管理架构标准-20261030.md:9)、[方案的独立性声明](F:/vps/review/round-cf-1030/方案.md:39)。

4. **CF 配置会被平台覆盖，不能称作“预期、无害”。**

   执行记录承认 CF 节点未进入平台节点表，下次发布会清除；源码也只支持 REALITY 与 HY2。假如用户相信 CF 是封锁后的救命通道，一次普通配置发布就能把它删除，这直接损害预案。

   `desired=accepted=21` 只能描述版本记录，不能证明设备文件与平台内容一致，更不能证明以后不会重发。出处：[执行记录遗留项](F:/vps/docs/plans/CF试点与全链路故障修复执行记录-20261030.md:44)、[模板协议分支](F:/vps/mgmt/backend/internal/clash/template.go:91)。

5. **方案遗漏了决定 YouTube 实际走向的 `US-MEDIA` 组。**

   方案只展示 `PROXY → AUTO → NODE-PICK`，但仓库模板把 YouTube、googlevideo 等流量交给 `US-MEDIA`。当天 09:05—09:07 的实测又发现 R4 的 `US-MEDIA=[COMPATIBLE]`，属于已知媒体分流问题。

   下午记录只说明把 CF 加入 `PROXY/NODE-PICK`，没有附出修后 `US-MEDIA` 成员和普通 LAN 流量的命中链。因此，即使直接测试 CF 节点成功，也不能证明日常 YouTube 已自动使用它。本次不能断言下午仍有空组，只能确认验收证据缺失。出处：[媒体规则](F:/vps/mgmt/backend/internal/clash/golden.yaml:203)、[上午实测](F:/vps/docs/reports/Hysteria2增量方案评估-20261030.md:7)、[下午添加范围](F:/vps/docs/plans/CF试点与全链路故障修复执行记录-20261030.md:40)。

6. **“YouTube 长期封数据中心 IP，R1 之外首次可达”与仓库已有证据不符。**

   10 月 29 日晚的记录已写明：R1/R3/R5 均经 GIA 出口取得 YouTube 首页 HEAD=200。R4 次日上午失败时又存在媒体空组问题。因此，不能把 R4 的失败直接归因于 YouTube 对数据中心 IP 的信誉封锁。

   首页 200 也不等于视频播放成功，但它足以否定“其他设备长期都不可达”的绝对说法。出处：[三台复核结果](F:/vps/docs/plans/远程不断线修复执行记录-20261029.md:83)、[方案的归因](F:/vps/review/round-cf-1030/方案.md:51)、[初审的首次声明](F:/vps/review/round-cf-1030/审查_zcode.md:14)。

7. **CF 的成功案例不足以证明“作为兜底合格”。**

   方案给出的业务连接成功率只有约三至五成，却据此认可无人干预兜底。对于连续办公、登录会话、上传等业务，这个水平是否可接受，需要明确业务标准，不能由评审自行认定。

   同时，记录没有提供成功率的样本数、测试时间跨度、失败阶段、视频播放、持续吞吐和长连接结果。339/366ms 的两个成功值，只能证明曾成功，不能代表可靠性。出处：[方案实测描述](F:/vps/review/round-cf-1030/方案.md:37)、[初审“兜底合格”](F:/vps/review/round-cf-1030/审查_zcode.md:14)、[试点结果](F:/vps/docs/plans/CF试点与全链路故障修复执行记录-20261030.md:36)。

8. **“源站日志健康，因此瓶颈确定是联通国际段波动”证据不足。**

   成功请求到达源站，能证明这些请求的回源正常；不能解释没到达源站的失败请求。后者还可能涉及 TCP、TLS/SNI、CF 边缘处理、WAF、WebSocket 升级等阶段。

   需要把每次失败与客户端错误、CF 状态码/Ray ID、源站日志按时间对应，才能缩小故障范围。“国际段波动”目前应标为候选解释。出处：[执行记录中的定位结论](F:/vps/docs/plans/CF试点与全链路故障修复执行记录-20261030.md:42)；CF 官方说明 [WebSocket 初始请求仍受 WAF 等机制处理](https://developers.cloudflare.com/network/websockets/)。

9. **“≤2 分钟自动切换”“≤2×120 秒恢复”都不是已证明的上限。**

   `lazy:false` 的作用是持续进行健康检查；它不提供业务恢复时限保证。发现故障还涉及探测周期、超时、嵌套组状态、DNS、备用链路当时是否健康，以及应用重试。

   方案自身还写 `AUTO interval=300`，初审却按两个 120 秒推算。更关键的是：如果主节点的 gstatic 探测成功，而目标业务失败，fallback 未必切换。已有 TCP 会话也不会自动迁移到另一条代理连接。

   已标死的 BWG 节点通常不会在每个新连接上逐个等待超时；真正问题是健康状态是否及时、准确，而不是把成员顺序机械换算成等待时间。出处：[方案切换链及承诺](F:/vps/review/round-cf-1030/方案.md:14)、[初审恢复估计](F:/vps/review/round-cf-1030/审查_zcode.md:33)；[mihomo v1.19.31 fallback 源码](https://raw.githubusercontent.com/MetaCubeX/mihomo/v1.19.31/adapter/outboundgroup/fallback.go)、[健康检查源码](https://raw.githubusercontent.com/MetaCubeX/mihomo/v1.19.31/adapter/provider/healthcheck.go)。

10. **自动链路末尾有 `DIRECT`，却没有评估直连回退的含义。**

    这不是“代理全部失败就安全停用”的策略。如果外层健康判断允许 DIRECT，部分业务可能改走本地公网出口；目标可达性、来源地址和隐私属性都会变化。对于原需求中的账号安全，这也需要明确说明。

    另一方面，mihomo fallback 在成员全不健康时仍可能返回首成员，不能理解成“无论怎样都会找到 CF”。出处：[方案的 DIRECT 回退](F:/vps/review/round-cf-1030/方案.md:14)、[本地模板](F:/vps/mgmt/backend/internal/clash/golden.yaml:88)；[对应版本 fallback 实现](https://raw.githubusercontent.com/MetaCubeX/mihomo/v1.19.31/adapter/outboundgroup/fallback.go)。

11. **“换过 IP 仍失败，所以证明 GFW 按运营商 IP 段封锁”不成立。**

    两次地址连接失败，不能直接识别封锁粒度。还需排除服务监听、云安全组、路由、协议配置、认证、SNI、临时阻断等因素，并对同段不同地址、不同端口、不同来源做对照。

    当天上午报告已谨慎指出：BWG:443 未建立连接，不能证明全 TCP 封锁。待审方案没有补出足够的 TCP/UDP 对照证据，却把它升级成确定根因。“换 IP 一定无效”也随之失去依据。出处：[方案的因果结论](F:/vps/review/round-cf-1030/方案.md:43)、[上午报告的限制](F:/vps/docs/reports/Hysteria2增量方案评估-20261030.md:9)。

12. **“GFW 无法封 CF 共享边缘”是核心错误；SNI 分别处理也没有得到充分讨论。**

    共享 IP 可能提高整体封锁的附带成本，但不构成技术保证。CF 官方明确说明：其 IP 可能被国家或 ISP 阻断，也可能只阻断某些前缀。无需封掉整个 CF，就能影响这条通道。[CF 官方说明](https://developers.cloudflare.com/support/troubleshooting/general-troubleshooting/potential-isp-blocking/)。

    当前节点仍发送自己的 SNI；钉住 CF IP 不会隐藏它，也不是域名前置。公开项目曾报告 CF 地址上不同 SNI 的放行/阻断差异，但这类历史观察不能直接证明 2026-10-30 山东联通存在某套固定“分级规则”。应在同一 IP、同一端口对不同 SNI 做有控制的比较，再判断当前失败是否相关。[一手项目观察](https://github.com/GFW-knocker/gfw_resist_tls_proxy#about-gfw-sni-filtering-on-cloudflare-ips-skip-if-you-want)。

    方案据两次成功就宣布“核心命题验证成立”，明显超出了实验能支持的范围。出处：[执行记录的核心命题](F:/vps/docs/plans/CF试点与全链路故障修复执行记录-20261030.md:42)、[初审共享 IP 论证](F:/vps/review/round-cf-1030/审查_zcode.md:22)。

13. **REALITY 能提高抗探测能力，不等于“能防协议识别”。**

    初审把两者直接画等号，并使用“目前 TCP 类最强”这样的比较结论，却没有提供比较标准或实验。

    uTLS 官方明确提醒：ClientHello 模拟可能不完美，而且不模拟 ClientHello 之后的行为。因此 `client-fingerprint: chrome` 不能证明整条连接等同于浏览器；连接时长、流量形态、目标与地址关系、实现版本等仍需考虑。REALITY 官方也承认某些回落限速设置会形成特征。[uTLS 说明](https://github.com/refraction-networking/utls#parroting)、[REALITY 配置说明](https://github.com/XTLS/REALITY)。

    同样，本次也没有证据认定 GFW 已精准识别并封锁这台 R4 的 REALITY。正确结论应保留这两边的不确定性。出处：[初审的防识别保证](F:/vps/review/round-cf-1030/审查_zcode.md:12)、[当前模板的指纹默认值](F:/vps/mgmt/backend/internal/clash/template.go:184)。

14. **CF 免费版 WebSocket 的技术支持与使用政策被混在一起了。**

    官方明确支持所有套餐使用 WebSocket。因此不能仅凭“免费版”认定这种连接不受支持。[官方功能说明](https://developers.cloudflare.com/network/websockets/)。

    但这也不意味着免费 CDN 承诺承载无限代理、视频或大文件。当前 CDN 条款保留了对未经相应付费服务承载视频或不成比例大文件流量进行限制的权利。这是方案把 YouTube 当作主要验收业务时必须纳入的政策风险；也不能反过来断言已经违规或必然封号。[现行 CDN 条款](https://www.cloudflare.com/service-specific-terms-application-services/#content-delivery-network-free-pro-or-business)。

    初审只写成“理论上可能限速、未见实证”，低估了已有明确条款这一层。出处：[初审对免费版的评估](F:/vps/review/round-cf-1030/审查_zcode.md:23)。

15. **CF 正常运行也会断 WebSocket，需要独立的会话恢复验收。**

    官方明确说明：网络更新可能终止 WebSocket，连接空闲也可能被关闭，并建议心跳保活。方案没有说明当前组合是否发送适当的 WebSocket 心跳、断线后业务如何重连。

    不能把所有断连归为封锁或国际段拥塞，也不能把普通 TCP keepalive 配置当作已经完成应用层保活验收。[官方连接行为说明](https://developers.cloudflare.com/network/websockets/)。出处：[试点验收范围](F:/vps/docs/plans/CF试点与全链路故障修复执行记录-20261030.md:36)。

16. **优选 IP 方案缺少可复现方法，多加一个 CF IP 也不是独立灾备。**

    记录没有交代 0.79s 测的是 TCP、TLS、普通 HTTP，还是实际 VLESS 代理。实际业务使用 2053、特定 SNI/Host 和 WebSocket 路径，不能用另一个端口或测试域名的速度代替。

    CF 使用 anycast，固定 IP 不等于固定机房、运营商路径或长期质量；同一 IP 的路由也可能变化。[CF 对 anycast 路由的说明](https://developers.cloudflare.com/support/troubleshooting/general-troubleshooting/geographic-traffic-routing/)。

    第二个 CF IP 可以分散部分前缀和路径故障，但仍共享 CF、域名、账号和 GIA 源站。它不能解决域名封锁、CF 账号停用或源站消失。出处：[优选记录](F:/vps/docs/plans/CF试点与全链路故障修复执行记录-20261030.md:41)、[初审 CF2 建议](F:/vps/review/round-cf-1030/审查_zcode.md:35)。

17. **66云停服后的“5—10 分钟恢复”没有经过验证，也没有完整的可重放部署材料。**

    在其指向的执行记录中，我看到的是部署摘要；参数 JSON 包含节点参数和回退命令，但并非完整的源站重建脚本。这不证明其他位置绝对没有脚本，却不足以支持“已固化、可重放”的声明。

    恢复还依赖备用 VPS 已可管理、程序和证书已准备、端口及防火墙正确、CF 能回源、设备参数兼容，以及验收通过。若事先已有独立供应商的热备源站，恢复可以很快；如果故障后才采购和部署，5—10 分钟只能是愿望。

    BWG/DMIT 即使大陆直连失败，仍可能作为 CF 的国外源站，这是值得验证的现有资源利用方向。出处：[初审恢复承诺](F:/vps/review/round-cf-1030/审查_zcode.md:38)、[部署摘要](F:/vps/docs/plans/CF试点与全链路故障修复执行记录-20261030.md:38)、[参数文件](F:/vps/backups/.cf-node-params.json)。

18. **“域名被封，花两美元换域即解”遗漏了恢复依赖。**

    更换域名至少可能涉及注册、NS 激活、证书签发、CF 配置、设备 SNI/Host 更新，以及全设备配置发布。当前平台又不支持该 WS 节点格式。

    需要区分：原域名/SNI 被阻断、域名注册被停用、CF zone 被限制、CF 整个账号被停用。后两者不能假定在同一账号换个域名就解决。域名购买价也不等于完整恢复成本。出处：[方案换域论断](F:/vps/review/round-cf-1030/方案.md:62)、[初审残余风险](F:/vps/review/round-cf-1030/审查_zcode.md:23)、[域名部署步骤](F:/vps/docs/plans/Cloudflare域名中转方案-k3x9m2.top-20261030.md:37)。

19. **Argo Smart Routing 与 Cloudflare Tunnel 需要分开讨论，不能当作前段质量的根治办法。**

    CF 官方当前标注 Argo 与 WebSocket 不兼容。Cloudflare Tunnel 则是源站主动连接 CF 的回源方式，两者不是同一功能。[WebSocket 兼容性](https://developers.cloudflare.com/network/websockets/)、[Tunnel 原理](https://developers.cloudflare.com/tunnel/)。

    在 GIA 安装 Tunnel 可以改变回源和源站暴露方式，但 R4 仍要连接 CF 边缘，因此不能据此承诺解决山东联通至 CF 的问题。出处：[方案将其列为稳定性根治候选](F:/vps/review/round-cf-1030/方案.md:64)。

20. **设计中的 443 与实装的 2053 没有同步，移植预案容易照错。**

    原方案图和设备推送步骤写 443，事实包与实际参数写 2053。2053 本身属于 CF 支持的 HTTPS 端口，当前使用它没有因此被否定。[官方端口列表](https://developers.cloudflare.com/fundamentals/reference/network-ports/)。

    但不能只把客户端改为 443，就假设 CF 自动回源至 GIA:2053；需要明确回源端口映射或相应前置服务。GIA:443 已承载 REALITY，也不能直接拿来接普通 WS 回源。出处：[设计端口](F:/vps/docs/plans/Cloudflare域名中转方案-k3x9m2.top-20261030.md:27)、[实装端口](F:/vps/review/round-cf-1030/方案.md:28)。

21. **CF 的 `Full` 模式缺少源站身份校验，初审未指出。**

    `Full` 会加密回源，但不验证源站证书是否可信、过期或匹配域名。十年自签证书并不能弥补这一点。CF 官方明确提示了连接被劫持并替换证书的风险。[Full 模式限制](https://developers.cloudflare.com/ssl/origin-configuration/ssl-modes/full/)。

    后续正式方案应评估受信任证书或 CF Origin CA 配合 `Full (strict)`，再视服务支持情况考虑回源认证。这些是待授权实施事项，本次未改。出处：[实装 SSL 模式](F:/vps/docs/plans/CF试点与全链路故障修复执行记录-20261030.md:39)；[Strict 要求](https://developers.cloudflare.com/ssl/origin-configuration/ssl-modes/full-strict/)。

22. **引入 CF 后，代理的信任边界改变了。**

    当前是客户端至 CF、CF 至源站两段 TLS。对于普通、未另外加密的 VLESS-over-WS，CF 作为 TLS 终止方有能力接触代理协议数据，包括认证及目标地址；不能把它描述成完全看不到内容的透明转发层。

    内层 HTTPS 仍可保护网站内容，因此这不等于 CF 能读取所有 HTTPS 正文。但方案应说明这个区别，而不是只谈抗封锁收益。出处：[当前传输协议](F:/vps/review/round-cf-1030/方案.md:30)；[CF 双证书结构](https://developers.cloudflare.com/ssl/concepts/)、[VLESS 对外层安全的要求](https://xtls.github.io/en/config/outbounds/vless.html)。

23. **“服务器真实 IP 不暴露”只能作有限表述。**

    CF 节点连接时，设备的对端确实是 CF；但同一设备仍在直接使用 GIA 的 REALITY/HY2，GIA 地址也早已存在于设备配置和历史记录中。因此，这次增加 CF 不能让已暴露的地址重新变得不可知。

    记录还写明源站 WS 端口从外网直达通过，没有提供仅允许 CF 回源的证据。若目标包括保护该端口，应另评估限定回源来源或认证，且不能波及现有 SSH/REALITY。出处：[同时保留的直连节点](F:/vps/review/round-cf-1030/方案.md:17)、[外网直达验证](F:/vps/docs/plans/CF试点与全链路故障修复执行记录-20261030.md:38)；[CF 源站保护建议](https://developers.cloudflare.com/fundamentals/security/protect-your-origin-server/)。

24. **HY2 的价值判断基本正确，但安全与实测条件没有补齐。**

    同机 HY2 可以应对部分仅限 TCP 的路径故障，但不构成服务器或供应商冗余。UDP/QUIC 本身也可能受限；已有一手研究证实了 GFW 的 QUIC/SNI 阻断机制，不能把 UDP 当作天然不可识别。[USENIX 研究](https://gfw.report/publications/usenixsecurity25/en/)。

    另外，当前本地模板硬编码 `skip-cert-verify: true`，没有输出证书固定等字段。密码认证并不能替代客户端对服务器身份的验证。正式评审应读回实际客户端配置，核实证书验证、版本及必要的混淆兼容性。出处：[HY2 模板](F:/vps/mgmt/backend/internal/clash/template.go:96)；[Hysteria 官方 TLS 配置](https://v2.hysteria.network/docs/advanced/Full-Client-Config/#tls)。

25. **R4 当天已发生的配置故障，比假设中的下一次封锁更需要纳入可行性结论。**

    执行记录记载：R4 因 YAML 缩进损坏崩溃循环约 2 小时 17 分钟。这说明所有网络备用节点都可能被同一个配置文件错误同时击穿；多加节点无法解决这个共同故障。

    后续方案需要覆盖有效配置保留、提交前校验、加载失败回退，以及重启后的持久性验收。还应保留因果证据限制：“tasks 表没有平台任务、渲染器缩进正常”可以排查部分路径，但不能仅凭这些锁定某次手工写入的执行者。出处：[实际故障及根因分析](F:/vps/docs/plans/CF试点与全链路故障修复执行记录-20261030.md:12)。

26. **参考文档中另有真实的凭据治理问题。**

    被要求必读的架构标准第 64 行含明文 SSH 登录口令。这里不复述其内容。它与仓库“凭证受控读取、不进入普通文档或报告”的规则冲突，也使跨 AI 分发参考材料存在实际泄露风险；归档或忽略提交本身不能完成脱敏。

    本次没有移动文件或轮换任何凭据。出处：[含凭据的位置](F:/vps/docs/current/舰队管理架构标准-20261030.md:64)、[仓库凭据规则](F:/vps/AGENTS.md)。

原问题应按故障类型回答，不能统一写成“有 CF 就有保障”：

| 用户关心的情况 | 当前证据支持的回答 |
|---|---|
| R4 通道现在是否可以 | 记录支持主链路当时可用；CF 是曾成功、质量尚未验收的试点。 |
| 能否防止平台账号被封 | 没有这种保证；原分析没有充分回答这一含义。 |
| 66云仅被大陆直连封锁 | CF 路径有条件继续可用，前提是 CF 入口、SNI、回源和源站均正常。 |
| 66云停服、封号或服务器消失 | 当前同机三条路径一起失效，需要独立源站；尚无已演练的恢复时限。 |
| 目标网站拒绝 GIA 出口 | 当前 CF 中转通常仍使用该出口，不能据此解决。 |
| CF 自身入口、域名或账号受限 | 优选 IP、换域、换源站分别只解决其中一部分，须准备对应预案。 |

**最需要先补齐的是独立源站、可持续发布的 CF 配置、真实业务路由验收，以及保留广州 SSH 条件下的单台故障切换演练。** 在这些证据出现前，应撤回“防协议识别：能”“CF 兜底合格”“无需人工干预”“5—10 分钟恢复”等确定性承诺。
