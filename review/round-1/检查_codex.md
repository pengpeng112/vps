这份方案**覆盖了用户需求的主要方向，但还不能作为“四台设备插上即自动刷完、没有遗漏”的执行标准**。它把部分历史修复、单台 R7 的人工补齐结果，以及尚未实现的功能混在“已固化”流程中；当前脚本还有阻断批量执行的问题。

以下核查于 **2026-10-14**，全程只读，未修改文件、未连接现网、未运行刷机或部署脚本。结论以当前源码为主；历史实测单独标明。本地 Python 入口不可执行，因此未运行 Python 回归；型号解析问题用只读字符串样例复核。

**会阻断或破坏四台自动刷机的发现：**

1. **公钥分发的 `{N}` 没有全部展开，“临时文件互踩已修”不成立。**  
   主台 `scp` 目的路径是普通字符串 `/tmp/snr-ak-{N}`，后面的读取命令却是 f-string，读取 `/tmp/snr-ak-8` 等实际编号路径。TX/BWG 分发同样混用字面量 `{N}` 和展开后的编号：写入、复制与读取路径不一致，四台还会共享部分临时文件。脚本只打印分发失败，继续执行。应统一渲染路径，逐台核对远端授权结果，失败停止。  
   出处：[flash-snr.py:388](F:/vps/scripts/flash-snr.py:388)、[flash-snr.py:397](F:/vps/scripts/flash-snr.py:397)；方案 §2.1 第5项。

2. **批量探测把返回文本最后一行当型号，正常 shell 提示符会导致新机被排除。**  
   `until("#")` 返回含提示符的文本，而 `_model = ...splitlines()[-1]` 很可能得到 `root@OpenWrt:~#`。只读样例中，真实型号为 AX3000/MT7981，仍被解析成提示符并拒绝。应剥离回显和提示符，使用明确的输出标记。  
   出处：[flash-batch.py:65](F:/vps/scripts/flash-batch.py:65)、[flash-batch.py:73](F:/vps/scripts/flash-batch.py:73)。

3. **四线程并发创建设备会碰到管理后台的地址分配竞争。**  
   后台每个事务都读取现有 `wg_ip`，选择最小空闲地址，再插入设备；没有看到分配锁或唯一冲突重试。并发事务可能选中同一地址，数据库唯一约束使其中一个创建失败。这是源码可达的竞争，尚未现场复现。即使新设备不用 WG 传输，创建记录仍要分配该字段。建议录入阶段串行，文件部署并行，或修复后台分配。  
   出处：[devices.go:32](F:/vps/mgmt/backend/internal/httpserver/devices.go:32)、[devices.go:83](F:/vps/mgmt/backend/internal/httpserver/devices.go:83)、[0001_init.sql:13](F:/vps/mgmt/backend/internal/db/migrations/0001_init.sql:13)。

4. **失败设备不会被下一轮自动发现，方案的无人值守续跑承诺尚未实现。**  
   单台脚本首先改口令，随后停止 telnet；批量入口只发现 telnet 开放、出厂口令可登录的设备。因此，后续任一步失败，下一轮扫描通常直接跳过。SOP 实际要求人工 SSH、判断检查点、复刻命令块。  
   出处：[flash-snr.py:150](F:/vps/scripts/flash-snr.py:150)、[flash-snr.py:172](F:/vps/scripts/flash-snr.py:172)、[flash-batch.py:60](F:/vps/scripts/flash-batch.py:60)、[SOP:206](F:/vps/docs/operations/新SN-R1刷机录入SOP-自动化-20260928.md:206)；方案 §四第4、5项。

5. **拟议的 `/tmp/flash-N.stepK` 标志不足以解决恢复。**  
   `/tmp` 会随重启丢失；编号不是硬件身份；“曾完成”不代表文件仍完整或服务仍健康。更关键的是，批量扫描本身尚不能发现已经改密、关闭 telnet 的半成品。恢复方案需要持久化设备指纹、编号、端口、平台身份、引导包和步骤状态，并支持 SSH 恢复入口；跳过步骤前重新核验后置条件。  
   出处：方案 §3.2-R1；[flash-batch.py:153](F:/vps/scripts/flash-batch.py:153)、[flash-snr.py:248](F:/vps/scripts/flash-snr.py:248)。

