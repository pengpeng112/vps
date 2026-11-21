# qos-gate：R2 独立 PoC 执行器

本模块未接入生产配置、广州 API 或正式配额控制器。正式设备保持 observe。
`UDP ASSOCIATE` 为 `TCP_ONLY`；100MiB 大象流哨兵尚未实现，配置非零会明确拒绝。

## 运行与单位

仅允许字面量回环地址。默认 PoC 拓扑为 7895 视频、7896 国外下载、7897 禁用哨兵，
上游为独立 mihomo 的 7894 SOCKS listener（`proxy: AUTO`，避免再进入业务规则回环）。
`down_bps` 一律 bit/s：60000=7500B/s，100000=12500B/s。
两类各有共享桶；上传单独转发，不使用下行桶。

首次启动缺少有效状态文件时进入 emergency 直通；状态文件损坏也直通并报告 state_error。
只允许通过明确的 `clear-emergency` 恢复限速，须携带当前 `expected_generation`。

```json
{"cmd":"status"}
{"cmd":"release"}
{"cmd":"release-class","class":"video"}
{"cmd":"clear-emergency","expected_generation":2}
{"cmd":"set-rate","class":"video","down_bps":60000}
{"cmd":"reload"}
```

控制口为 mode0600 的 Unix socket；上例为逐行 JSON，代际数从实际 status 读取，不硬编码2。
release先同步落盘，再解除；全局和单类状态均跨进程重启保持。
reload/SIGHUP/set-rate只修改参数，不清人工紧急锁。
clear-emergency可带class，仅恢复该类，其它类的人工锁保留。
并发控制串行化；旧 expected_generation 拒绝，代际跨重启单调增加。

## 转发与资源边界

正常请求 EOF 只传播半关闭，继续转发响应；错误/取消才协同中止双向。
握手及上游应答8秒超时；读写操作5分钟无进展超时；连接等待可取消。
全 gate 最多256条被跟踪连接；单次下行预授权/读取最多4096B，套接字接收缓冲请求16KiB。
内核可能调整缓冲实际值；其它代理层的缓存也不受这个数单独约束。
ClassStats记录实际成功转发的载荷，桶total是授权统计，不是供应商计费字节。
本模块没有生产看门狗/平台策略恢复链，不能把独立 PoC 当成可直接正式发布的完整系统。

## 构建与验证

```text
go vet ./...
go test -count=1 ./...
GOOS=linux GOARCH=arm64 CGO_ENABLED=0 go build -trimpath -ldflags "-s -w" -o qos-gate .
```

跨平台变量设置应使用执行环境的正确语法。回归入口还包括仓库根的
`python review/batch12_regression_review_20261119.py`，它只在副本中添加缺陷负例。

独立 PoC 的窗口调节只用于专用沙箱节点，不修改正式节点或全局 WAN。
固定版本支持字段参见 [mihomo v1.19.31 源码](https://github.com/MetaCubeX/mihomo/blob/v1.19.31/adapter/outbound/hysteria2.go)。
Hysteria 接收窗口的意义参见 [官方性能说明](https://www.hy2.app/docs/advanced/Performance/)。
省流必须同时记录客户端载荷和相关上游 IP 字节，注明时间窗、缓冲尾差与计数范围。
