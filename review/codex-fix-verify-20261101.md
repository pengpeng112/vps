【结论】

**A：文件修复正确，运行态尚未验收；B：补绑与发布方向正确，但不能据现有验收认定容灾链已完整恢复。**

三点需要纠正：

- 节点 **9 是 GIA 的 UDP 入口，与主节点 7 同服务器**，不是独立的 BWG 备用层。
- 节点 **4 是 BWG 直连 VLESS，不是 CF 节点**。
- 已存 R7 配置及运行时快照的顺序是 **7→13→9→4→10→11**，与题述顺序不同。

本次核查依据仓库工作树、已存设备快照及上游源码；**未重新登录现网**。你提供的本轮实测记录予以采信，历史快照不用于否定之后已经发生的修复。

【每项核查】

**1. 修复 A：不重启不会自动更新 procd 参数，判断正确。**

更准确地说：参数在 init 脚本向 procd **注册或更新服务实例定义时**提交，随后保存在 procd 内存中。普通进程退出后的自动 respawn 使用内存参数，不会重新执行 init 文件。服务 restart、相应的服务定义重提交或整机启动才会加载修改；部分 reload 路径也会重提交，不能把 reload 当作无影响检查。[OpenWrt procd.sh](https://raw.githubusercontent.com/openwrt/openwrt/openwrt-21.02/package/system/procd/files/procd.sh)、[procd instance.c](https://raw.githubusercontent.com/openwrt/procd/2cfc26f8456a4d5ba3836c914a742f3d00bad781/service/instance.c)

因此，“午夜重启自然加载”成立，前提是设备确实重启且服务启用；若 self-heal 提前执行了服务 restart，也会提前加载。仓库 SOP 的 P4 与此一致，但 G5 目前只检查文件，**没有验证 procd 内存参数**。[SOP P4](F:/vps/docs/operations/设备升级标准流程-20261031.md:17)、[G5](F:/vps/docs/operations/设备升级标准流程-20261031.md:132)

记录中的“5 次/小时即停”不精确。源码实际是：单次运行不足 3600 秒则累计计数，运行达到阈值才清零；计数 **大于 5** 后停止自动拉起，并非每小时滚动计数。`3600 0 0` 表示无限重试、重试等待零秒。[procd instance_exit](https://raw.githubusercontent.com/openwrt/procd/2cfc26f8456a4d5ba3836c914a742f3d00bad781/service/instance.c#L706)

午夜前的剩余风险：

- cron 正常、无 pause、无未提交事务时，self-heal 通常能在下一次检查或冷却结束后 restart agent，届时加载新参数。
- **300 秒冷却仅约束 self-heal，不约束 procd。**
- 遗留 `uncommitted` 会让 self-heal 持续跳过 agent 重启，因此不能承诺“最多 300 秒恢复”。[self-heal.sh](F:/vps/mgmt/agent/self-heal.sh:48)

可将 A 标记为“落盘完成、运行态待验收”，无需为了审计立即重启。

**2. 修复 B：重复设置 node 7 primary 不会清零其 fp，但有其他副作用。**

`ON CONFLICT` 只更新 `is_primary`、`updated_at`，**不会改已有绑定的 `failover_priority`**。重复设置 7 primary 本身不会破坏节点 9 的 fp0。[nodes.go](F:/vps/mgmt/backend/internal/httpserver/nodes.go:442)

但它并非纯重发布：

- 无论 `apply` 是否为 true，都会写 `devices.current_node_id=7`。
- 下一次成功心跳返回的 `policy.vps_host` 根据该字段生成。
- 若策略指纹变化，agent 会重新处理 QoS、cron、代理模式及访问控制；若字段和指纹均未变化，这部分通常被去重跳过。[nodes.go:458](F:/vps/mgmt/backend/internal/httpserver/nodes.go:458)、[policy.go](F:/vps/mgmt/backend/internal/httpserver/policy.go:111)、[心跳策略入口](F:/vps/mgmt/agent/routermgmt-agent.sh:237)、[策略去重](F:/vps/mgmt/agent/handlers/switch_node.sh:600)

`apply=true` 还会产生 `switch_node` 任务，应用过程中先将 PROXY 指向主节点做出口验收。**当前工作树**随后恢复 AUTO，但这段恢复逻辑属于未提交差异，不能据此证明设备运行中的 handler 已包含它。[发布任务](F:/vps/mgmt/backend/internal/httpserver/configs.go:227)、[切组与恢复](F:/vps/mgmt/agent/handlers/switch_node.sh:313)

所以 R1/R2 还应补验：**PROXY.now=AUTO、AUTO 成员正确、NODE-PICK 顺序正确，以及跨重启持久化**。NP=5、出口正确、health 200 尚未覆盖这些条件。

排序方面：

```text
is_primary DESC → failover_priority ASC → nodes.priority DESC → node_id ASC
```

7 是唯一且启用的 primary，就一定排在 9 前面。9 与 4 都为 fp0 时：

| 条件 | 顺序 |
|---|---|
| priority(9) > priority(4) | 9→4 |
| priority(9) < priority(4) | 4→9 |
| 两者 priority 相等 | 4→9，因 id 4 更小 |

渲染器原样沿用该顺序，不会再次按延迟排序。[排序 SQL](F:/vps/mgmt/backend/internal/httpserver/configs.go:34)、[成员生成](F:/vps/mgmt/backend/internal/clash/template.go:125)

本地 `audit-R1/R2` 是补绑前四成员快照，不能独立证明 v26/v31 的最终顺序；需要修复后的文件或运行时成员列表补证。[R1 旧快照](F:/vps/backups/audit-R1-20261101.yaml:198)

**3. R8 的 4:fp1 与 R7 的 4:fp0：当前可能同序，稳定性不同。**

排除节点 13 后：

- R8：9:fp0→4:fp1，顺序不受二者 `nodes.priority` 调整影响。
- R7：9:fp0 与 4:fp0，顺序依赖 `nodes.priority`；相等时反而是 4 在前。

因此，如果当前 priority(9)>priority(4)，两台实际效果相同；但 R8 更明确地固定了层次，不能把两种绑定称为完全等价。

fallback 使用已记录的健康状态选择前面的可用成员；上述顺序不是“每个请求依次等完所有节点超时”。模板探测间隔为 120 秒，历史 30–60 秒演练结果也不能当作所有故障的硬上限。[模板](F:/vps/mgmt/backend/internal/clash/golden.yaml:101)、[mihomo v1.19.31 fallback 源码](https://raw.githubusercontent.com/MetaCubeX/mihomo/v1.19.31/adapter/outboundgroup/fallback.go)

**4. 节点 13：实验可保留，但目前证据不支持题述顺序和“仅换端口”的描述。**

两个独立存档一致：

```text
7 → 13 → 9 → 4 → 10 → 11
```

即主节点失效、13 被判健康时，R7 会优先使用 13，**提前于 GIA-HY2**。如果后来已调整顺序，需要新的运行时快照证明。[设备 YAML](F:/vps/backups/audit-R7-20261101.yaml:221)、[运行时 NODE-PICK](F:/vps/logs/r7fix-20261101/nodepick.final.json:1)

另外，快照显示：

- 13 与 10 同为 `104.18.32.7`；11 是 `162.159.135.42`。
- 13 与 10/11 的 **SNI、WS Host 不同**，不只是端口不同。因此还不能直接推定它们共用同一回源链。[节点定义](F:/vps/backups/audit-R7-20261101.yaml:91)

443 与 2053 都是 CF 支持的 HTTPS 端口。互补价值主要在端口限制：只允许 443 的网络可能放行 13；仅针对 443 的限制则可能由 2053 补位。这是条件推演，不能保证某个端口必然更稳定。[Cloudflare 端口文档](https://developers.cloudflare.com/fundamentals/reference/network-ports/)

同边缘 IP 被整体阻断时，换端口没有帮助；若最终仍依赖同一源站，源站故障也没有服务器层面的增益。应核实 13 的真实回源、端口映射和完整代理可用性，不能只凭 TCP/TLS/WS 握手判定。[Cloudflare 回源规则](https://developers.cloudflare.com/rules/origin-rules/features/)

【新发现问题】

1. **独立 BWG-HY2 层可能并未覆盖全舰队。**  
   快照中的节点 9 是 `154.36.139.155:34568`，与节点 7 同服务器；独立 BWG-HY2 是节点 8。按你给出的绑定，R4 多绑 8 实际增加了服务器层面的容灾能力，不能仅记为“无害额外成员”。其他设备是否缺 8，应逐台查证。总览“四层防线”的全舰队表述需要重新对拍。[节点 9 实际地址](F:/vps/backups/audit-R5-20261101.yaml:91)、[总览出口基线](F:/vps/docs/current/一周重大变更与现势总览-20261025_1031.md:27)

2. **热加载验收存在明确漏检。**  
   `reload_effective` 只检查名称是否存在，既不比较 NODE-PICK 成员顺序，也不比较同名节点的地址、端口或协议。旧运行态名称相同时，即使新顺序没有加载，也可能通过，从而不进入 restart 回退。[switch_node.sh](F:/vps/mgmt/agent/handlers/switch_node.sh:60)

3. **`_rb` 会污染后续任务。**  
   它没有声明为局部变量，也没有在任务入口清零。一次加载失败设为 1 后，同一 agent 下一次任务即使加载成功，也可能继续进入回滚分支。已完成同 shell 最小复现；尚未证明现网触发过。[任务入口](F:/vps/mgmt/agent/handlers/switch_node.sh:212)、[失败标记使用](F:/vps/mgmt/agent/handlers/switch_node.sh:296)

4. **批量接口存在失败后仍提交部分变更的路径；展示顺序也与渲染不同。**  
   `publishAndEnqueue` 返回应用层错误时，事务回调仍返回 nil，前面的绑定及 current_node_id 修改可能提交；后者更新的错误也未检查。设备详情排序则漏了 `n.priority DESC`，所以 UI 绑定列表不能代表真实切换顺序。[批量事务](F:/vps/mgmt/backend/internal/httpserver/nodes.go:457)、[详情排序](F:/vps/mgmt/backend/internal/httpserver/devices.go:411)

5. **孤儿事务会妨碍自愈；零秒 respawn 也有代价。**  
   `uncommitted` 检查无过期机制，而恢复函数只在 agent 启动时执行。agent 和事务看门狗都退出时，可能形成“等待启动恢复、却禁止启动”的状态。此外，`3600 0 0` 在持续启动失败时会快速循环，300 秒冷却不会限制它。是否保留几秒间隔应通过修订基线统一决定。[事务恢复](F:/vps/mgmt/agent/handlers/switch_node.sh:191)、[self-heal 保护](F:/vps/mgmt/agent/self-heal.sh:62)

6. **持久化、LF 和记录仍有收尾项。**  
   R8 cache 回滚风险、R7 午夜验收仍未关闭；当前文件是 LF，但 Git 配置为 `core.autocrlf=true`，golden.yaml 未设置 `eol=lf` 属性。A/B 已进入当前状态，却未找到配套 plans 记录，总览版本表仍为 R1 v25/R2 v30。[当前状态遗留](F:/vps/docs/current/当前状态.md:16)、[总览版本表](F:/vps/docs/current/一周重大变更与现势总览-20261025_1031.md:44)、[记录义务](F:/vps/docs/current/一周重大变更与现势总览-20261025_1031.md:179)

7. **执行记录存在明文凭证。**  
   应脱敏该位置，并按实际传播范围处理；本次未核查 Git 历史，不推定已提交入库。[具体位置](F:/vps/docs/plans/R7修复执行记录-Codex指挥-20261101.md:47)

**标准巡检片段，对应第 6 问**

关键原则：**采集失败记 UNKNOWN，单次失败不推导为设备故障。** 通过既有双层 Paramiko `direct-tcpip`，将 LF 字节送入设备 `sh -s`；复用广州连接，设备串行检查，失败后退避。下面片段已做本地 shell 语法检查，未在设备执行。

设备侧：

```sh
#!/bin/sh
set -u
S=/etc/routermgmt/state

date -u
cat /proc/sys/kernel/random/boot_id
for t in "$S"/txn/*/uncommitted; do
    [ ! -f "$t" ] || {
        echo "UNKNOWN: apply in progress"; exit 2;
    }
done
[ ! -e /tmp/selfheal.pause ] ||
    echo "PENDING: selfheal.pause exists"
cat "$S/accepted_version" 2>/dev/null ||
    echo "UNKNOWN: accepted_version"
md5sum /etc/routermgmt/self-heal.sh
grep -E 'self-heal[.]sh|routermgmt-reboot' /etc/crontabs/root

# 分别检查磁盘参数、procd 内存参数；不输出整个实例的 env。
for svc in routermgmt mgmt-tunnel; do
    printf '\nSERVICE=%s\n' "$svc"
    grep -n '^[[:space:]]*procd_set_param[[:space:]]*respawn' \
        "/etc/init.d/$svc"
    j=$(ubus call service list "{\"name\":\"$svc\"}" 2>/dev/null) || {
        echo "UNKNOWN: ubus unavailable"; continue;
    }
    for k in running pid respawn; do
        printf '%s:\n' "$k"
        v=$(jsonfilter -s "$j" \
            -e "@['$svc'].instances[*].$k" 2>/dev/null)
        [ -n "$v" ] && printf '%s\n' "$v" ||
            echo "UNKNOWN: field absent"
    done
done

# 使用运行中核心的实际 -d/-f，避免 geodata 目录误判。
pids=$(pidof mihomo 2>/dev/null || pidof clash 2>/dev/null || true)
set -- $pids
[ "$#" -eq 1 ] || {
    echo "UNKNOWN: core count is not one"; exit 2;
}
p=$1
args=$(tr '\000' '\n' < "/proc/$p/cmdline")
D=$(printf '%s\n' "$args" | awk '$0=="-d"{getline; print; exit}')
F=$(printf '%s\n' "$args" | awk '$0=="-f"{getline; print; exit}')
case "$D" in
    /*) ;;
    *) echo "UNKNOWN: verify core home"; exit 2 ;;
esac
[ -n "$F" ] || F="$D/config.yaml"
case "$F" in
    /*) ;;
    *) echo "UNKNOWN: relative config path"; exit 2 ;;
esac
[ -r "$F" ] && [ -s "$F" ] || {
    echo "UNKNOWN: config unreadable"; exit 2;
}
printf 'core_pid=%s home=%s config=%s\n' "$p" "$D" "$F"
before=$(sha256sum "$F" | awk '{print $1}')
printf 'disk_sha256=%s\n' "$before"

# 字节计数，完全避开 ash 的 ANSI-C quoting 差异。
od -An -v -tu1 "$F" |
    awk '{for(i=1;i<=NF;i++)if($i==13)n++}
         END{print "CR_bytes=" n+0}'

# 只输出成员名，不输出配置中的凭据。
awk '
/^  - name: "NODE-PICK"/ {p=1;next}
p && /^  - name:/ {exit}
p && /^      - / {print "disk_member=" substr($0,9)}
' "$F"

if { [ -s "$D/Country.mmdb" ] || [ -s "$D/geoip.metadb" ]; } &&
   { [ -s "$D/GeoSite.dat" ] || [ -s "$D/geosite.dat" ]; } &&
   command -v timeout >/dev/null 2>&1; then
    timeout 25 "/proc/$p/exe" -t -d "$D" -f "$F" >/dev/null 2>&1
    printf 'validate_rc=%s (0=PASS; other=investigate)\n' "$?"
else
    echo "UNKNOWN: verify geodata/timeout before validation"
fi

for g in PROXY AUTO NODE-PICK US-MEDIA; do
    printf '\nGROUP=%s\n' "$g"
    j=$(curl --noproxy '*' -fsS -m 5 \
        "http://127.0.0.1:9090/proxies/$g") || {
        echo "UNKNOWN: API request failed"; continue;
    }
    jsonfilter -s "$j" -e '$.name' -e '$.type' \
        -e '$.now' -e '$.fixed' -e '$.all[*]'
done

h=$(curl --noproxy '*' -ksS -m 8 -o /dev/null \
    -w '%{http_code}' https://127.0.0.1:18443/api/v1/health)
printf 'mgmt_rc=%s http=%s\n' "$?" "$h"

for i in 1 2 3; do
    t=$(curl --noproxy '' -fsS -m 10 \
        -x http://127.0.0.1:7890 \
        https://www.cloudflare.com/cdn-cgi/trace)
    rc=$?
    ip=$(printf '%s\n' "$t" | awk -F= '$1=="ip"{print $2;exit}')
    printf 'egress_sample=%s rc=%s ip=%s\n' \
        "$i" "$rc" "${ip:-UNKNOWN}"
    sleep 2
done
h=$(curl --noproxy '' -sS -m 10 -x http://127.0.0.1:7890 \
    -o /dev/null -w '%{http_code}' \
    https://www.gstatic.com/generate_204)
printf 'overseas_rc=%s http=%s (expected 204)\n' "$?" "$h"
nslookup www.baidu.com 127.0.0.1 >/dev/null 2>&1
printf 'local_dns_rc=%s\n' "$?"

after=$(sha256sum "$F" | awk '{print $1}')
[ "$before" = "$after" ] ||
    echo "UNKNOWN: config changed during audit"
```

广州侧，在同一已建立的管理会话内执行：

```sh
date -u
systemctl is-active nginx gz-api
ss -ltnp | awk 'NR==1 || $4 ~ /:(8443|8080|132[0-9][0-9])$/'

sshd -T |
    awk '/^(maxstartups|persourcemaxstartups|persourcepenalties|maxsessions|logingracetime) /'
journalctl -u ssh -u sshd --since '-15min' --no-pager |
    grep -Ei 'MaxStartups|drop connection|penalt|refus|throttl' || true

sudo -u postgres psql -X -v ON_ERROR_STOP=1 -d routermgmt <<'SQL'
BEGIN READ ONLY;
SELECT clock_timestamp() AS observed_at;

SELECT d.name,d.id,
       round(extract(epoch FROM (now()-d.last_heartbeat_at))) AS hb_age_s,
       d.current_node_id,d.current_config_version,d.desired_config_version,
       d.highest_accepted_version,c.sha256 AS desired_sha256
FROM devices d LEFT JOIN configs c
  ON c.device_id=d.id AND c.version=d.desired_config_version
WHERE d.lifecycle_status='approved'
ORDER BY d.name;

SELECT d.name,a.node_id,n.name,n.host,n.port,
       a.is_primary,a.failover_priority,n.priority
FROM node_assignments a
JOIN devices d ON d.id=a.device_id
JOIN nodes n ON n.id=a.node_id
WHERE d.lifecycle_status='approved' AND n.enabled=true
ORDER BY d.name,a.is_primary DESC,a.failover_priority,n.priority DESC,n.id;
ROLLBACK;
SQL
```

判读时再遵守四条：

- 文件 respawn 与 procd 中的 `threshold/timeout/retry` 分开签收。
- DB 顺序、设备 YAML 顺序、运行时 `.all` 三处逐项比较，不能只数成员。
- 心跳至少观察两个周期；新 SSH 失败但心跳推进时，先查连接限制，不能直接判离线。`MaxStartups` 限制的是未认证连接，并非简单的每秒连接次数。[OpenSSH 文档](https://man.openbsd.org/sshd_config#MaxStartups)
- 将 `desired_sha256` 与设备 SHA 对拍；不一致时先核对代理模式覆盖，因为策略代码会改写 YAML 的 `mode:`，不能直接归因为渲染损坏。AUTO 的缓存持久化仍需既定快照流程及午夜复验。[模式覆盖代码](F:/vps/mgmt/agent/handlers/switch_node.sh:570)
