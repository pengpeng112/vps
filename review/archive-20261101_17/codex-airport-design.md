推荐结构是：

```text
PROXY → AUTO(fallback)
          ├─ NODE-PICK：原有自建节点及 CF，保持优先级
          ├─ AIRPORT(url-test)：机场节点自动择优
          └─ DIRECT

US-MEDIA(fallback) → [原有美西专用节点（如有）, NODE-PICK, AIRPORT]
```

以下于 **2026-10-31** 核对官方文档及 **v1.19.31 标签源码**；未做舰队实机测试。标记含义：**【文档】**官方明确说明；**【源码】**该版本实现确认；**【建议/推断】**工程判断。

**1. 自动择优：机场池用 url-test，外层继续用 fallback**

**【建议】**参数起点：

| 项目 | 推荐值 | 理由 |
|---|---|---|
| provider 更新间隔 | 21600 秒 | 每 6 小时刷新节点清单 |
| provider 健康检查 | 300 秒，`lazy: false` | 机场作为常备兜底，需要持续掌握可用性 |
| 检查目标 | HTTPS gstatic，要求 204 | 与现有自建链保持一致，减少门户页面等误判 |
| 检查超时 | 5000 毫秒 | 给跨境连接适当余量 |
| AIRPORT 类型 | `url-test` | 机场节点没有可信的固定优先级 |
| tolerance | 100 毫秒起步 | 减少小幅延迟变化引起的切换 |

