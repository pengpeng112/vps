只读核查完成，未修改文件、未运行刷机脚本、未连接现网。**当前补丁存在刷机中途必然崩溃、防误刷失效及批量公钥串写等问题，不宜据此认定流程已经闭环。**以下行号均为补丁文件本身的行号。

1. **[P1] `--num` 为字符串，步骤 5 必然抛出 `TypeError`。**  
   argparse 未指定 `type=int`，`N=args.num` 后直接执行 `0x20 + N`。此时已经完成改密、预装清理及平台录入，设备会留下半成品，且 telnet 已关闭，无法直接全量重跑。本地复现 `--num 8` 得到 `unsupported operand type(s) for +: 'int' and 'str'`。  
   证据：[diff_1_flash-snr.patch:139](/F:/vps/verify/round-2/diff_1_flash-snr.patch:139)、[302](/F:/vps/verify/round-2/diff_1_flash-snr.patch:302)。

2. **[P1] MAC 防误刷实际失效，并且设备型号没有准入校验。**  
   指纹命令输出 `wan <MAC>`，正则却要求 `wan: <MAC>`，因此正常输出被解析为 `{}`，所有前缀排除判断均落空，最终仍返回出厂设备。`model` 只用于展示，缺失 MAC、未知型号都不阻止刷机；任一可用出厂口令登录的设备可能进入硬编码的清理及块设备擦写流程。  
   证据：[diff_2_flash-batch.patch:62](/F:/vps/verify/round-2/diff_2_flash-batch.patch:62)–70；本地用两行合法 MAC 输出复现，解析结果为空。

3. **[P1] 并行刷机共享远端临时文件，会混用或丢失公钥。**  
   各台都向 DMIT 的 `/tmp/snr-ak` 上传，再读取、删除；TX/BWG 分发也共享 `/tmp/snr-ak-tb`。可复现交错：A 上传→B 覆盖→A 查找自己的编号失败并删除→B 读取不存在的文件。更严重的是下游直接 `cat` 临时文件，可能把 B 公钥当成 A 本轮分发内容。批量预分配端口无法消除这些竞争。  
   证据：[diff_1_flash-snr.patch:391](/F:/vps/verify/round-2/diff_1_flash-snr.patch:391)–407。

4. **[P1] TX/BWG 分发命令传入的是字面量 `$K`，失败后仍继续。**  
   `O='-i $K ...'` 保留了字面量，后续 `$O` 展开不会再次展开其中的 `$K`。本地 shell 复现，`-i` 的下一参数确实为 `$K`。没有另外可用的默认认证时，分发失败；代码仅打印失败，不中止。  
   证据：[diff_1_flash-snr.patch:402](/F:/vps/verify/round-2/diff_1_flash-snr.patch:402)–411。

5. **[P1] “防劫持纠偏重试”没有纠正 API，而且第一次错误写入已发生。**  
   POST 使用 `M.BASE`，之后才查 DMIT。第二轮只是给执行 SSH 查询的子进程设置 `MGMT_API_BASE`：不会改变父进程的 `M.BASE`，也没有重新认证、重新录入；实际只是重复同一个 SQL。若入口指向 DO，旧库已经被写入后才中止，与“旧管理库冻结勿写”冲突。  
   证据：[diff_1_flash-snr.patch:239](/F:/vps/verify/round-2/diff_1_flash-snr.patch:239)–265；[变更说明.md:22](/F:/vps/verify/round-2/变更说明.md:22) 对纠偏效果的描述不成立。

6. **[P1] 擦写数据分区没有卸载成功或目标身份闸门。**  
   `umount` 的返回码被忽略，接着无条件覆盖 `/dev/mmcblk0p6` 前 256 MiB；`umount -l` 返回成功也不能证明所有引用及 I/O 已结束。代码没有核验该分区确为预期数据盘、保存恢复材料或验证擦写结果。该操作实际破坏文件系统元数据，不能等同于“只隔离”。  
   证据：[diff_1_flash-snr.patch:191](/F:/vps/verify/round-2/diff_1_flash-snr.patch:191)–200；[SOP:226](/F:/vps/docs/operations/新SN-R1刷机录入SOP-自动化-20260928.md:226) 的保留描述与实现存在冲突。

