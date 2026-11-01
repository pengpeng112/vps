# R7 修复执行令 v1（Codex 指挥，2026-11-01）

> 来源：codex exec（gpt-6.1-sol / xhigh，read-only 沙箱）会话输出摘录。
> 输入：ZCode 四轮只读实测证据 + 禁令库。状态：待 ZCode/deepseek 双评估后终审。
> 输出文件由会话整理，未改动 Codex 原文语义。

**1. 锁定本轮裁决与窗口**

- A：先用 `PUT /proxies/PROXY` 热修，再验证实际缓存中 `selected/PROXY=AUTO`。禁止在线删除、替换、编辑 `cache.db`。
- B：本轮只注释 VPS4。VPS2/VPS3 登记观察，不扩大范围。确认配置读取机制安全后改文件，不重启 `mgmt-tunnel`。
- C：留置 `tunnel-watchdog.sh`；不接 cron、不执行、不删除。当前无运行影响，接入会新增未经验证的隧道控制逻辑。
- D：本轮不修改仓库 `rssh-probe.sh`；登记单独治理项，后续按广州实际部署方式修订并验证。
- E：当前窗口预算 35 分钟，最多 40 分钟；午夜验收另计。不得把“当前修复完成”写成“跨重启验收完成”。

按设备时间执行：2026-11-01 23:00 前开始，23:40 前结束写入；2026-11-02 00:00 沿用既有计划重启，安排重启前后值守。错过窗口则停止安排午夜前写入。

将本稿交 ZCode、deepseek 独立评估；将第 2 步绑定信息、事务接口及差异意见一并送终审。

**2. 完成只读预检与执行参数绑定，预算 0—8 分钟**

除明确标注外，命令均在已验证的 R7 交互会话内逐段执行。禁止把代码块封装成反向 SSH 文件推送链。任何命令异常，停止进入下一步。

广州侧记录监听及会话归属：

```sh
ss -lntp | awk 'NR==1 || $4 ~ /:132[0-9][0-9]$/'
ss -ntp | awk 'NR==1 || $4 ~ /:13229$/ || $5 ~ /:13229$/'
```

使用既有身份凭据与已登记主机密钥，验证一条独立的新连接：

```sh
ssh -p 13229 \
  -o BatchMode=yes \
  -o StrictHostKeyChecking=yes \
  -o ConnectTimeout=5 \
  -o ControlMaster=no \
  -o ControlPath=none \
  root@127.0.0.1 'cat /proc/sys/kernel/random/boot_id'
```

R7 侧：

```sh
set +x
umask 077

date '+%Y-%m-%d %H:%M:%S %z'
ubus call system board
cat /proc/sys/kernel/random/boot_id
cat /proc/uptime
crontab -l

R7_MPID=$(pidof mihomo)
test -n "$R7_MPID"
test "$(printf '%s\n' "$R7_MPID" | wc -w)" -eq 1

R7_BOOT=$(cat /proc/sys/kernel/random/boot_id)
ls -l "/proc/$R7_MPID/fd"
df -Pk /root /etc/routermgmt
```

在受控会话中核对运行参数、打开文件、服务脚本，绑定以下变量；不得猜路径、鉴权变量名或健康 URL：

| 变量 | 绑定依据 |
|---|---|
| `R7_CFG` | 当前 mihomo 实际加载的配置文件 |
| `R7_CACHE` | 当前 mihomo 打开的 `cache.db` 的真实路径 |
| `R7_SELFHEAL` | 当前 self-heal v3.1 脚本 |
| `R7_AGENT_PID` | 当前唯一 agent 进程 |
| `R7_HEALTH_URL` | 四轮实测使用的本地 18443 健康 URL |
| `R7_API_SECRET` | 既有 X14 流程已核验的运行进程 environ 来源密钥 |
| `R7_EGRESS_URL` | 四轮实测使用的出口 IP 回显 URL |

密钥只在内存中使用；不得输出完整 environ、密钥或带密钥的调试日志。来源、匹配结果或鉴权异常即停。

```sh
: "${R7_CFG:?STOP: missing runtime config}"
: "${R7_CACHE:?STOP: missing active cache}"
: "${R7_SELFHEAL:?STOP: missing selfheal path}"
: "${R7_AGENT_PID:?STOP: missing agent PID}"
: "${R7_HEALTH_URL:?STOP: missing health URL}"
: "${R7_API_SECRET:?STOP: missing verified API secret}"

test -f "$R7_CFG"
test -f "$R7_CACHE"
test -f "$R7_SELFHEAL"
test -d "/proc/$R7_AGENT_PID"
test ! -L "$R7_CACHE"

readlink -f "$R7_CACHE"
df -Pk "$R7_CACHE"
md5sum "$R7_SELFHEAL"
```

