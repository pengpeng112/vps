# 分册03：保留现场修复的模板、迁移与安全发布（Codex核查修订v2）

> **任何新节点configs/apply之前必须先过本册最小门禁。** 只先做最小门禁即可接06，不强制捆绑02管理改造、全舰队换库或04限速。
> 本轮GZ schema9，R2 39/39/39、R9 4/5/4。运行binary mtime08:04不证明模板正确；本地0010存在但生产未登记。
> 先读AGENTS/现势/00/07；当前两个门禁工具不存在，01先实现。本册未部署。

## 0. 核对现网和源码差异，先吸收成果

1. 读取最新当前状态与受控原件，不运行fleet-matchdirect等已有脚本“看看”，先读入口/调用链，其可能修改生产。
2. 记录GZ实际运行进程/二进制SHA与源码构建清单、schema全记录、活跃任务/锁。模板文件、编译入二进制的模板、设备当前文件三者分开。
3. 已有成果：R2现场MATCH,DIRECT、geolocation-!cn、国内fallback；全舰队上午修复为前会话报告，本轮只重登R2，逐台仍需对拍。
4. 本地golden仍保留国外fallback与8.8.8.8 proxy-server-nameserver，不能只凭MATCH,DIRECT就下发。保留bboluo/antdu/istarshine/dlbrowser/qhupdate/hlquant/xxcg/sdent等当前已验单域策略与游戏dnsmasq五域名，不恢复旧整份模板。
5. 原版所称11-19自动化已停须查实际任务配置；本轮未访问调度器，不重复创建/删除自动化。冲突则只暂停维护动作，先合并现势。

## 1. 最小模板修复

1. **增量修改DNS段**，不要用旧计划整块示例覆盖所有已有fake-ip-filter/nameserver-policy/监听/嗅探。先将已验国内fallback/default/proxy-server DNS回填源码，保留全部游戏过滤和真实DNS分流。
2. 再单独验证更深DNS设计：国内默认解析、不在cn库的国内站不能被强拉裸连海外DNS；海外解析需经已知可用代理，但代理节点自身解析不能依赖未建立代理形成环路。
3. 若使用#PROXY标记DoH，先在目标mihomo固定版本验证语法、首次启动/代理全挂/Geo标签缺失的依赖，不把当前网页示例当真机事实。新DNS设计不是新增CF域的必要捆绑项。
4. rules保留国内白名单/CN-MEDIA/DOWNLOAD DIRECT、受保护管理/节点IP、GEOSITE,geolocation-!cn,PROXY与末尾MATCH,DIRECT；.cn顶级规则如加，须说明它也会把海外托管的.cn强制直连，不能宣称所有国内永远100%正确。
5. 不新增未经Geo库验证存在的gfw标签；保留现场正在使用的geolocation-!cn。规则顺序与现有海外下载/视频分类逐条对拍，不为限速擅改DOWNLOAD默认。
6. 合入现有用途修复：普通PROXY/NODE-PICK排BULK、primary ordinary、所有节点IP直连保护在continue前生成。v39普通PROXY仍含BULK，需验证源码修复实际进入产物。
7. golden与SQL LF；新模板单测包括保留近期国内规则/两新CF/旧主用顺序/BULK隔离/机场拉取与管理依赖。只跑相关检查，DB集成仅隔离库。

## 2. 真机语法与库能力

1. 原始候选配置含真实凭据，放受控文件（umask077）/ignored目录，不把掩码preview拿去测试连接。使用真实已支持Geo库或隔离库，避免mihomo -t隐式下载库/修改生产目录。
2. 先读该版本mihomo -t行为，临时独立工作目录/配置、端口不启动监听；验证确实不启动透明链。不覆盖/etc/mihomo/config.yaml。
3. 不为本次新CF一律换全舰队Geo库。只有新增标签/旧库缺失才换对应实际使用文件；Country.mmdb/geoip.metadb不是必须同时装，按geodata-mode/geodata-loader与当前路径选择。
4. 需要换库时：官方来源/版本/下载SHA+格式/标签验证，先估新旧库+临时文件+恢复余量（不是固定25MB）；.new写同分区并验hash、保留旧原件后原子替换。
5. 失败恢复本人库文件，确保旧配置仍可解析；库与配置组合都要验。雅典娜路径/生成源独立；R2同hash已验则不重复推11MB。

## 3. GZ二进制与0010迁移（最小发布前置）

### 3.1 备份与隔离演练

