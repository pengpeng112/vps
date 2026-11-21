# Batch2 执行记录：qos-gate 开发 + golden.yaml 分类拆分 + R2 隔离 PoC（GLM，2026-11-19 19:16—20:15）

> 会话：`GLM-Batch2-20261119`；锁：device:R2=`lock-f87d10053921`、server:BWG=`lock-f0156ff2ecfb`（均已释放）。
> 边界遵守：全批 observe、绝不 enforce、未触碰 `ratelimit-server-executor.sh`（DEPRECATED）、未操作 WAN tc root、未推路由器配置、未动广州。

## 1. qos-gate 开发（✅ 交付真实代码与测试）

**位置**：`mgmt/qos-gate/`（纯 Go 标准库，module `qosgate`）。文件：`main.go`（入口/SIGHUP/优雅退出）、`bucket.go`（聚合令牌桶）、`socks.go`（SOCKS5 CONNECT：域名/IPv4/IPv6 原样转发不本地解析；UDP ASSOCIATE 明确拒绝=TCP_ONLY）、`pipe.go`（反压受控拷贝：下行先 Take 后 Read，无令牌不读上游）、`gate.go`（三类入站+上游 dial）、`ctl.go`（Unix socket 0600 控制：status/reload/release/release-class/clear-emergency/set-rate）、`config.go`（JSON 配置+emergency 原子持久化 tmp+rename）。

**拓扑**：业务→gate（7895 视频/7896 下载/7897 大象哨兵）→ mihomo 沙箱 listener `type: socks :7894 proxy: AUTO`（直连组不经 rules，无环）→ 出口。

**关键实现语义**：
- 下行聚合共享桶（`down_bps` 按 Q0.1 为 **bit/s**，内部 /8 转 B/s——真机实测抓出的 8 倍单位 bug 已修+测试口径同步）；
- `Take` 返回**实际授权量**，调用方按授权量 Read（曾发现"授权 min(n,burst) 却读满 n"的 4 倍放大 bug，已修）；
- 上游连接 `SetReadBuffer(16KB)`：强制内核缓冲不吞反压（否则小流量测试时窗口数据全进内核缓冲冒充"已限速"）；
- 紧急解除：先原子持久化 `qos-gate-state.json`（generation 递增）再 Broadcast 唤醒（Q4.3 顺序）；重启读持久化 emergency 恢复直通（Q4.4 旧限制不复活）；
- 大象哨兵 `elephant_mb=0`=禁用直通（本批），Batch3 置 100 的挂点已留（`ClassCfg.ElephantMB`）。

**测试**（`go test` 8 项全 PASS，本机 windows/amd64）：60k/100kbit 精度（2.82s/2.64s vs 理想）、2/4 并发共享聚合（3.00/3.00/2.40s=总速率不变，非每连接各一份）、紧急解除唤醒 **20ms**（远小于 3s 目标）、SOCKS5 三种目标+UDP 拒绝、端到端限速（64KiB@7500B/s=8.21s 反压实测）、热重载、状态持久化往返。

**交叉编译**：`GOOS=linux GOARCH=arm64 CGO_ENABLED=0 -trimpath -ldflags "-s -w"` → 静态 ELF 2.7MB，**最终版 SHA256=046ce0eb…（qos-gate v4）**，`file` 实证 ARM aarch64 statically linked。

## 2. golden.yaml 分类拆分（02-Q2.1 精确表）✅