6. **录入不是幂等的，中断重跑可能生成重复身份或遗失一次性凭据。**  
   脚本每次直接 POST 创建设备，没有按硬件指纹恢复既有记录；后台设备名称没有唯一约束。平台创建成功但响应或本地保存失败时，会留下孤儿记录。enroll 成功后注册码立即作废，若引导包没保存成功，不能直接再 enroll。固定文件名还可能覆写旧记录。  
   出处：[flash-snr.py:248](F:/vps/scripts/flash-snr.py:248)、[flash-snr.py:253](F:/vps/scripts/flash-snr.py:253)、[flash-snr.py:262](F:/vps/scripts/flash-snr.py:262)、[devices.go:115](F:/vps/mgmt/backend/internal/httpserver/devices.go:115)、[0001_init.sql:11](F:/vps/mgmt/backend/internal/db/migrations/0001_init.sql:11)。

7. **“单台失败不影响其他台”只适用于正常返回失败码，异常会打断总汇报。**  
   `flash_one()` 的超时、进程启动失败和日志写入失败没有捕获；`f.result()` 任一抛异常，主程序就不能保证汇总其他设备结果或清锁。日志在子进程结束后才写，超时场景不会执行该写入，无法保证保留断点。应实时写独立日志，逐台捕获异常，统一收集所有结果。  
   出处：[flash-batch.py:119](F:/vps/scripts/flash-batch.py:119)、[flash-batch.py:175](F:/vps/scripts/flash-batch.py:175)。

8. **原子锁没有完整覆盖生命周期，也没有实际预留编号和端口。**  
   MAC 查询、编号/端口分配、子进程异常等路径不在统一 `finally` 内，可能遗留锁。超过90分钟自动删锁只依据时间，不核对原进程；同时遇到过期锁还存在删除/重建竞争。此锁只约束本机 batch，单台脚本及其他主机可同时取得相同资源。“预分配”目前只是计算结果，没有服务端占位。  
   出处：[flash-batch.py:135](F:/vps/scripts/flash-batch.py:135)、[flash-batch.py:170](F:/vps/scripts/flash-batch.py:170)、[flash-snr.py:67](F:/vps/scripts/flash-snr.py:67)。

**防误刷、清理和验收方面的发现：**

9. **防误刷集合失败时仍继续，且返回不完整集合时会放弃前缀保护。**  
   `fleet_exact_macs()` 不检查 SSH 返回码；返回空集合会进入旧前缀模式，同批新设备可能被误排除。只要集合非空，前缀保护就完全不执行，即使它遗漏了某台现役设备。平台只存单个 `mac_addr`，也不能表达全部接口和历史指纹。建议使用完整、经核验的精确清单，失败时停止自动刷机。  
   出处：[flash-batch.py:22](F:/vps/scripts/flash-batch.py:22)、[flash-batch.py:79](F:/vps/scripts/flash-batch.py:79)、[flash-snr.py:567](F:/vps/scripts/flash-snr.py:567)。

10. **型号准入过宽，单台入口则完全没有型号和分区保护。**  
    batch 只要型号字符串含 `MT7981` 或 `AX3000` 就通过，不能证明是相同固件和分区布局。直接运行 `flash-snr.py --host` 会绕过这个检查，却执行固定分区写零、全局 openssl 替换和 iptables 部署。应把硬件、固件版本、分区布局和所需工具检查放到单台入口，batch 共用。  
    出处：[flash-batch.py:75](F:/vps/scripts/flash-batch.py:75)、[flash-snr.py:150](F:/vps/scripts/flash-snr.py:150)、[flash-snr.py:200](F:/vps/scripts/flash-snr.py:200)、[固件索引](F:/vps/firmware/README.md)。

11. **“PCDN 六面净”没有落实到脚本验收，R7 的人工清理也未全部合入。**  
    当前终验只统计部分进程，没有检查异常端口、S 自启、安装钩子、弹药和分区执行文件。清理命令包含更多进程，终验正则却遗漏 `net_qua_cli`、`inst_width`、`tcp_stun_ser` 等。SOP 最新记录的 rathole/zcon 二进制、厂商 CGI 云端点和 checkMem 清除，在当前通用脚本中也未见对应完整处理。R7 终态不能代表四台新机自动结果。  
    出处：[flash-snr.py:179](F:/vps/scripts/flash-snr.py:179)、[flash-snr.py:608](F:/vps/scripts/flash-snr.py:608)、[SOP:254](F:/vps/docs/operations/新SN-R1刷机录入SOP-自动化-20260928.md:254)；方案 §五。