1. 备份/核验现运行/opt/mgmt/server、必要unit/配置元数据、数据库custom格式dump，原件放权限受控位置。不输出unit环境/完整DSN/MASTER_KEY/JWT。
2. 旧命令sudo -u postgres pg_dump > root目录可能被shell重定向权限卡住；使用**postgres拥有的0700备份目录，pg_dump -Fc -f绝对目标**或等价受控方案，命令成功后验非空、pg_restore --list，再在隔离库恢复验证。不把仅文件存在当完整备份。
3. PostgreSQL dump不代替角色/权限/扩展/外部签名文件恢复，按实际依赖补恢复清单。生产DSN不得给测试使用。
4. 只读查schema_migrations全记录及nodes.purpose非法值、node_assignments主绑定非ordinary；表名不是device_nodes。查真实0010约束SQL和当前数据，冲突先产精准修复清单，不静默全局改用途。
5. 注意0010当前枚举ordinary/video，不能擅写download并指望自动接受；如新设计需要增加值，另迁移与完整API测试。
6. 在隔离恢复库用同一新二进制**-migrate**迁移，验约束/跟踪表与服务启动；演练旧binary在迁移后是否兼容，以及失败回退。仅回拷旧binary不一定足以恢复，必须实测兼容。

### 3.2 正规迁移与上线

1. 01构建工具验收后产物与manifest关联源码/模板/迁移hash，核对广州架构，运行测试与隔离恢复/迁移结果。
2. 受控获取有效运行环境，MASTER_KEY/JWT只认运行进程environ；迁移专用角色的MIGRATE_DATABASE_URL从受控入口按需取得，不回显、不使用过时.env，不在shell命令行拼明文DSN。
3. 真实入口：cmd/server/main.go中**serve只EnsureCurrent，不自动迁移**；新二进制在维护窗口执行**-migrate**，它从内嵌SQL按序迁移并登记。禁止手跑SQL后仅INSERT版本号。
4. 为约束DDL设合理锁等待/超时和失败停点，确认无并行GZ变更。迁移按单文件事务并非所有文件全局事务，失败先查实际已提交水位，不盲目反复运行。
5. 成功后验生产水位/约束/跟踪记录，再原子安装已验新binary，保留原件；restart gz-api（实际为原生unit，不套Docker重建命令）。
6. 验systemctl active、health200、运行进程实际binary SHA、API鉴权/DB权限和各设备心跳；再只preview R2/R5/R9确认新渲染保留现场规则，不能health通过就全推。
7. 10秒不是保证健康门，可按本机基线设置有上限等待；启动失败按已演练兼容回退恢复原binary/服务，不因失败直接restore全库覆盖其间其它业务。
8. 升级后原件key/签名/设备身份/数据目录保持一致；不顺手初始化/种子管理员或迁移管理主台。

## 4. R2试点与逐台推广

最小门禁先给R2发布**只含已审核模板修复**的新高版本，或与06两新CF作为同一已review候选发布，避免无意义双发；每项断言相同，不混QoS/新协议。

- current/desired/highest、DB最高版本对拍后取新不可变版本，不硬编码v40；R9旧desired5不是已验版本，不能重放。
- preview revision→apply携revision/Idempotency-Key；409重preview，不降版本/不手改设备YAML。
- 任务ok、实际文件hash/mtime、9090组、cache副本、两次心跳/管理口与DNS样本共同验收。
- 国外用显式代理/节点专用路径，设备直连curl可被污染不能判全断；国内同源验证DIRECT命中与真实响应，不强求所有百度<1s/政务302。
- 保护游戏五域名真实IP/既有UCI dnsmasq、不把路由器查询等同客户端游戏体验。
- cache选择保持基线或经审核恢复PROXY=AUTO写穿，US-MEDIA保持正常、DOWNLOAD DIRECT；不清cache/全删连接。
- R2真实客户端与低峰冷启动等效验收后才推广。走06时统一CF01—12，不重复发布；单做DNS库则R2→R5→R9→其余在线SN。
- 离线不排任务，雅典娜按生成源/nft/10.1.2.18专项维护，不由SN模板覆盖run.yaml。

## 5. 交付与回退

产出真实源码/模板/迁移/产物hash、dump恢复证据、每台版本/任务/文件/组/cache/业务/时间、失败补偿和未验项。三件套使用尚在生效的完整总览路径，不提前造第二份现势。
单台异常保留管理并用新高版本恢复已验语义，不全舰队回退；数据库约束与binary按兼容演练处理，不“直接旧binary肯定秒恢复”。本人解锁后交接06或下个批次。