- **US-MEDIA 收窄为纯视频**：移除 spotify.com/scdn.co/spotifycdn.com/githubusercontent.com/githubassets.com（共 5 条）；
- **移回普通 PROXY**（永不限速）：spotify.com、scdn.co、spotifycdn.com、githubassets.com、raw.githubusercontent.com，加 `githubusercontent.com` 兜底（FOREIGN-DL 精确子域之后命中）；
- **新增 FOREIGN-DL 组**（select，PoC 成员=[PROXY] 全速；Batch3 enforce 时平台渲染切 QOS-DL-GATE）与 12 条精确子域规则：objects/release-assets.githubusercontent.com、codeload.github.com、dl.google.com（原位从 PROXY 改指，保持"先于 google-cn"语义）、gvt1.com、huggingface.co、cdn-lfs.huggingface.co、civitai.com、mega.nz、dropboxusercontent.com、mediafire.com、cdn-telegram.org；
- **DOWNLOAD=DIRECT 与全部国内/游戏/保护规则零改动**；
- `template_test.go`：新增 `TestGoldenQ21ClassSplit`（五组断言：US-MEDIA 无音乐/GitHub、FOREIGN-DL 存在+成员+精确子域、dl.google 先于 google-cn 顺序、githubusercontent 兜底次序、DOWNLOAD 首位 DIRECT+保护规则+尾条 MATCH,DIRECT）；旧 `TestGoldenRules` 的 dl.google 断言同步 FOREIGN-DL。
- **`python scripts/build_gz_api.py --check-only` ALL GREEN**（LF/源码范围/vet/全量 go test；check-only 模式为本次新增，git 元文件常量白名单）。

## 3. R2 隔离 PoC（全部在 /tmp/qos-poc，正式配置零接触）

### 3a. 拓扑验证 ✅
- **mihomo v1.19.31 arm64 真机原生支持 `listeners: [{type: socks, port: 7894, listen: 127.0.0.1, proxy: AUTO}]`**（`mihomo -t` successful 实证，独立工作目录 `-d /tmp/qos-poc`，dat/mmdb/provider 缓存复制，禁 TUN，DNS 不监听避 1053 冲突，cache 独立）；
- 沙箱 mihomo（PID 记录于 pid.mihomo）+ qos-gate v4（PID 记录于 pid.gate）双进程起；经 gate→7894→AUTO→BWG 冒烟 204/200 全通。

### 3b. 一键解封先行（Q6.3.3）✅
- set-rate video=2000B/s → 60KiB 实测 31.0s（理想 ~30s ✓ 桶生效且 total=62000 与下载量精确一致）；
- **release 命令时延 1-2ms**，持久化 `{"emergency":true,"generation":2}` 原子落盘，桶立即直通（本地单测唤醒 20ms）；
- clear-emergency → 恢复 60kbit（generation 3）✓。脱离平台本地 ctl 即可解封（Q4.8）✓。

### 3c. 60kbit 限速实测 ✅（T06/T07 本机项）
| 项 | 实测 | 判定 |
|---|---|---|
| 同路基线（7894 直连） | 1.4s 拉 1.43MB ≈ **1MB/s** | 基线远高于目标 ✓ |
| 单连接（512KiB 级） | 60s 窗口 347894B = **5798B/s**（稳定段桶采样 7500→6700B/s，首尾含握手/截断） | ≈60kbit ✓ |
| 2 并发聚合 | 100s 窗口 637766B = **6376B/s** | 聚合=单桶 ✓ 非每连接各 60k |
| 4 并发聚合 | 100s 窗口 672551B = **6723B/s** | 同上 ✓ |
| 百度直连 | 200 **0.172s**（<0.2s ✓） | 零扰动 ✓ |
| Google g204 / GitHub | 经正式 7890（不进 gate）204 0.79s / 200 4.8s | 全速不误伤 ✓ |
| 大象哨兵 7897（elephant_mb=0） | 4.5s 拉 677KB（直通速率） | 禁用语义 ✓ |
| gate 计量对账 | video.down=1793542B ≈ 客户端三轮合计 | 类计量与实测一致 ✓ |

### 3c+. 省流对账（Q3.3）⚠️ **PARTIAL_SAVE_TRAFFIC（如实记录）**
| 侧 | 本窗口字节 | 速率 |
|---|---|---|
| HK 源站发出（sent.log 逐连接） | **4,194,304B/连接全发满** | 源站不省（数据进中间缓冲） |
| 客户端收到（curl） | 347,894B/60s | 5,798B/s |
| **BWG→R2 hy2 隧道（供应商计费方向，iptables 纯计数规则临时插入）** | **788,072B/250 包**（窗口内从 0 起） | **~13KB/s，较基线 ~1MB/s 降 98.7%** |

