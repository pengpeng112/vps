# 分册06：已购备用域名接入与冷备归档执行指南（Codex v2，2026-11-18）

> **仅上线6849271.xyz两条备用节点；冷备8364927.xyz用户本轮已确认是7位，仅归档、不建节点、不绑定、不下发。83649271.xyz为误写。**
> 先读[00](00-全局诊断与总体治理蓝图.md)、[07](07-核查结论与执行验收总表.md)、[切换台账](../../operations/Cloudflare多域名与源站应急切换台账-20261118.md)。
> 本轮仅核查修订，以下维护步骤交后续授权执行会话，不是本轮部署记录。

## 第一步：购买已完成，保留后续采购注意事项

无需再次购买、刷机或轮换现有UUID。有效期/续费/账户恢复方式以注册商账户为准，未知不编日期。
删除“¥7永久续费”“某后缀绝不被封”“冷备不进CT”的承诺；价格按当日结算与续费价，冷备即使不下发也可能被DNS/CT公开。

## 第二步：绑定已由用户完成，按表读取核对，不重复修改

| 角色 | 子域名 | 预期账户内A记录 | 端口与边界 |
|---|---|---|---|
| 本次上线HK | hk.6849271.xyz | 191.223.215.12 | TCP2053；用户报告橙云/Full/WS |
| 本次上线BWG | usbwg.6849271.xyz | 74.82.193.113（东京） | TCP2053；禁写us.6849271.xyz |
| 只冷备HK | hk.8364927.xyz | 191.223.215.12 | 只登记，不建节点/不绑定/不发布 |
| 只冷备BWG | usbwg.8364927.xyz | 74.82.193.113 | 同上 |

1. 橙云公开DNS显示CF边缘是正常现象，不能证明源站A/账户归属/SSL/WS设置。用已有受控CF入口读取；没有入口由用户面板确认，不新建全账户token、不让用户把token贴入对话。
2. 源站自签证书下保留Full；**客户端到CF仍严格验链和主机名，不加skip-cert-verify**。未来配好匹配源站证书再单独升级Full(strict)，本批不换证书。
3. 官方Universal SSL窗口常见15分钟至24小时，并非固定10—30分钟。15:05本轮两子域×两边缘已4/4严格TLS及WS101通过，旧“等证书”不能继续当现势；没读账户不冒称控制台Active。

## 第三步：给执行AI的启动任务

```text
在F:\vps先读AGENTS.md、现势、本目录00/07/06。
只上线hk.6849271.xyz→191.223.215.12与usbwg.6849271.xyz→74.82.193.113备用CF节点。
冷备8364927.xyz已确认，仅入账；八位域为误写，不操作。
先完成01必要工具与03最小安全发布门禁，再按第四步/CF01—CF12实施。
保留旧主节点、BWG正常路径、国内DIRECT/DNS、游戏修复；CF永不primary。
不执行QoS/端口跳跃/AnyTLS/重启改时/香港中继。
R2逐条新节点强制路径实测，通过后在线SN串行，雅典娜独立适配，离线不排任务。
保存检查点和三件套；HTTP节点响应可能含WS路径，不直接打印/记录完整响应。
```

## 第四步：执行AI可续跑步骤

### 4.0 范围、锁、基线（CF01）

1. 对拍其他会话；session_guard.py当前不存在，先按01最小包实现/测试，再按真实CLI持gz-prod锁。锁不能阻止不配合的其他机器会话，不能把本地文件锁当全局互斥已证明。
2. 原始备份放ignored受控目录：GZ二进制SHA/服务/迁移记录、节点/绑定/活跃任务；各设备配置原件与SHA、current/desired/highest、9090选择、DNS、meter规则/continuity、cache选择。review只存脱敏证据。
3. 本轮R2=39/39/39、主用8=BWG-HY2；接手重取，不硬编码下一版本。R9=4/5/4，先查旧v5失败，不重放旧任务。
4. 记录旧绑定与优先顺序，新增不删旧节点、不提升CF/GIA为主用。上线判定需新鲜心跳与可用管理口；离线设备单列。

### 4.1 广州最小安全发布门禁（CF02）

不能只写“分册03完成”，必须满足：

1. 本地golden仍有国外裸连fallback，R2现场已为国内fallback；先将已验DIRECT/国内DNS修复合并到源码，保留geolocation-!cn、MATCH,DIRECT、单域策略和游戏五域名。
2. 本地0010与生产水位9不一致。检查真实表**node_assignments**与nodes，隔离库验证后用新二进制**-migrate**正规迁移。不能手工插schema_migrations骗过启动。
3. 合并用途隔离：PROXY普通候选排BULK、节点IP直连保护不漏；不回退近期有效修复。
4. 构建产物关联模板/迁移/源码哈希；投产后核对运行进程实际二进制。健康200之外，preview R2与R5/R9必须保留现场有效规则。
5. 此门禁不要求同时改变DNS全套设计、全舰队库、重启/限速；已有更新吸收后重验，不重装旧包。