12. **固定写零768MB缺少分区确认、备份和成功检查。**  
    脚本直接写 `/dev/mmcblk0p6`，未核对它是否为预期 userdata、容量和文件系统；stderr 被丢弃，`dd` 失败也可能被后面的 `sync` 掩盖。懒卸载和 mount 文本检查也不能证明没有后台持有者。SOP 已将该操作降为辅助，方案仍把它作为标准清理，却没有解释每台为什么必须做，以及恢复路径。  
    出处：[flash-snr.py:190](F:/vps/scripts/flash-snr.py:190)、[flash-snr.py:198](F:/vps/scripts/flash-snr.py:198)、[SOP:254](F:/vps/docs/operations/新SN-R1刷机录入SOP-自动化-20260928.md:254)。

13. **QUIC“重载后复验”实际是无条件修正，不能证明自愈生效。**  
    Q 关在切节点之前采样。切 LAX、回东京后，脚本直接执行 REJECT 重插，没有读取最终顺序、更新验收结果或检查命令返回码。这样会掩盖自愈缺陷。平台 handler 首选 API 热加载，也不必经过 init.d 的 SELFHEAL 路径。应分别验证完整启动、平台热加载、完整重启后的规则，再决定是否修复。  
    出处：[flash-snr.py:609](F:/vps/scripts/flash-snr.py:609)、[flash-snr.py:640](F:/vps/scripts/flash-snr.py:640)、[switch_node.sh:72](F:/vps/mgmt/agent/handlers/switch_node.sh:72)。

14. **隧道监听数量不能证明备用路径或远程排障可用。**  
    `L>=3` 只统计端口，健康检查只访问18443。没有核验18444/18445连接的是 DMIT 当前管理面，也没有从 DMIT 经分配的 `-R` 端口实际登录目标设备。下发任务成功证明 agent 通道，不能代替排障 SSH 验收。  
    出处：[flash-snr.py:434](F:/vps/scripts/flash-snr.py:434)、[flash-snr.py:596](F:/vps/scripts/flash-snr.py:596)、[SOP:200](F:/vps/docs/operations/新SN-R1刷机录入SOP-自动化-20260928.md:200)。

15. **“秒级心跳、MAC固定、WiFi默认关”等承诺没有对应完整检查。**  
    心跳关只要求 logread 曾出现一次成功；MAC 只是写 UCI，并注明重启才生效，未检查运行值和重启后的稳定性；WiFi 执行 down 后没有读回接口、射频和启动策略。cron 按含 `routermgmt` 的行数判断、rc.local 按文本出现次数判断，也不能证明每条任务准确、可执行。应逐项核对真实状态及平台新鲜度。  
    出处：[flash-snr.py:346](F:/vps/scripts/flash-snr.py:346)、[flash-snr.py:561](F:/vps/scripts/flash-snr.py:561)、[flash-snr.py:602](F:/vps/scripts/flash-snr.py:602)、[flash-snr.py:610](F:/vps/scripts/flash-snr.py:610)；方案 §五。

16. **没有逐台首次重启后的验收闭环。**  
    历史问题多次只在重启后暴露：MAC变化、cron被覆盖、WiFi策略死代码、PCDN复活、QUIC竞态。当前脚本没有重启后复验，也没有等待夜间重启、自动收集结果再标记交付完成的机制。必须区分“安装完成”和“可交付完成”，不能用R7历史重启结果替四台验收。  
    出处：[SOP:230](F:/vps/docs/operations/新SN-R1刷机录入SOP-自动化-20260928.md:230)、[flash-snr.py:648](F:/vps/scripts/flash-snr.py:648)；方案 §五。

17. **代理验证主要在路由器自身进行，缺少用户终端链路验收。**  
    curl 走本地7890证明核心能代理，不证明新机 LAN 客户端的 DHCP、DNS、透明转发、访问策略和视频分流正确。当前状态已记录：R2曾因只测路由器外网、漏测WiFi空口而误判。新机至少应有一个有线终端穿过它进行访问测试；WiFi交付启用时再验证无线。  
    出处：[flash-snr.py:599](F:/vps/scripts/flash-snr.py:599)、[flash-snr.py:636](F:/vps/scripts/flash-snr.py:636)、[当前状态](F:/vps/docs/current/当前状态.md)“2026-10-04 傍晚”记录。

**标准化实现和执行条件方面的发现：**