确认以下条件后才准进入写入阶段：

- 设备身份为 `SN-R7-AX3000 / rtr-37cf72`，独立新连接成功。
- 缓存位于可持久化、可写文件系统；运行文件未被删除、替换或指向另一份缓存。
- 已核对当前版本对应的选择持久化实现与启动路径。
- 已读 self-heal v3.1：确认暂停标志检查位置、事务锁、事务开始/提交/回滚原语及当前无未完成事务。
- 在评审附件中列出上述事务原语的**真实命令**；不得用 `true`、空函数或手造锁代替。
- 若 `tunnel.conf` 受事务管理，后续必须通过既有事务入口提交。
- `329c6081` 按已给出的摘要指纹核对；同时记录完整 MD5，不把八位指纹当完整 MD5。

停止点：任何绑定缺失、事务接口不明确、基线已有退化，停止写入，不修改服务与 cron。

**3. 建立备份、API 与暂停操作，预算 8—11 分钟**

R7 侧：

```sh
R7_RUN="/root/r7fix-$(date +%Y%m%d-%H%M%S)"
mkdir "$R7_RUN"

R7_API='http://127.0.0.1:9090'
R7_TUN='/etc/routermgmt/tunnel.conf'

api() {
    curl --noproxy '*' --silent --show-error --fail \
      --connect-timeout 2 --max-time 8 \
      -H "Authorization: Bearer $R7_API_SECRET" "$@"
}

pause_begin() {
    test ! -e /tmp/selfheal.pause || return 1
    (set -C; : > /tmp/selfheal.pause) || return 1
    touch /tmp/selfheal.pause || return 1
    R7_PAUSE_INODE=$(ls -id /tmp/selfheal.pause | awk '{print $1}')
    R7_PAUSE_OWN=1
}

pause_end() {
    test "${R7_PAUSE_OWN:-0}" = 1 || return 1
    test "$(ls -id /tmp/selfheal.pause | awk '{print $1}')" = "$R7_PAUSE_INODE" || return 1
    rm /tmp/selfheal.pause || return 1
    R7_PAUSE_OWN=0
}

cache_copy() {
    local tag="$1" h0 h1 hc
    h0=$(md5sum "$R7_CACHE" | awk '{print $1}') || return 1
    cp -p "$R7_CACHE" "$R7_RUN/cache.$tag.db" || return 1
    h1=$(md5sum "$R7_CACHE" | awk '{print $1}') || return 1
    hc=$(md5sum "$R7_RUN/cache.$tag.db" | awk '{print $1}') || return 1
    test "$h0" = "$h1" && test "$h0" = "$hc" || return 1
    md5sum "$R7_RUN/cache.$tag.db"
}

cp -p "$R7_CFG" "$R7_RUN/mihomo.config.before"
cp -p "$R7_SELFHEAL" "$R7_RUN/selfheal.before"

md5sum "$R7_CFG" "$R7_RUN/mihomo.config.before"
md5sum "$R7_SELFHEAL" "$R7_RUN/selfheal.before"

api -o "$R7_RUN/version.json" "$R7_API/version"
api -o "$R7_RUN/proxy.before.json" "$R7_API/proxies/PROXY"
api -o "$R7_RUN/auto.before.json" "$R7_API/proxies/AUTO"
api -o "$R7_RUN/proxies.before.json" "$R7_API/proxies"

R7_OLD_PROXY=$(jsonfilter -i "$R7_RUN/proxy.before.json" -e '@.now')
test -n "$R7_OLD_PROXY"
test "$(jsonfilter -i "$R7_RUN/proxy.before.json" -e '@.type')" = Selector

jsonfilter -i "$R7_RUN/proxy.before.json" -e '@.all[*]'
jsonfilter -i "$R7_RUN/auto.before.json" -e '@.now'

cache_copy before
```

核对 `PROXY.all` 包含 `AUTO`，且 `AUTO.now=NODE-PICK`。

将备份通过既有已验证文件通道下载。若广州已有可用的传统 SCP，使用下式；`R7_RUN` 必须绑定为设备刚输出的真实目录：