### 4.2 TLS、WS逐域逐边缘验证（CF03）

候选104.18.32.7与162.159.135.42，TCP2053。两主备子域×两边缘共4项，各记录来源/时间/目标/SNI/严格证书/101/Accept，不输出UUID/path。候选不保证永久优选。

本轮4/4已过，执行前重测。冷备本批不发WS/代理流量。
可复用只读工具（读取受控参数、只读GZ/R2，文件名不得覆盖历史）：

```powershell
python review/governance_cf_readonly_audit_20261118.py --output review/CF接入前核查-新时点.json
```

另写探测器须系统可信CA+正确SNI，HTTP/1.1 Upgrade带随机key/WS版本13，核验Accept对应key及Upgrade/Connection。**不能用curl -k的400/404判通过；101也还没验证VLESS鉴权。**

| 现象 | 处理 |
|---|---|
| 超时/客户端TLS失败 | 区分到边缘线路、SNI/证书、时钟/CA，多来源对拍；不一律判断待签证书 |
| 400/404 | 检查路径/Host/Upgrade，不是代理通过 |
| 403/挑战/重定向 | 查该域WAF/Access/Redirect/Worker/Origin Rule，不全局关保护 |
| 521/522 | 查源站服务/监听/CF限源/A记录/路由，不对公网全开2053绕过 |
| 525 | CF到源站TLS握手失败，Full与Strict均可出现；查源站TLS/端口/SNI/套件 |
| 526 | 常见Strict下源站证书校验失败，核对证书和模式，不与525混为一谈 |

### 4.3 幂等建两条ordinary节点（CF04）

先查同名节点：不存在才POST；存在则核对端点、用途、受控凭据一致性，匹配复用ID，不匹配停下登记冲突。不覆盖旧id10/11或就地修改全局旧SNI。

实际源码要求凭据放**params内**，下面仅结构模板，内存替换占位符后发送，不原样调用：

```json
{
  "name": "CF2D-HK-01",
  "proto": "vless-ws",
  "purpose": "ordinary",
  "host": "104.18.32.7",
  "port": 2053,
  "priority": -100,
  "enabled": true,
  "params": {
    "uuid": "<受控源站UUID>",
    "sni": "hk.6849271.xyz",
    "ws_host": "hk.6849271.xyz",
    "ws_path": "<受控WS路径>",
    "flow": "",
    "fp": "chrome"
  }
}
```

第二条**CF2D-BWG-01**：host=162.159.135.42，SNI/Host=usbwg.6849271.xyz，其余同结构。UUID/path逐源站核对，不能仅信“共享”文字。
接口POST /api/v1/nodes。priority=-100是保持旧候选优先的建议，结合实际failover_priority排序preview；不能擅改旧主用。
is_primary属于绑定，不是建节点字段。返回DTO中ws_path可能未掩码，只导出ID/name/host/port/purpose/HTTP码，不打印完整响应。

### 4.4 只绑定R2，apply=false（CF05）

对两实际新ID分别POST /api/v1/assignments/batch，一次仅一台：

```json
{"device_ids":["rtr-a9bcba"],"node_id":12345,"is_primary":false,"apply":false}
```

12345换真实ID。逐条检查items[0].ok与device_id，HTTP200不等于成功。接口是upsert，不要先清空旧绑定。
两次绑定失败可能部分完成，必须重新读取实际集合后续跑。新绑定默认failover_priority=0，实际顺序还受node priority/id影响；preview确保旧主节点唯一、旧优先级不误变、CF非primary。

### 4.5 preview→apply（CF06）

1. POST /api/v1/devices/rtr-a9bcba/configs/preview，取revision，正文在内存分析，不记录敏感字段。
2. 断言crlf=0；新SNI/Host正确、严格TLS；旧主节点/候选顺序未意外变化；PROXY无BULK；节点IP直连保护完整；国内DIRECT/DNS/游戏保留；US-MEDIA与DOWNLOAD选择基线保持；无冷备域、无QoS/新协议夹带。
3. 真机临时隔离语法验证，不使用掩码凭据联网；当前内核实际支持才算，不以最新文档代替。
4. POST .../configs/apply，body为本次{"revision":"<返回值>"}，唯一Idempotency-Key。409重preview，不无限重试或复用旧revision。
5. 版本高于设备已接受水位及DB历史最高；核验任务ok、签名发布SHA/实际文件SHA与mtime、9090组状态、绑定集合。相同版本不单独算通过。
6. cache可能保留旧选择，检查PROXY=AUTO、US-MEDIA基线选择、DOWNLOAD=DIRECT；如需恢复只用selector PUT写穿并验cache副本，不清cache、不全删连接。