**结论**：承载供应商（BWG 计费方向）的真实隧道字节被反压约束在 ~13KB/s，**上游省流在供应商计费面成立**；但源站（HK）发出的 4MB 被 BWG sing-box/内核用户态缓冲吸收（比客户端多收 2.26×），**未达理想 1.1× 比值**——判 **PARTIAL**，非 PASS 也未触发硬停（承载面省流成立）。Batch 3 前置改进：①稳态长窗口（≥3 分钟）复测比值（本窗口含前轮连接的缓冲排空尾差，计数窗口污染已识别）②评估 mihomo 沙箱/入站侧进一步缩小缓冲 ③若比值仍 >1.5×，按 Q3.1 停点机制上报用户决策（服务端整形 vs 接受代理链缓冲吸收的现实边界）。

### 过程修复记录（真实调试史，防复犯）
1. busybox date 无 `%N` → elapsed 假 0ms（改 python3 计时）；
2. Git Bash sha256sum 对反斜杠路径输出 `\hash *path` 转义行（夹具一律 POSIX 路径）；
3. Take 授权量 vs Read 长度不匹配（4 倍放大）→ Take 返回 granted；
4. `down_bps` bit/s 被按 B/s 用（8 倍）→ setLocked /8 + 测试口径全改；
5. read_counter 的 comment=端点 tag 无方向后缀（夹具修正）；
6. HK 服务器部署踩 shell 转义层（`\\r` 多层变形）→ 最终 Write 工具本地生成+SFTP 直传+py_compile 双端验证；
7. `pkill -f qos-poc-srv` 自匹配杀掉自身 bash → `[s]erver.py` 技巧或分步执行。

## 4. 清理与生产状态 ✅

- BWG 计数规则删除（grep poc-meter=0）、HK 服务器停止+`/tmp/qos-poc-srv` 删除；
- R2：gate/mihomo 沙箱按记录 PID 精准 kill（GATE_DEAD/MIHOMO_DEAD 实证），`/tmp/qos-poc` 删除，7891/7894/7895/7896/7897/9091 全释放；
- **生产零扰动**：正式 mihomo PID 4880 不变、prod g204=204 0.48s、baidu 200 0.18s、计量 `health=ok partial=true(当日正确) quota_eligible=false` seq=563（Batch1 修复的状态机正常运行中）；
- 锁全部释放（device:R2、server:BWG）。

## 5. 遗留与 Batch 3 前置

1. **PARTIAL_SAVE_TRAFFIC 稳态复测**（见 3c+）——Batch 3 开工首项；
2. `quota_eligible` 状态机（Batch1 修复）待 11-20 00:00 换日后产出首个资格自然日；
3. gate 的 active_unc 覆盖条件本地夹具未覆盖（真机 9090 路径可用），Batch 3 平台接入时一并验；
4. UDP ASSOCIATE 明确 TCP_ONLY（golden 既有 UDP443 REJECT 未动，T09 如实标 BLOCK）；
5. `ratelimit-ctl-v2.sh` 与 qos-gate 的控制器整合（Batch 3 广州 API 上线时统一）。

## 产物索引
- 代码：`mgmt/qos-gate/`（v4，静态 ELF SHA256 前缀 046ce0eb…，2.69MB）
- 模板：golden.yaml FOREIGN-DL/PROXY 拆分 + `TestGoldenQ21ClassSplit`
- 工具脚本：`logs/batch2_poc_*.py`、`logs/batch1_meter_isotest_20261119.py`（夹具复用）
- 设备侧：`/tmp/qos-poc/*`（已清）、R2 备份 `backup-meter-20261119/`（Batch1 产物，未动）