18. **“远端脚本一律整文件base64+md5”与当前实现不一致。**  
    当前仍有 SSH 链上的 sed/awk 修改 agent、rc.local、登录页和WiFi文件；引导包、初始mihomo配置、tunnel.conf及生成的init脚本也有直接cat/heredoc写入，没有统一校验。`push()`的md5改进是存在的，但不能概括成全部文件均受保护。应限定规则适用范围，并落实对应写入和检查方式。  
    出处：[flash-snr.py:294](F:/vps/scripts/flash-snr.py:294)、[flash-snr.py:315](F:/vps/scripts/flash-snr.py:315)、[flash-snr.py:410](F:/vps/scripts/flash-snr.py:410)、[flash-snr.py:459](F:/vps/scripts/flash-snr.py:459)；方案 §2.1 第10项。

19. **远端命令和API错误处理仍不统一，“已完成”可能只是打印结果。**  
    `run()`不读取远端退出码；清理输出被忽略；安装输出经管道截取；隧道启动结果不判定。绑定循环、告警PATCH、指纹PATCH、SQL更新也没有完整检查。尤其绑定API即使HTTP200，逐设备结果仍可能 `ok=false`，脚本没有处理。最末双验不能发现遗漏的备用绑定、告警或台账失败。  
    出处：[flash-snr.py:127](F:/vps/scripts/flash-snr.py:127)、[flash-snr.py:296](F:/vps/scripts/flash-snr.py:296)、[flash-snr.py:506](F:/vps/scripts/flash-snr.py:506)、[nodes.go:409](F:/vps/mgmt/backend/internal/httpserver/nodes.go:409)。

20. **“错后端零写入”表述过宽，身份检查也发生得太晚。**  
    身份GET之前已经改设备口令、清理预装、写分区和替换openssl；`M.token()`在缓存失效时还会向当前后端POST登录和TOTP。因此只能说“创建设备前做了后端检查”，不能说没有任何写入。管理面、资产、凭证和节点等可预检条件，应放在设备变更之前。  
    出处：[flash-snr.py:170](F:/vps/scripts/flash-snr.py:170)、[flash-snr.py:239](F:/vps/scripts/flash-snr.py:239)、[mgmt_api_test.py:78](F:/vps/scripts/mgmt_api_test.py:78)；方案 §2.1 第1项。

21. **四进程共享管理隧道及token缓存，缺少并发保障。**  
    四台会同时调用 `ensure_tunnel()`，可能同时尝试绑定18444；健康检查只要HTTP请求不抛异常就返回，并不判定状态码和平台身份。token缓存也未按后端隔离、未原子写入、无跨进程锁。建议batch先完成管理面检查和认证，再启动子任务，避免每台重复建立共享前置条件。  
    出处：[mgmt_api_test.py:50](F:/vps/scripts/mgmt_api_test.py:50)、[mgmt_api_test.py:78](F:/vps/scripts/mgmt_api_test.py:78)、[flash-snr.py:239](F:/vps/scripts/flash-snr.py:239)。

22. **固定编号、端口和接线方式只是条件性预期。**  
    编号和端口由当前最大值加一，未固定为R8–R11、13230–13233；失败录入或其他接入就会改变结果。设备按IP字符串排序，也没有“物理标签→硬件指纹→编号”映射。扫描排除`.1`，所以batch不会发现PC直连默认地址的新机；该方式须走单台入口。级联还需确认PC路由、WAN管理访问、端口数量及新机WAN/LAN子网重叠情况。  
    出处：[flash-batch.py:41](F:/vps/scripts/flash-batch.py:41)、[flash-batch.py:99](F:/vps/scripts/flash-batch.py:99)、[flash-batch.py:116](F:/vps/scripts/flash-batch.py:116)、[SOP:180](F:/vps/docs/operations/新SN-R1刷机录入SOP-自动化-20260928.md:180)；方案 §四。

23. **8–10分钟“含巡检间隔”没有成立的时间预算或四台实测证据。**  
    即便并行执行确实需要6–8分钟，10分钟巡检的最坏等待还应另加，约为16–18分钟，尚未计算串行指纹探测、启动等待和重试。四台还共享PC上行、网络及管理服务，并各自写零768MB。改轮询10秒为6秒，只减少完成状态的发现延迟，不缩短任务本身；调换大小文件推送顺序也不能替代断点传输。  
    出处：方案 §2.2、§3.2-R2、§四第3项；[flash-batch.py:155](F:/vps/scripts/flash-batch.py:155)、[flash-snr.py:200](F:/vps/scripts/flash-snr.py:200)、[flash-snr.py:497](F:/vps/scripts/flash-snr.py:497)。