### 4.6 两条新节点分别强制路径验收（CF07—CF09）

**AUTO继续走BWG的204不能证明新CF可用。**

1. 对每新节点单独delay至境外HTTPS204目标；先读对应API语义，确保测鉴权/目标HTTP，平台tcp_connect不算。
2. 每节点专用测试通道：优先使用真机验证过的回环入口；否则临时独立mihomo实例，独立目录/端口，仅此新节点、strict TLS，不启TUN/redir/透明规则，不碰正式进程/cache。先检查内存/端口，不足则NOT_TESTED，不能用偷偷改正式PROXY代替。
3. 经专用入口验HTTPS204、可校验长度64—128KiB文件及出口。记录bytes/time/HTTP/exit。预期HK=191.223.215.12、BWG=74.82.193.113；如源站实际NAT/WARP则先核对真实出口，不把CF边缘地址当出口。
4. R2本机+至少一个真实LAN客户端来源验收；没有客户端来源，记本机已验/LAN待验，**推广暂停在R2检查点**。
5. 国内普通站、政务样本、游戏五域名真实IP、管理心跳/排障口/医院依赖按适用范围验；时延与同来源基线比较，不强求固定0.2s/0.6s。
6. cache副本检查持久化；低峰一次冷启动等效复核或等例行重启，未完成COLD_START_PENDING，先不推广。
7. 至少两次小流量验收间隔5分钟，R2观察至少15分钟。不要全局封旧主线路演练；既有连接切换可能重连，不能承诺视频不断流。

### 4.7 计量与全舰队串行推广（CF10—CF11）

1. 同CF边缘IP:2053承载多个域名时，内核五元组**不能分域名/源站**。总账按唯一IP+协议+端口去重，不逐节点重复计入；不能由此分别计HK/BWG供应商账单。
2. 新端点必须覆盖。更新前结算旧计数与连续性，不清链后装作无断点；coverage/day账不合格保持observe。
3. CF07—10全部通过，串行R1→R3→R4→R5→R6→R8→R9；每台备份→apply=false绑定→preview→apply→逐节点/国内/管理/持久化验收，失败即停。
4. R9不重放旧v5，包含当前现场修复生成新不可变高版本；管理半开设备先修管理再推进。
5. GL、R7/R10/R11离线记SKIPPED_OFFLINE，不下任务、不计成功。
6. **雅典娜独立子批**：核对core、active run.yaml、OpenClash生成源、热加载/持久化入口、组选择及nft白名单。不能向0/0设备走SN API，不仅修改会被重生覆盖的run.yaml。按已有可恢复源配置流程增量加节点，备份/语法/测试/加载/10.1.2.18与管理验收/持久化均通过才算。未知则保持正常并列专项待办，不把SN8台通过写“全舰队完成”。

### 4.8 回退与交付（CF12）

| 失败点 | 处理 |
|---|---|
| TLS/WS失败 | 不建/绑，保留现网，按错误层处理 |
| 已建未绑 | 登记ID，可停用本次新节点，不影响旧节点 |
| 已绑未发 | 读取实际集合，仅恢复本次新增项；当前未见解绑API，先补受审查接口或按已有维护授权用精确事务撤本次绑定，不伪造接口、不删全局节点 |
| 发布后异常 | 先恢复基线selector/停新CF参与，保留管理；撤新增绑定后用新高版本发已验内容，不降版本、不手改设备配置 |
| 后续单台失败 | 停该台与后续，保留已验正常设备，逐台记录 |
| 二进制/迁移失败 | 用03已测的兼容回退方案；CF失败不触发全库回滚 |

回填CF01—CF12、真实node ID/设备/version/时点/出口/回退状态。原件/敏感参数只在ignored目录，受控参数文件只补元数据，不替换UUID/path。完成三件套后释放本人锁。

## 官方依据（2026-11-18查询）

- [CF HTTPS支持2053](https://developers.cloudflare.com/fundamentals/reference/network-ports/)。
- [Universal SSL签发与覆盖](https://developers.cloudflare.com/ssl/edge-certificates/universal-ssl/enable-universal-ssl/)。
- [Full模式](https://developers.cloudflare.com/ssl/origin-configuration/ssl-modes/full/)。
- [525](https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-5xx-errors/error-525/)与[526](https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-5xx-errors/error-526/)。
- [WS支持及边缘维护可能断开连接](https://developers.cloudflare.com/network/websockets/)。