**【文档＋源码】**`fallback` 按顺序选健康节点，适合已有明确主备关系的自建节点；`url-test` 比较探测延迟。`tolerance` 属于 `url-test`，不会给 `fallback` 增加延迟择优能力。[fallback 文档](https://wiki.metacubex.one/config/proxy-groups/fallback/)、[url-test 实现](https://github.com/MetaCubeX/mihomo/blob/v1.19.31/adapter/outboundgroup/urltest.go)

将下面片段合并到模板；保留其他组、规则以及 NODE-PICK 的原有成员：

```yaml
proxy-providers:
  airport-sub:
    type: http
    url: "__AIRPORT_SUB_URL__"  # 服务端渲染成实际 URL
    path: ./proxy_providers/airport-g01.yaml
    interval: 21600

    # 仅控制订阅下载；使用不依赖机场的自建组
    proxy: NODE-PICK

    health-check:
      enable: true
      url: https://www.gstatic.com/generate_204
      interval: 300
      timeout: 5000
      lazy: false
      expected-status: 204

    exclude-filter: '(剩余流量|套餐到期|官网|公告|重置时间)'
    override:
      additional-prefix: "AP | "

proxy-groups:
  - name: AIRPORT
    type: url-test
    use:
      - airport-sub
    url: https://www.gstatic.com/generate_204
    interval: 300
    timeout: 5000
    expected-status: 204
    tolerance: 100
    lazy: false
    empty-fallback: REJECT

  - name: AUTO
    type: fallback
    proxies:
      - NODE-PICK
      - AIRPORT
      - DIRECT
    url: https://www.gstatic.com/generate_204
    interval: 120
    timeout: 5000
    expected-status: 204
    lazy: false

  - name: US-MEDIA
    type: fallback
    proxies:
      # 如已有专用美西节点，继续放在 NODE-PICK 前
      - NODE-PICK
      - AIRPORT
    url: https://www.gstatic.com/generate_204
    interval: 120
    timeout: 5000
    expected-status: 204
    lazy: false
```

这里有五个关键点：

- **AIRPORT 必须在 NODE-PICK 与 DIRECT 之间。**这样自建恢复后优先回自建，机场只承担后备。NODE-PICK 内也应继续保持没有 DIRECT。
- **US-MEDIA 必须单独加 AIRPORT。**它原来直接引用 NODE-PICK，单改 AUTO 不会让视频规则获得机场后备。
- **`PROXY` 实际选择必须是 AUTO。**手选具体节点会绕过自动链。
- **`empty-fallback: REJECT` 建议保留。**订阅初始化失败、组为空时，避免默认 `COMPATIBLE` 把空机场组变成直连出口；该字段在 v1.19.31 已存在。[组解析实现](https://github.com/MetaCubeX/mihomo/blob/v1.19.31/adapter/outboundgroup/parser.go)、[空组处理](https://github.com/MetaCubeX/mihomo/blob/v1.19.31/adapter/outboundgroup/groupbase.go)
- **真正管理机场节点定时探测的是 provider 的 `health-check`。**不要只配置 AIRPORT 的 `interval/lazy`。本方案所有检查 URL 完全相同，源码会避免为 provider 再注册这个相同 URL 的额外检查任务。[健康检查实现](https://github.com/MetaCubeX/mihomo/blob/v1.19.31/adapter/provider/healthcheck.go)

**【建议】地区先别大范围裁剪。**地区远不等于不稳定，机场线路的入口、转接和拥塞往往更关键。先保留 40 个，排除公告节点；有实际结果后，再在 **provider 层**加白名单，例如：

```yaml
# 加在 airport-sub 下；此例只适用于包含这些中文地区名的订阅
filter: '(香港|日本|新加坡|台湾|美国)'
```

provider 层过滤会减少实际加载、探测的节点；仅在 AIRPORT 组过滤，不能减少 provider 默认检查的节点数。[provider 解析与过滤实现](https://github.com/MetaCubeX/mihomo/blob/v1.19.31/adapter/provider/provider.go)

**【限制】**url-test 测的是探测请求延迟，不是吞吐、长期丢包率或 Netflix 解锁能力。需要严格美区时，应另建筛选美国节点的 `AIRPORT-US` 给 US-MEDIA 使用。

另外，**【源码推断，未实机复现】**v1.19.31 的 `fastNotExist` 检查从第二个成员开始；当前选择恰为首成员时，可能绕过 tolerance。因此 `100` 是减少切换的参数，不能当成严格防抖保证。[对应选择逻辑](https://github.com/MetaCubeX/mihomo/blob/v1.19.31/adapter/outboundgroup/urltest.go#L110-L133)

**2. 订阅不可达：有效缓存会继续使用，但首次启动需要独立引导**

**【源码】**v1.19.31 的行为如下：

| 情况 | 实际行为 |
|---|---|
| 启动或重载，缓存存在且能解析 | 优先加载缓存 |
| 缓存已超过更新间隔 | 先使用缓存，同时安排刷新 |
| 后续下载失败、HTTP 错误或内容解析失败 | 保留原有节点，不用错误内容替换 |
| 没有有效缓存，首次下载也失败 | provider 初始化报错；没有机场节点可用 |
| 超过 6 小时仍更新失败 | 不会因为“满 6 小时”就删除有效缓存 |

这意味着：**订阅域名被墙，不等于缓存中的机场节点立即失效**；但节点自身到期、改密或被封仍会使它不可用。[缓存与刷新实现](https://github.com/MetaCubeX/mihomo/blob/v1.19.31/component/resource/fetcher.go)、[HTTP 下载实现](https://github.com/MetaCubeX/mihomo/blob/v1.19.31/component/resource/vehicle.go)

**【建议】**采用上面已有的这两个字段：

```yaml
# 合并到 airport-sub
proxy: NODE-PICK
path: ./proxy_providers/airport-g01.yaml
```

`proxy` 的文档语义就是指定下载/更新使用的代理，**不会把机场业务流量也串进 NODE-PICK**。[provider 文档](https://wiki.metacubex.one/config/proxy-providers/#proxy)

鸡生蛋问题这样处理：

- 下载通道选不包含机场的 `NODE-PICK`。
- 避免下载依赖 `AIRPORT`，也避免依赖包含 AIRPORT 的 AUTO/PROXY。
- **自建全断＋没有机场缓存＋订阅直连不通时，单靠这份 YAML 无法获得机场节点。**首次接入应提前完成下载；更稳的是服务端同时注入少量真实机场节点作为 provider 的 `payload` 备用内容，或采用第 4 点的平台代拉方案。v1.19.31 支持 HTTP provider 使用 `payload` 作为备用节点。[备用 payload 实现](https://github.com/MetaCubeX/mihomo/blob/v1.19.31/adapter/provider/provider.go#L172-L195)

还要明确缓存目录：

**【文档】**相对 `path` 按 mihomo 的 **`-d` HomeDir** 解析，不应按 `-f config.yaml` 所在目录猜测。不同设备的 `-d` 需要分别核对。[path 文档](https://wiki.metacubex.one/config/proxy-providers/#path)

**【建议】**缓存放可写、持久目录，避免 `/tmp` 重启后丢失。“设备端只读使用配置”可以保持，但 mihomo 仍需要写 provider 缓存；若文件系统真的只读，HTTP provider 更新会受到影响。

**3. 热替换：可以零手工登录设备，但不能只改环境变量文件**

**结论：完成首次渲染器改造后，可以通过平台换订阅；还要处理进程环境生效和缓存刷新。**

**【源码】**固定使用：

```yaml
path: ./proxy_providers/airport-g01.yaml
```

即使 `url` 已换成新机场，重载时仍会优先读这个路径里的旧内容。缓存尚未满 6 小时时，新订阅可能不会立即下载；这是保留旧内容的机制，也会造成换机场延迟。[Fetcher 初始化逻辑](https://github.com/MetaCubeX/mihomo/blob/v1.19.31/component/resource/fetcher.go#L52-L106)

**【建议】给“机场切换代次”编号，换机场时递增，普通发布保持不变：**

```yaml
proxy-providers:
  airport-sub:
    type: http
    url: "__NEW_AIRPORT_SUB_URL__"
    path: ./proxy_providers/airport-g02.yaml
    interval: 21600
    proxy: NODE-PICK
    # health-check、override 等沿用第 1 段
```

这样新配置不会读取 g01 文件。**仅改 provider 名称、保留同一个 path，不能隔离旧内容。**

取舍是：g02 没有缓存，需要首次下载；因此应先在服务端验证新订阅，最好一并提供新机场备用 `payload`。旧缓存可暂留作回退，**不要每次渲染都生成新路径**。

如果希望“新订阅失败时继续用旧机场”，可以保持固定 path，并由平台自动调用：

```text
PUT /providers/proxies/airport-sub
```

这会立即触发更新；失败则保留旧节点。但它需要平台已有或新增相应任务能力，不能假设改环境变量就会自动调用。[更新 API 实现](https://github.com/MetaCubeX/mihomo/blob/v1.19.31/hub/route/provider.go)

结合当前仓库，首次接入还需要这些具体改动：

- [template.go](/F:/vps/mgmt/backend/internal/clash/template.go:60) 目前没有机场渲染输入，需增加 URL、缓存代次及对应模板输出；使用 YAML 序列化生成 URL 标量。
- [golden.yaml](/F:/vps/mgmt/backend/internal/clash/golden.yaml:88) 当前 AUTO 已是 **120 秒**，并非题述 300 秒；US-MEDIA 成员还由渲染器动态生成，需同步追加机场。
- [template_test.go](/F:/vps/mgmt/backend/internal/clash/template_test.go:151) 目前断言 AUTO 必须恰为 `[NODE-PICK, DIRECT]`，需更新。
- 模板通过 `go:embed` 编入后端，**首次模板改造需要重新构建部署后端**。后续换订阅不需要重新编译，但运行进程必须取得新环境变量，再渲染、签名、发布配置。

**【建议】**可以增加非敏感环境变量 `AIRPORT_GENERATION=g02`。修改 `.env` 文件不会自动改变现有进程环境，需按生产部署方式使其生效。

凭证边界也要说清：**环境变量能避免 token 进入源码；直接注入 URL 后，token 仍会出现在设备运行配置中。**渲染结果、配置差异和下载错误日志都应受控并脱敏。

**4. 风控与资源：40 节点通常可接受，11 台共用账号的限制需要机场明确允许**

**【未知】**机场没有给出具体规则，无法判断它按订阅下载 IP、节点连接 IP、同时在线设备还是连接数量计数；mihomo 配置不能保证规避这些限制。

**【计算＋推断】**11 台每 6 小时刷新，正常约 **44 次订阅请求/天**，单看频率不高，但同步启动会形成突发；失败自动重试还会增加请求。该版本刷新退避实现没有随机抖动。[刷新与退避实现](https://github.com/MetaCubeX/mihomo/blob/v1.19.31/component/resource/fetcher.go)

更容易忽略的是：**健康检查本身会连接机场节点，即使用户业务尚未切过去，也可能被计入在线 IP 或连接数。**

**【建议】长期更适合平台统一代拉：**

```text
平台每 6 小时拉机场一次
  → 校验、筛选并保留最近成功结果
  → 国内可达的鉴权端点
  → 11 台设备读取
```

设备 provider 可改为以下字段，其余健康检查保持不变：

```yaml
# 合并到 airport-sub；地址须换成自有国内可达端点
url: "https://config.example.com/providers/airport.yaml"
proxy: DIRECT
interval: 21900  # 示例：由平台按设备生成略有差异的间隔
header:
  Authorization:
    - "Bearer __DEVICE_PROVIDER_TOKEN__"
```

这样上游机场 URL/token 只留在服务端，也集中解决订阅被墙、格式变化和坏订阅回退。**它只减少订阅拉取端的压力，不会消除 11 台设备连接机场节点时的设备/IP 限制。**仅修改 interval 也不能错开第一次拉取，首次发布仍应分批。

资源方面：

| 项目 | 判断 |
|---|---|
| 40 节点、300 秒一次 | 每台约 11,520 次节点检查/天 |
| 定时检查并发 | v1.19.31 每个健康检查任务上限 10，并非全进程全局上限 |
| 当前模板 `unified-delay: true` | 成功检查还可能发第二次 HEAD，请求数约翻倍 |
| CPU/内存 | 主要看 TLS、协议实现和实际并发业务；节点数量不能直接换算固定 MB |

并发及第二次 HEAD 均可由源码确认。[健康检查并发](https://github.com/MetaCubeX/mihomo/blob/v1.19.31/adapter/provider/healthcheck.go#L115-L175)、[URLTest 请求实现](https://github.com/MetaCubeX/mihomo/blob/v1.19.31/adapter/adapter.go#L191-L258)

**【建议】**先用 40 节点、300 秒观察低配设备。若出现明显 CPU 峰值或内存压力，再在 provider/服务端保留 **12～20 条经过验证、覆盖不同地区和入口的线路**。没有可直接配置的通用“只健康检查前 N 条”字段。

SS/VLESS 混合本身没有必须拆 provider 的要求，实际要防的是：

- **解析失败与连通失败不同。**一个保留节点字段非法，可能使整份 provider 更新失败；密码错误、线路不通则通常体现为该节点检查失败。[provider 节点解析](https://github.com/MetaCubeX/mihomo/blob/v1.19.31/adapter/provider/provider.go#L421-L435)
- **完整 Clash YAML 可以接入，前提是顶层有实际 `proxies:` 列表。**provider 不会把机场的规则、DNS、策略组并入主配置；只有其他 provider 引用而没有节点列表的订阅不满足这个条件。[订阅解析结构](https://github.com/MetaCubeX/mihomo/blob/v1.19.31/adapter/provider/provider.go#L28-L30)
- **保留节点原始协议字段。**不要统一覆盖 VLESS 的 TLS、Reality、WS、flow，也不要统一强开 `skip-cert-verify`、UDP 或复用功能。
- **检查重名。**该版本 provider 对同名节点会去重；统一加前缀不能修复订阅内部重名，应在服务端规范化。
- **发布前验证实际节点内容及连通性。**仅检查主配置通过，不能证明远端 provider 的全部节点兼容，也不能证明 YouTube/Netflix 可用。