```sh
umask 077
R7_LOCAL=$(mktemp -d /var/tmp/r7-review.XXXXXX)

scp -O -P 13229 \
  -o BatchMode=yes \
  -o StrictHostKeyChecking=yes \
  "root@127.0.0.1:$R7_RUN/cache.before.db" "$R7_LOCAL/"

md5sum "$R7_LOCAL/cache.before.db"
bbolt check "$R7_LOCAL/cache.before.db"
bbolt get --format ascii-encoded \
  "$R7_LOCAL/cache.before.db" selected PROXY
```

下载 MD5 必须与设备副本一致；`bbolt check` 必须成功。仅对下载副本运行数据库工具，不对运行中的缓存加锁或打开数据库。

将该副本作为经校验的取证备份；**禁止据此在线覆盖运行缓存**。复制中哈希不稳定时，最多重新取证一次；仍失败则停止写入。

**4. 执行唯一的 PROXY 热修，预算 11—15 分钟**

按已核验的 v3.1 事务保护进入本次变更；确认事务空闲后：

```sh
pause_begin
```

暂停标志只覆盖短暂应用窗口；不得以它替代现行事务保护。

```sh
R7_CODE=$(api \
  -X PUT \
  -H 'Content-Type: application/json' \
  --data '{"name":"AUTO"}' \
  -o "$R7_RUN/proxy.put.body" \
  -w '%{http_code}' \
  "$R7_API/proxies/PROXY")

test "$R7_CODE" = 204

api -o "$R7_RUN/proxy.after.json" "$R7_API/proxies/PROXY"
api -o "$R7_RUN/auto.after.json" "$R7_API/proxies/AUTO"

test "$(jsonfilter -i "$R7_RUN/proxy.after.json" -e '@.now')" = AUTO
test "$(jsonfilter -i "$R7_RUN/auto.after.json" -e '@.now')" = NODE-PICK
test "$(pidof mihomo)" = "$R7_MPID"
test "$(cat /proc/sys/kernel/random/boot_id)" = "$R7_BOOT"
test -d "/proc/$R7_AGENT_PID"

test "$(curl --noproxy '*' --silent --show-error \
  --connect-timeout 2 --max-time 8 \
  -o /dev/null -w '%{http_code}' "$R7_HEALTH_URL")" = 200
```

同时完成广州独立新连接检查、现行心跳检查及原 LAN 上网检查。通过后按既有事务机制提交，再执行：

```sh
pause_end
```

失败即停：

- HTTP 非 204：先 GET 确认实际状态，禁止盲目重复 PUT。
- 13229、新连接、健康或用户流量退化：执行第 10 步 API 回退，再退出本步事务并解除本次暂停。
- 仅持久化证据不足、实时功能正常：保留 `AUTO`，进入第 5 步取证；不得直接恢复旧缓存。

本步禁止调用 `/configs` 写接口、`/restart`、连接清空接口，以及任何服务重启或防火墙 reload。

**5. 验证持久化，预算 15—20 分钟**

按设备版本核对实际生效配置中的 `profile.store-selected`，并审查启动脚本、配置生成器及缓存恢复逻辑。不要从 `GET /configs` 缺少该字段推断它为 false。