24. **自动定时器仅有历史记载，方案缺少可重复部署和当前健康证明。**  
    历史变更说明记载安装过automation任务，但两个脚本本身没有定时器配置。方案未明确执行主机、Python实际路径、工作目录、环境、电脑休眠后的行为和失败通知。本次没有查询任务运行态，不能确认“cron已武装”现在可用。  
    出处：[变更说明:32](F:/vps/verify/round-2/变更说明.md:32)、[flash-batch.py:14](F:/vps/scripts/flash-batch.py:14)；方案 §2.2、§四第2项。

25. **台账在终验前登记，“完成记录”与“失败记录”没有区分。**  
    当前指纹总览在第9步追加，第10/11步仍可能失败。批量汇总只打印，缺少带时间、设备身份、步骤状态、验收证据和恢复动作的结构化结果。四台还共同追加同一总览，未见统一写入和去重机制。方案R3/R4应先定义记录状态，再实现持久化。  
    出处：[flash-snr.py:590](F:/vps/scripts/flash-snr.py:590)、[flash-snr.py:645](F:/vps/scripts/flash-snr.py:645)、[flash-batch.py:179](F:/vps/scripts/flash-batch.py:179)；方案 §3.2-R3/R4。

26. **资产及配置缺少统一前置验证，有些错误会在设备已被清理后才暴露。**  
    脚本依赖本机固件组件、历史clash配置、模板、SSH工具、密钥和Python包，没有一次性检查存在性、版本、架构及可信哈希。历史配置解析失败还可能生成空成员或空直连规则，初始配置在启动前未显式执行`mihomo -t`。节点ID和出口IP也硬编码，未先核对平台节点身份。  
    出处：[flash-snr.py:231](F:/vps/scripts/flash-snr.py:231)、[flash-snr.py:443](F:/vps/scripts/flash-snr.py:443)、[flash-snr.py:506](F:/vps/scripts/flash-snr.py:506)、[flash-snr.py:640](F:/vps/scripts/flash-snr.py:640)。

27. **参数和固化策略还有具体遗漏。**  
    `--random-pw`调用的函数实际仍返回统一口令；`--num`没有范围限制；显式`--rssh`绕过自动分配的冲突检查；`--max<=0`可能造成空任务错误或异常切片。MAC公式在规模扩大后也会出现跨接口重号，例如R8 LAN末字节与R24 WAN相同。另一个遗漏是创建请求里的`hw_model`不被创建接口接收，enroll时也未补传。  
    出处：[flash-snr.py:57](F:/vps/scripts/flash-snr.py:57)、[flash-snr.py:140](F:/vps/scripts/flash-snr.py:140)、[flash-snr.py:297](F:/vps/scripts/flash-snr.py:297)、[flash-batch.py:132](F:/vps/scripts/flash-batch.py:132)、[devices.go:52](F:/vps/mgmt/backend/internal/httpserver/devices.go:52)、[devices.go:120](F:/vps/mgmt/backend/internal/httpserver/devices.go:120)。

28. **方案未定义真正能证明本轮修订完成的回归标准。**  
    历史回归主要有AST、局部复现、heredoc语法、无设备扫描和R7实机检查，不能覆盖四台并发。至少应验证：带提示符的真实telnet输出、公钥路径渲染、并发录入、逐步骤中断恢复、子任务超时、错误后端、台账写入失败，以及四台完整结果汇总。意见处理也应记录每项采纳/不采纳、理由、代码变更和验证证据，而非仅凭“共识”收口。  
    出处：[核查报告:53](F:/vps/verify/round-2/核查报告.md:53)、[SOP:252](F:/vps/docs/operations/新SN-R1刷机录入SOP-自动化-20260928.md:252)；方案 §六。

建议将修订顺序调整为：**先修探测、公钥分发和并发录入；再实现持久化恢复与逐台失败隔离；随后让验收逐项覆盖交付承诺；最后用四台真实批次测量时间并压缩等待。** 目前不宜把“零其他操作”“人工仅在续跑仍失败时”“8–10分钟含巡检”写成已具备的能力。

本轮仅提交审查意见，未按原始需求中的最后一步修改脚本，遵守了你本轮明确规定的只读约束。