7. **[P2] WiFi 开机策略被追加到既有 `exit 0` 后，通常不会执行。**  
   步骤 5 明确保留并在 `exit 0` 前插入 onboot-hooks；步骤 9 仅删除旧 `# wifi-default-off` 起的尾部。新机不存在该标记时，原来的 `exit 0` 留着，后追加的 WiFi 重试及 cron 恢复块不可达。当前关 WiFi 不等于“人工开启后重启仍恢复开启”。  
   证据：[diff_1_flash-snr.patch:345](/F:/vps/verify/round-2/diff_1_flash-snr.patch:345)、[543](/F:/vps/verify/round-2/diff_1_flash-snr.patch:543)–558。

8. **[P2] PCDN 终验会把检查命令自身计入，还漏查部分清理对象。**  
   `ps w | grep -cE ...` 未排除 grep 及远端 shell；在显示命令参数的环境中，检查自身即可令计数非零。本地无 PCDN、只有该 grep 的进程输出样例得到计数 1。另一方面，终验没有覆盖清理列表中的 `net_qua_cli`、`inst_width`、`tcp_stun_ser`，也没有查残留监听及自启入口。  
   证据：[diff_1_flash-snr.patch:180](/F:/vps/verify/round-2/diff_1_flash-snr.patch:180)、[606](/F:/vps/verify/round-2/diff_1_flash-snr.patch:606)、[620](/F:/vps/verify/round-2/diff_1_flash-snr.patch:620)。SOP 已在 [244](/F:/vps/docs/operations/新SN-R1刷机录入SOP-自动化-20260928.md:244) 提醒这一误判。

9. **[P2] QUIC“复验”没有读取结果，前面还再次执行会挤位的脚本。**  
   mihomo 启动块已加入自愈，但随后又直接调用一次 `mihomo-ipt.sh`；按 SOP 所述机制，这会重新把 DROP 插到首位。下发双验后所谓“复验”仅执行 `-D/-I`，没有检查返回码、读取规则或更新验收结果。不能证明重载后顺序已经正确。  
   证据：[diff_1_flash-snr.patch:478](/F:/vps/verify/round-2/diff_1_flash-snr.patch:478)–491、[640](/F:/vps/verify/round-2/diff_1_flash-snr.patch:640)–643。

10. **[P2] 批量锁不具备互斥保证，失败也不能可靠交付。**  
    `exists`→普通 `open("w")` 是非原子操作，两进程可同时通过。超过 90 分钟只凭年龄接管，没有判断原进程是否仍活着；分配、子任务超时或日志写入异常后，也没有覆盖全流程的 `finally` 清锁。普通子任务返回非零时，批量主进程仍正常退出 0；超时则没有该任务完整日志或汇总。  
    证据：[diff_2_flash-batch.patch:97](/F:/vps/verify/round-2/diff_2_flash-batch.patch:97)–104、[113](/F:/vps/verify/round-2/diff_2_flash-batch.patch:113)–153。

11. **[P2] 分配查询失败会猜测编号和端口，端口池也会越界。**  
    SSH/数据库失败未检查返回码，空输出直接退回编号 8、端口 13230，可能复用既有身份及本地凭证文件。批量分配没有单台分配器的 `<13300` 限制。提取函数并 mock SSH 后复现：失败仍分配 `[8,9]`、`[13230,13231]`；当前最大口为 13299 时分配 `[13300,13301]`。  
    证据：[diff_2_flash-batch.patch:74](/F:/vps/verify/round-2/diff_2_flash-batch.patch:74)–95。

12. **[P2] 传输和任务失败处理仍不足，成功结论缺少重启后证据。**  
    `push()` 没有核对远端退出码、长度或摘要，`run()` 同样不检查退出码；发布超时返回 `None` 后，打印语句先执行 `t.get(...)`，会直接抛异常。此外，下发后的出口比较未去除换行，响应带换行即可误失败。最终成功条件也没有真实重启、MAC 生效及重启后清理持久性验证。  
    证据：[diff_1_flash-snr.patch:120](/F:/vps/verify/round-2/diff_1_flash-snr.patch:120)–128、[520](/F:/vps/verify/round-2/diff_1_flash-snr.patch:520)、[635](/F:/vps/verify/round-2/diff_1_flash-snr.patch:635)–646；[SOP:230](/F:/vps/docs/operations/新SN-R1刷机录入SOP-自动化-20260928.md:230) 要求真实重启验证。

两份补丁的 Python AST 均可解析，提取的 mihomo init 脚本通过 `sh -n`；语法通过不能排除上述执行问题。变更说明中关于 R7 已清理、重启验证及当前暴露面的内容属于历史陈述，本次未取得现网证据，不能据此独立确认 R7 已无残留。