标准实现的选择 API 会调用 `SetSelected`；该函数在持久化关闭、数据库未打开或写入失败时，仍可能让 API 返回 204。因此，**204、mtime 或 MD5 变化均不能单独作为写穿证据**。参照 [选择 API 实现](https://github.com/MetaCubeX/mihomo/blob/Meta/hub/route/proxies.go)及[缓存实现](https://github.com/MetaCubeX/mihomo/blob/Meta/component/profile/cachefile/cache.go)，以设备版本对应源码为准。

R7 侧：

```sh
sync
cache_copy after

logread | grep -Ei \
  'CacheFile|read.only|no space left|I/O error' | tail -n 40
```

广州/分析端下载 `cache.after.db`，核对 MD5 后：

```sh
bbolt check "$R7_LOCAL/cache.after.db"

test "$(bbolt get --format hex \
  "$R7_LOCAL/cache.after.db" selected PROXY)" = 4155544f
```

必须同时满足：

- 有效选择缓存为 `selected/PROXY=AUTO`。
- 运行时持久化开关有效。
- 下次启动读取同一持久缓存，没有启动恢复旧库或其他选择写入者。
- 当前运行与原 LAN 测试继续通过。

按 [mihomo 持久化配置说明](https://wiki.metacubex.one/config/general/)，通过上述检查后记录：**预期重启保持 AUTO，待午夜实证**。

持久化失败时按以下裁决停止：

- 开关关闭、路径在临时盘、写入失败或启动覆盖：保留已验证正常的实时 `AUTO`；不进入 VPS4 写入，回报具体原因。
- 配置开关修订另列单次变更；不得用热重载试验其效果。
- 定向修改 `selected/PROXY` 仅作为后续停核维护方案，本窗口不执行；禁止删除整库。
- 对平台 `set_proxy_state` 只读审查任务 `t-9c6a9b5d776f` 的执行目标、实际调用、响应与后续写入链。不得仅凭任务“成功”认定持久化，也不得为验证再次下发任务。

**6. 审核 VPS4 注释的运行时安全性，预算 20—24 分钟**

R7 侧：

```sh
grep -RnsE \
  'tunnel\.conf|VPS4_|159\.223\.36\.119|inotify|inotifywait|respawn|reload_trigger' \
  /etc/init.d /etc/routermgmt /etc/hotplug.d /etc/rc.local /etc/crontabs
```

完整阅读命中的启动脚本、子脚本和守护逻辑。确认：

- 配置只在服务启动时读取；修改文件不会触发监控、reload、重启或隧道清理。
- 注释 VPS4 后，缺失变量会跳过该端点；没有默认值、硬编码地址或旧环境变量将它补回。
- 当前 13229 控制链不依赖 VPS4。
- `respawn` 保持 `3600 0 0`。
- self-heal、平台生成器不会把这两行恢复。
- 若受配置事务管理，已明确正常提交及其完整性记录更新方法。

**上述条件全部成立，才把“文件改动无即时运行副作用”作为本机结论。** 任一条件不成立，停止 VPS4 写入，提交具体脚本位置；不得临时改守护逻辑或改用重启验证。

**7. 备份并生成 VPS4 候选文件，预算 24—27 分钟**

R7 侧：

```sh
test -f "$R7_TUN"
test ! -L "$R7_TUN"

R7_TUN_MD5=$(md5sum "$R7_TUN" | awk '{print $1}')
cp -p "$R7_TUN" "$R7_RUN/tunnel.conf.before"

test "$(md5sum "$R7_RUN/tunnel.conf.before" | awk '{print $1}')" = "$R7_TUN_MD5"

test "$(grep -cE '^[[:space:]]*VPS4_HOST=' "$R7_TUN")" -eq 1
test "$(grep -cE '^[[:space:]]*VPS4_PORT=' "$R7_TUN")" -eq 1

grep -nE '^[[:space:]]*VPS4_(HOST|PORT)=' "$R7_TUN"
```

人工核对两条赋值确为 `159.223.36.119`、`28722`；出现额外命令、其他取值或重复项即停。

```sh
R7_CAND=$(mktemp /etc/routermgmt/.tunnel.conf.r7.XXXXXX)
cp -p "$R7_TUN" "$R7_CAND"

sed \
  -e '/^[[:space:]]*VPS4_HOST=/s/^\([[:space:]]*\)/\1# /' \
  -e '/^[[:space:]]*VPS4_PORT=/s/^\([[:space:]]*\)/\1# /' \
  "$R7_TUN" > "$R7_CAND"

sh -n "$R7_CAND"

test "$(grep -cE '^[[:space:]]*VPS4_(HOST|PORT)=' "$R7_CAND")" -eq 0

diff -u "$R7_TUN" "$R7_CAND"
```

`diff` 此处预期返回 1；必须只显示两条 VPS4 赋值前增加 `# `。其他任何变化即停。

验证反向还原后逐字节一致：

```sh
sed \
  -e 's/^\([[:space:]]*\)# \(VPS4_HOST=.*\)$/\1\2/' \
  -e 's/^\([[:space:]]*\)# \(VPS4_PORT=.*\)$/\1\2/' \
  "$R7_CAND" > "$R7_RUN/tunnel.roundtrip"

cmp -s "$R7_RUN/tunnel.conf.before" "$R7_RUN/tunnel.roundtrip"
```

停止点：语法、差异或字节校验异常，删除本次候选文件，保持在线文件原状。

**8. 原子提交 VPS4 文件变更，预算 27—30 分钟**

先进入既有 v3.1 配置事务，再执行：

```sh
pause_begin

test "$(md5sum "$R7_TUN" | awk '{print $1}')" = "$R7_TUN_MD5"
cmp -s "$R7_TUN" "$R7_RUN/tunnel.conf.before"

mv -f "$R7_CAND" "$R7_TUN"
sync

sh -n "$R7_TUN"
test "$(grep -cE '^[[:space:]]*VPS4_(HOST|PORT)=' "$R7_TUN")" -eq 0
md5sum "$R7_TUN"

test "$(pidof mihomo)" = "$R7_MPID"
test "$(cat /proc/sys/kernel/random/boot_id)" = "$R7_BOOT"
test -d "/proc/$R7_AGENT_PID"
```

执行广州独立新连接及原健康检查。通过后，按既有事务机制提交完整性记录，再执行：

```sh
pause_end
```

不运行 `mgmt-tunnel restart/reload`，不杀任何 SSH/autossh 进程。若配置确为启动时读取，当前 DO 重试可能持续到既定重启；不得因此追加第二个运行态变更。

失败即停：文件异常或通道、上网退化，按第 10 步恢复 `tunnel.conf`；不连带回退已通过的 PROXY 变更。

**9. 完成当前窗口验收，预算 30—35 分钟**

R7 侧：

```sh
api -o "$R7_RUN/proxies.final.json" "$R7_API/proxies"
api -o "$R7_RUN/proxy.final.json" "$R7_API/proxies/PROXY"
api -o "$R7_RUN/auto.final.json" "$R7_API/proxies/AUTO"

test "$(jsonfilter -i "$R7_RUN/proxy.final.json" -e '@.now')" = AUTO
test "$(jsonfilter -i "$R7_RUN/auto.final.json" -e '@.now')" = NODE-PICK

test "$(pidof mihomo)" = "$R7_MPID"
test "$(cat /proc/sys/kernel/random/boot_id)" = "$R7_BOOT"
test -d "/proc/$R7_AGENT_PID"
test ! -e /tmp/selfheal.pause

cmp -s "$R7_CFG" "$R7_RUN/mihomo.config.before"
cmp -s "$R7_SELFHEAL" "$R7_RUN/selfheal.before"

md5sum /etc/routermgmt/tunnel-watchdog.sh
sh -n "$R7_TUN"
netstat -nt
```

沿用既有只读 G1—G6 验收命令，核对 agent=1、mihomo=1、心跳 1 秒、NODE-PICK 六节点及 AIRPORT 37 节点 alive、本地健康 200。不另触发批量健康检查。

在原 LAN 测试端执行，禁止把路由器本机直连 curl 代替透明代理验收：

```sh
curl --noproxy '*' --fail --silent --show-error \
  --connect-timeout 5 --max-time 15 "$R7_EGRESS_URL"

curl --noproxy '*' --silent --show-error \
  --connect-timeout 5 --max-time 15 \
  -o /dev/null -w '%{http_code}\n' \
  https://www.gstatic.com/generate_204
```

出口应为 `154.36.139.155`，gstatic 应为 204；广州独立新连接继续成功。

记录状态为：

- PROXY：即时修复通过，缓存取证通过，跨重启待验。
- VPS4：文件修订通过，下次启动生效待验。
- watchdog：留置，未接入。
- 仓库漂移：另单治理，广州部署未变。
- VPS2/VPS3：观察项。
- 结论仅适用于 R7。

**10. 按需执行本步回退；禁止批量回退**

PROXY 回退仅用于本次选择切换导致通道或用户流量退化。使用备份中的原值，不硬编码节点：

```sh
R7_OLD_PROXY=$(jsonfilter -i "$R7_RUN/proxy.before.json" -e '@.now')
test -n "$R7_OLD_PROXY"

R7_ROLLBACK_JSON=$(printf '{"name":"%s"}' "$R7_OLD_PROXY")

R7_CODE=$(api \
  -X PUT \
  -H 'Content-Type: application/json' \
  --data "$R7_ROLLBACK_JSON" \
  -o "$R7_RUN/proxy.rollback.body" \
  -w '%{http_code}' \
  "$R7_API/proxies/PROXY")

test "$R7_CODE" = 204
api -o "$R7_RUN/proxy.rollback.json" "$R7_API/proxies/PROXY"

test "$(jsonfilter -i "$R7_RUN/proxy.rollback.json" -e '@.now')" = "$R7_OLD_PROXY"
```

本次已知原值不含 JSON 特殊字符；若备份实测原值含引号、反斜杠或控制字符，停止使用上述 JSON 构造，改用已验证 JSON 编码工具。

`tunnel.conf` 回退须进入既有配置回退事务，并在本次拥有的暂停窗口内执行：

```sh
test "$(md5sum "$R7_RUN/tunnel.conf.before" | awk '{print $1}')" = "$R7_TUN_MD5"

R7_RESTORE=$(mktemp /etc/routermgmt/.tunnel.conf.restore.XXXXXX)
cp -p "$R7_RUN/tunnel.conf.before" "$R7_RESTORE"

sh -n "$R7_RESTORE"
mv -f "$R7_RESTORE" "$R7_TUN"
sync

cmp -s "$R7_TUN" "$R7_RUN/tunnel.conf.before"
md5sum "$R7_TUN"
```

按既有事务机制恢复完整性记录，验证通道和上网后解除本次暂停。禁止恢复整份 `cache.db`，禁止重启服务促成回退。

若 13229 已断，停止通过该通道反复写入；仅使用既有已核验备用控制入口。没有可用入口时，回报失联，不远程盲杀 stale 会话。

**11. 执行午夜前后验收**

2026-11-01 23:55，重新确认：

```sh
date '+%Y-%m-%d %H:%M:%S %z'
cat /proc/sys/kernel/random/boot_id
test ! -e /tmp/selfheal.pause

api -o "$R7_RUN/proxy.preboot.json" "$R7_API/proxies/PROXY"
test "$(jsonfilter -i "$R7_RUN/proxy.preboot.json" -e '@.now')" = AUTO

sh -n "$R7_TUN"
grep -nE 'VPS4_(HOST|PORT)=' "$R7_TUN"
```

重新取 `cache.preboot.db` 副本并在分析端验证 `selected/PROXY=AUTO`；确认无未完成事务。不要主动执行 `reboot`。

2026-11-02 00:00 后，待既定重启恢复：

1. 广州新建连接，核对既有主机密钥、设备身份及 **boot_id 已改变**。端口 LISTEN 本身不算设备恢复。
2. 重新绑定新 PID、运行参数和 environ 鉴权信息；不得沿用旧 PID。
3. **任何补救 PUT 之前**，先保存 `/proxies/PROXY`、`/proxies/AUTO` 和新缓存证据。
4. 重做第 9 步验收，确认 `PROXY=AUTO`、`AUTO=NODE-PICK`。
5. 检查配置中的 VPS4 仍被注释，实际进程树中没有 VPS4 子连接。

```sh
date '+%Y-%m-%d %H:%M:%S %z'
cat /proc/sys/kernel/random/boot_id
cat /proc/uptime
pidof mihomo

grep -nE 'VPS4_(HOST|PORT)=' /etc/routermgmt/tunnel.conf
netstat -nt | awk '$5=="159.223.36.119:28722"'
```

跨至少一个已审明的重试周期观察，且不少于 3 分钟；若周期超过值守窗口，记录未完成，不能据短时无连接宣布通过。重启后不应出现新的 VPS4 建连或认证重试。

若 PROXY 回到旧节点，先留证，再按第 4 步进行一次受控 API 热修；状态标为“实时恢复，持久化未通过”，禁止清缓存、追加 cron 或重启试错。

若 VPS4 仍重试，按启动默认值、配置覆盖、其他服务实例定位；本轮不杀进程。**只有午夜全部通过，才关闭 A/B 修复项。**

**12. 回退总表**

| 触发条件 | 回退或停点 | 禁止动作 |
|---|---|---|
| 预检、密钥或事务机制不明 | 停在写入前，反馈缺项 | 猜密钥、手造锁、跳过事务 |
| API 非 204／超时 | GET 确认实际状态；停止重复写 | 盲目重试 PUT |
| API 切换导致通道或上网退化 | API 恢复备份中的原选择，验证后退出事务 | 覆盖缓存、重启 mihomo |
| 实时 AUTO 正常但缓存未证实 | 保留 AUTO，停止后续变更并报告 | 删除缓存、将 204 当持久化成功 |
| VPS4 候选校验失败 | 丢弃本次候选，在线文件不变 | 扩大 sed 匹配、按固定行号修改 |
| VPS4 提交后异常 | 事务内原子恢复原文件及完整性记录 | 重启隧道、杀 stale 会话 |
| 暂停标志已存在或所有权变化 | 停止；查明原有事务与标志归属 | 删除他人暂停标志 |
| 午夜后 PROXY 回滚 | 先取证，再单次受控 API 热修 | 清库、加启动任务掩盖原因 |
| 午夜后 VPS4 仍重试 | 留证并定位启动链，标记未通过 | 扩改 VPS2/VPS3、盲杀 SSH |
| 控制通道失联 | 使用既有已核验备用入口；否则报告失联 | 无归属地清理 132xx 会话 |
| watchdog／仓库漂移 | 本轮保持原状，另项治理 | 接 cron、删脚本、替换广州正常部署 |