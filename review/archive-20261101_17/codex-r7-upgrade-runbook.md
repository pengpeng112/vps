已读指定三份文件，并核对了绑定、发布、任务结果和 agent 调用链。**R7 已恢复管理面，跳过 §六应急修复，走绑定对齐 → preview → 单次 apply → G1–G6。**

但有一个必须先解决的红线冲突：当前源码中，`batch(apply:false)` 改 primary 也会更新 `current_node_id`，随后心跳策略可能重建 VPS_QOS iptables；配置热加载失败时，handler 还会自动重启 mihomo，其 init 会重装规则。[绑定入口](F:/vps/mgmt/backend/internal/httpserver/nodes.go:444)、[策略调用](F:/vps/mgmt/agent/handlers/switch_node.sh:598)、[热加载回退](F:/vps/mgmt/agent/handlers/switch_node.sh:72)。

**因此，未澄清前按“iptables 严格零触碰”执行：第 1–5 步可以做，第 6 步起不得执行。** 后续报文全部列出，便于闸门放行后继续；不能以“没有手工执行 iptables”代替零触碰。

以下命令均在**广州服务器的 root Bash** 执行，逐块运行、逐步回报。任何失败立即停止，不整份粘贴执行。

1. **确认操作窗口和广州后端**

   前提：执行者已确认其他会话暂不操作广州后端。无法确认则停止。

   ```bash
   set +x
   set -o pipefail
   umask 077

   DEV=rtr-37cf72
   BASE=http://127.0.0.1:8080/api/v1
   RUN="R7-$(cat /proc/sys/kernel/random/uuid)"
   BK="/root/r7-upgrade-private/$RUN"
   mkdir -p "$BK"

   date -u
   hostname
   systemctl is-active nginx gz-api
   systemctl is-active gz-relay
   curl -fsS -m 5 "$BASE/health"
   curl -fsk -m 5 https://127.0.0.1:8443/api/v1/health
   ss -ltnp | grep -E ':(8080|8443|13229)\b'

   systemctl show gz-api -p MainPID -p ActiveEnterTimestamp \
     > "$BK/gz-api.before.txt"

   db() {
     runuser -u postgres -- psql -X -v ON_ERROR_STOP=1 -d routermgmt "$@"
   }

   db -c "SELECT current_database(),inet_server_addr(),inet_server_port();"
   ```

   **预期／验证：**

   - nginx、gz-api 均 `active`；gz-relay 为 `inactive`，其退出码非零属预期。
   - HTTP 8080、TLS 8443 health 均成功；8080 只监听回环。
   - 广州 13229 LISTEN。
   - 数据库为广州本地 `routermgmt`。此连接方式若不匹配现场，停止回报，不创建数据库或修改认证。

   **失败动作：**只回报服务状态、监听和错误信息；不重启服务、不改安全组、不切 DMIT。

2. **登录广州 API，取得完整 JWT**

   可复用你的现成登录脚本，将 JWT 放入当前 Bash 的 `TOKEN`。完整协议如下；没有现成环境变量时可执行：

   ```bash
   read -r -s -p 'admin 密码：' ADMIN_PW
   printf '\n'

   LOGIN=$(
     printf '%s' "$ADMIN_PW" |
     jq -Rs '{username:"admin",password:.}' |
     curl -fsS -m 10 -H 'Content-Type: application/json' \
       --data-binary @- "$BASE/auth/login"
   )
   unset ADMIN_PW

   TICKET=$(jq -er '.ticket' <<< "$LOGIN")
   jq -e '.need_totp == true' <<< "$LOGIN" >/dev/null

   read -r -s -p '当前未使用过的六位 TOTP：' TOTP_CODE
   printf '\n'

   AUTH=$(
     printf '%s\n%s\n' "$TICKET" "$TOTP_CODE" |
     jq -Rn '{ticket:input,code:input}' |
     curl -fsS -m 10 -H 'Content-Type: application/json' \
       --data-binary @- "$BASE/auth/totp"
   )
   TOKEN=$(jq -er '.token | select(length>0)' <<< "$AUTH")
   unset LOGIN TICKET TOTP_CODE AUTH
   printf 'TOKEN_OK\n'

   api() {
     local method="$1" path="$2"
     shift 2
     curl -fsS -m 15 -X "$method" \
       -H "Authorization: Bearer $TOKEN" \
       -H 'Content-Type: application/json' \
       "$@" "$BASE$path"
   }

   api GET "/devices/$DEV" > "$BK/device.before.json"
   jq '.device | {
     id,name,lifecycle_status,service_active,
     current_node_id,desired_config_version,current_config_version,
     highest_accepted_version,last_heartbeat_at,proxy_mode,access_mode,reboot_cron
   }' "$BK/device.before.json"
   ```

   **预期／验证：**`TOKEN_OK`；设备身份正确、`approved`、`service_active:true`，版本为 `5/5/5`。

   **失败动作：**401 时检查广州时间和是否复用了同一个 TOTP 时间步；等待新验证码后重新走登录两阶段。持续失败则停。**不得重置 TOTP、换 MASTER_KEY/JWT 或参考旧 `.env` 改密钥。**

3. **实登 R7，核对目标态并保存保护基线**

   ```bash
   read -r -s -p 'R7 SSH 密码：' SSHPASS
   printf '\n'
   export SSHPASS

   r7() {
     sshpass -e ssh -T \
       -o StrictHostKeyChecking=yes \
       -o ConnectTimeout=5 \
       -p 13229 root@127.0.0.1 "$@"
   }

   r7 'grep -E "^(DEVICE_ID|MGMT_URL)=" /etc/routermgmt/agent.conf
   date -u
   cat /proc/uptime
   grep -E "^VPS(_HOST|_PORT)=" /etc/routermgmt/tunnel.conf
   grep "^#VPS4" /etc/routermgmt/tunnel.conf
   grep respawn /etc/init.d/routermgmt /etc/init.d/mgmt-tunnel
   head -2 /etc/routermgmt/self-heal.sh
   md5sum /etc/routermgmt/self-heal.sh
   crontab -l | grep -E "self-heal|routermgmt-reboot|wifi-restore"
   uci show firewall | grep mihomo-ipt
   uci show wireless | grep "\.disabled="
   ip -o link show | grep -E ": (ra|rax|rai|rae)[0-9]+:"
   pidof mihomo
   ps w | grep "[r]outermgmt-agent"
   curl -fsk -m 5 -o /dev/null -w "%{http_code}\n" https://127.0.0.1:18443/api/v1/health
   curl -fsS -m 5 http://127.0.0.1:9090/version'
   ```

   再保存基线：

   ```bash
   r7 'cat /proc/sys/kernel/random/boot_id' > "$BK/boot.before.txt"

   r7 'md5sum /etc/routermgmt/tunnel.conf /etc/config/network /etc/config/wireless /etc/init.d/routermgmt /etc/init.d/mgmt-tunnel /etc/routermgmt/self-heal.sh' \
     > "$BK/protected.before.md5"

   r7 'ubus call service list "{\"name\":\"mgmt-tunnel\"}"' \
     > "$BK/tunnel.before.json"

   r7 'iptables-save' |
     sed -E 's/\[[0-9]+:[0-9]+\]/[0:0]/g' \
     > "$BK/iptables.before.rules"

   r7 'ps w | grep "[/]usr/bin/mihomo"; stat /etc/mihomo/config.yaml; md5sum /etc/mihomo/config.yaml; cat /etc/routermgmt/state/accepted_version; grep -cF "154.36.137.54" /etc/mihomo/config.yaml; grep -cF "154.36.137.54" /etc/routermgmt/tunnel.conf' \
     > "$BK/config.before.txt"

   r7 'ls /etc/routermgmt/state/txn/*/uncommitted 2>/dev/null'
   ```

   **预期／验证：**

   - `DEVICE_ID=rtr-37cf72`；MGMT_URL 主口为 `https://127.0.0.1:18443`。
   - 隧道主端点广州 `134.175.151.213:28722`；DO 端点已注释。
   - routermgmt、mgmt-tunnel 两处 respawn 均 `3600 0 0`。
   - self-heal 为 v3.1，完整 MD5：`329c6081caaa05595c13ff380687187b`；每分钟 cron 在。
   - mihomo 一个进程，agent 至少一个；health 200，9090 正常。
   - 活动配置确为 `/etc/mihomo/config.yaml`；本地 accepted=5。
   - **配置中的旧 GIA IP 此时允许存在；tunnel.conf 中必须为零。**
   - 没有 `uncommitted`；最后一条因无文件返回非零属于预期。
   - 无线保持关闭；若发现 mesh 配置遗留，仅记录，不照搬 R8 的无线修改。

   **失败动作：**停止回报。尤其不能为了补 T1–T7 而运行 `fix-cmds.txt`：它会改隧道并重启服务，超出本轮红线。SSH 主机密钥不匹配也必须停止，不删除 known_hosts 绕过。

4. **备份配置，核对广州库和 R8 模板**

   ```bash
   sshpass -e scp -O -p \
     -o StrictHostKeyChecking=yes -P 13229 \
     root@127.0.0.1:/etc/mihomo/config.yaml "$BK/config-v5.yaml"

   chmod 600 "$BK/config-v5.yaml"

   r7 'md5sum /etc/mihomo/config.yaml'
   md5sum "$BK/config-v5.yaml"

   db -c "
   SELECT id,lifecycle_status,current_node_id,
          desired_config_version,current_config_version,highest_accepted_version,
          round(extract(epoch FROM now()-last_heartbeat_at)) AS hb_age_s,
          (SELECT max(version) FROM configs WHERE device_id=devices.id) AS max_version
   FROM devices WHERE id='rtr-37cf72';

   SELECT a.device_id,a.node_id,n.name,n.host,n.enabled,
          a.is_primary,a.failover_priority,n.priority
   FROM node_assignments a JOIN nodes n ON n.id=a.node_id
   WHERE a.device_id IN ('rtr-37cf72','rtr-1f6f0f')
   ORDER BY a.device_id,a.is_primary DESC,a.failover_priority,n.priority DESC,n.id;

   SELECT id,name,proto,host,port,enabled
   FROM nodes WHERE id IN (4,7,8,9,10,11) ORDER BY id;

   SELECT id,type,status,config_version,deadline
   FROM tasks
   WHERE device_id='rtr-37cf72' AND status IN ('pending','running');"

   db -c "COPY (
     SELECT device_id,node_id,is_primary,failover_priority,created_at,updated_at
     FROM node_assignments WHERE device_id='rtr-37cf72' ORDER BY node_id
   ) TO STDOUT WITH CSV HEADER" > "$BK/bindings.before.csv"
   ```

   **预期／验证：**

   - 两端配置 MD5 完全相同。
   - 广州 max=5，desired/current/highest=5；心跳年龄 ≤90 秒。
   - R7 初始仅绑定 `4主、5、3、2`；无 pending/running 任务。
   - R8 实际绑定符合 `7主、9/4 fp0、10 fp2、11 fp3`。
   - 目标节点均 enabled；7/9 指当前 GIA，10/11 是 CF。
   - 本轮**不额外绑定 id8**。因此不能声称 R7 已具有 BWG-HY2 层；以实际渲染链为准。

   **失败动作：**任一状态与已知现场冲突即停。不要自动垫版本、迁库、补节点或替换后端。备份文件含凭据，留在 `$BK`，不贴原文、不入仓库。

5. **红线闸门：检查自动副作用**

   ```bash
   r7 'grep -n -A 24 "^apply_vps_qos()" /etc/routermgmt/handlers/switch_node.sh
   grep -n -A 35 "^apply_policy_fields()" /etc/routermgmt/handlers/switch_node.sh
   grep -n -A 30 "^oc_reload()" /etc/routermgmt/handlers/switch_node.sh
   grep -n -A 12 "^reapply_runtime_modes()" /etc/routermgmt/handlers/switch_node.sh
   grep -n "apply_policy_from_hb" /etc/routermgmt/routermgmt-agent.sh
   grep -nE "iptables|mihomo-ipt|reload_service" /etc/init.d/mihomo
   command -v nft
   cat /etc/routermgmt/state/policy.fp'
   ```

   **验证／通过条件：**

   - 严格零触碰口径下，必须确认后续调用链不会写防火墙；**仅证明热加载通常成功不够**。
   - 设备磁盘上的 handler 也不必然等于 agent 已加载的函数，不能据此贸然放行。
   - `/tmp/selfheal.pause` 只暂停 self-heal，**不会暂停 agent 心跳策略**。

   **失败动作：**若存在上述自动改规则路径，或无法证明不会触碰，停在这里回报。**未得到明确约束澄清前，第 6–12 步保持封存。**

6. **放行后，进入配置维护窗口**

   ```bash
   r7 'test ! -e /tmp/selfheal.pause'
   r7 'touch /tmp/selfheal.pause; test -f /tmp/selfheal.pause'
   ```

   **预期／验证：**原先没有他人遗留 pause，现在文件存在；第 3、4 步确认无活动事务和其他任务。

   **失败动作：**原本已有 pause 时停，不覆盖他人维护现场。后续失败时按第 13 步处理，不能遗忘 pause。

7. **逐条添加目标绑定，全程 `apply:false`**

   ```bash
   bind_r7() {
     local node="$1" primary="$2" response
     response=$(api POST /assignments/batch \
       --data-binary "{\"device_ids\":[\"$DEV\"],\"node_id\":$node,\"is_primary\":$primary,\"apply\":false}") \
       || return 1

     printf '%s\n' "$response" |
       jq -e '.apply == false and
              (.items|length)==1 and
              .items[0].device_id=="rtr-37cf72" and
              .items[0].ok==true and
              (.items[0].task_id // "")==""'
   }
   ```

   以下**每次只执行一行**，看到 `true` 再继续：

   ```bash
   bind_r7 7 true
   ```

   ```bash
   bind_r7 9 false
   ```

   ```bash
   bind_r7 10 false
   ```

   ```bash
   bind_r7 11 false
   ```

   **预期／验证：**每条返回 `true`，没有 task_id；设备配置仍 v5。7 成为唯一 primary，4 自动降为 secondary。

   **失败动作：**立即停，不继续追加节点、不改成 `apply:true` 重试。HTTP 200 也必须检查 `items[0].ok`。**10/11 永不设 primary。**

8. **仅对 R7，事务删除旧绑定并规范优先级**

   当前 API 不支持解绑或写 `failover_priority`；发送这些额外字段不会实现目标。

   ```bash
   db <<'SQL'
   BEGIN;

   SELECT id FROM devices WHERE id='rtr-37cf72' FOR UPDATE;

   DELETE FROM node_assignments
   WHERE device_id='rtr-37cf72' AND node_id IN (5,3,2);

   UPDATE node_assignments
   SET failover_priority=CASE node_id WHEN 10 THEN 2 WHEN 11 THEN 3 ELSE 0 END,
       updated_at=now()
   WHERE device_id='rtr-37cf72' AND node_id IN (7,9,4,10,11);

   DO $$
   BEGIN
     IF (SELECT count(*) FROM node_assignments
         WHERE device_id='rtr-37cf72') <> 5
        OR (SELECT count(*) FROM node_assignments
            WHERE device_id='rtr-37cf72' AND is_primary) <> 1
        OR NOT EXISTS (
          SELECT 1 FROM node_assignments
          WHERE device_id='rtr-37cf72' AND node_id=7 AND is_primary
        )
        OR EXISTS (
          SELECT 1 FROM node_assignments
          WHERE device_id='rtr-37cf72'
            AND (node_id NOT IN (7,9,4,10,11)
                 OR failover_priority <>
                    CASE node_id WHEN 10 THEN 2 WHEN 11 THEN 3 ELSE 0 END)
        )
     THEN
       RAISE EXCEPTION 'R7 binding validation failed';
     END IF;
   END $$;

   COMMIT;
   SQL

   db -c "
   SELECT a.node_id,n.name,a.is_primary,a.failover_priority
   FROM node_assignments a JOIN nodes n ON n.id=a.node_id
   WHERE a.device_id='rtr-37cf72'
   ORDER BY a.is_primary DESC,a.failover_priority,n.priority DESC,n.id;

   SELECT desired_config_version,current_config_version,highest_accepted_version
   FROM devices WHERE id='rtr-37cf72';"
   ```

   **预期／验证：**首次 `DELETE 3`、`UPDATE 5`、`COMMIT`；最终精确五条：7 主 fp0，9/4 次 fp0，10 次 fp2，11 次 fp3；版本仍 `5/5/5`。

   **失败动作：**SQL 出错即事务回滚，停止。此前 API 已完成的绑定不会随此事务回滚；不要误认为全部恢复。设备详情 API 不返回 fp，**以 SQL 为验收依据**。

9. **全部绑定完成后，重新 preview**

   ```bash
   api POST "/devices/$DEV/configs/preview" \
     --data-binary '{}' > "$BK/preview.json"

   jq '{revision,current_version,next_version,sha256}' "$BK/preview.json"

   jq -r '.masked_yaml' "$BK/preview.json" > "$BK/preview.yaml"

   jq -r '.diff[] | "\(.op) \(.line)"' "$BK/preview.json" |
     sed -E 's#(^[+-] [[:space:]]*url:).*#\1 <REDACTED>#' \
     > "$BK/preview.diff.redacted.txt"

   cat "$BK/preview.diff.redacted.txt"

   jq -r '.masked_yaml' "$BK/preview.json" |
     grep -E 'name:|type: (fallback|url-test)|lazy:|empty-fallback:|IP-CIDR,|istarshine|^[[:space:]]+- (AUTO|NODE-PICK|AIRPORT|DIRECT|airport-sub)$'

   python3 - "$BK/preview.json" <<'PY'
   import json, sys
   p = json.load(open(sys.argv[1]))
   y = p["masked_yaml"]
   assert p["revision"] and p["current_version"] == 5
   assert p["next_version"] == 6
   assert "\r" not in y, "CRLF"
   assert "154.36.137.54" not in y, "旧 GIA IP 残留"
   for s in (
       "IP-CIDR,154.36.139.155/32,DIRECT,no-resolve",
       'name: "US-MEDIA"',
       'name: "AIRPORT"',
       "proxy-providers:",
       "airport-sub:",
       "proxy: NODE-PICK",
       "lazy: false",
       "empty-fallback: REJECT",
       "DOMAIN-SUFFIX,istarshine.com,DIRECT",
   ):
       assert s in y, "缺少 " + s
   print("PREVIEW_BASIC_OK")
   PY

   REV=$(jq -er '.revision' "$BK/preview.json")
   SHA=$(jq -er '.sha256' "$BK/preview.json")
   ```

   **预期／验证：**

   - `current=5、next=6、PREVIEW_BASIC_OK`。
   - 人工核对节点集合恰为 7/9/4/10/11 对应名称，旧 5/3/2 已消失。
   - NODE-PICK：7 首位，9/4 按实际排序在中间，CF1/CF2 在链尾。
   - AUTO 为 `NODE-PICK → AIRPORT → DIRECT`；US-MEDIA 至少保留 NODE-PICK、AIRPORT。
   - 旧 GIA IP 的**直连规则也必须消失**，不只检查 `server:`。
   - 新 GIA 直连规则重复两行属已知无害问题，本轮不修模板。
   - preview 不生成任务，不推进版本。

   **失败动作：**停止，不 apply。若 preview 后又改绑定，必须重新 preview，不能沿用 `REV`。

   **凭据注意：**当前 `MaskYAML` 未掩码机场订阅 URL；不得直接贴 `masked_yaml` 或完整 diff。上述打印版本已遮蔽 `url:`。

10. **只 apply 一次，按返回 task_id 轮询**

   ```bash
   APPLY_KEY="$RUN-apply"

   api POST "/devices/$DEV/configs/apply" \
     -H "Idempotency-Key: $APPLY_KEY" \
     --data-binary "$(jq -cn --arg revision "$REV" '{revision:$revision}')" \
     > "$BK/apply.json"

   jq '{ok,task_id,version}' "$BK/apply.json"

   TID=$(jq -er '.task_id' "$BK/apply.json")
   VER=$(jq -er '.version' "$BK/apply.json")
   test "$VER" -gt 5
   ```

   预期 `ok:true`、version=6、唯一 task_id。**无需再发一次 `ops switch_node`；apply 已生成该任务。**

   定义轮询：

   ```bash
   wait_task() {
     local tid="$1" i one status

     for i in $(seq 1 36); do
       api GET "/tasks?device_id=$DEV&page_size=100" \
         > "$BK/tasks.latest.json" || return 1

       one=$(jq -ec --arg id "$tid" \
         '.items[] | select(.id==$id)' "$BK/tasks.latest.json") || {
           printf 'STOP: 指定任务未找到\n'
           return 1
         }

       printf '%s\n' "$one" > "$BK/$tid.json"
       printf '%s\n' "$one" | jq '{
         id,status,config_version,
         result:((.result // {}) | {
           exit_ip,version,proxy_group,group_now,skipped,error,rollback_failed
         })
       }'

       status=$(jq -r '.status' <<< "$one")
       case "$status" in
         ok)
           jq -e '(.result.skipped // false)==false' <<< "$one" >/dev/null
           return $?
           ;;
         failed|rolled_back|expired|cancelled|unknown)
           return 1
           ;;
         pending|running)
           sleep 5
           ;;
         *)
           return 1
           ;;
       esac
     done

     printf 'STOP: 180 秒仍未收敛\n'
     return 1
   }

   wait_task "$TID"
   ```

   随后核对三方版本：

   ```bash
   api GET "/devices/$DEV" > "$BK/device.after-apply.json"

   jq '.device | {
     desired_config_version,current_config_version,highest_accepted_version,
     last_heartbeat_at,current_exit_ip
   }' "$BK/device.after-apply.json"

   r7 'cat /etc/routermgmt/state/accepted_version'
   ```

   **通过条件：**指定任务 `status=ok`、没有 `skipped:true`；task.config_version=返回版本；desired=current=highest=设备 accepted=`VER`。

   **失败动作：**

   - 409：停止，检查绑定／节点是否被并行修改，重新 preview；不盲换幂等键。
   - 请求超时：先查任务和设备，不能立即重新发布。
   - pending/running 不是成功；expired/unknown 不能当失败已回退。
   - `ok + skipped` 也停止，不能靠版本相等放行。
   - 其余失败进入第 13 步。

11. **核对运行态，必要时恢复 PROXY→AUTO**

   ```bash
   for group in PROXY AUTO NODE-PICK US-MEDIA AIRPORT; do
     r7 "curl -fsS -m 5 http://127.0.0.1:9090/proxies/$group" |
       jq '{name,type,now,all,alive}'
   done
   ```

   **预期／验证：**

   - PROXY.now=`AUTO`。
   - AUTO.now=`NODE-PICK`，NODE-PICK 当前选 GIA 主节点。
   - AIRPORT 有实际成员，当前选择不是 REJECT/COMPATIBLE。
   - US-MEDIA 非空，包含 NODE-PICK 与 AIRPORT。

   **仅当版本、文件和出口已验过，而 PROXY 仍被旧 handler 固定在主节点时**，执行：

   ```bash
   api POST "/devices/$DEV/ops" \
     -H "Idempotency-Key: $RUN-auto" \
     --data-binary '{"type":"set_proxy_state","params":{"enabled":true}}' \
     > "$BK/auto.json"

   AUTO_TID=$(jq -er '.task_id' "$BK/auto.json")
   wait_task "$AUTO_TID"

   r7 'curl -fsS -m 5 http://127.0.0.1:9090/proxies/PROXY' |
     jq '{name,type,now}'
   ```

   **预期：**任务 ok、`group_now=AUTO`、运行态 now=AUTO；配置版本不变。

   **失败动作：**停止，不再切节点、不重启 agent。handler 文件是否包含新逻辑，不能代替运行态检查。

12. **终验 G1–G6，六关全部通过才宣布完成**

   | 关卡 | 精确检查 | 通过标准 |
   |---|---|---|
   | G1 | 执行下方心跳 SQL，间隔 60 秒再执行一次 | 两次年龄均 ≤90 秒，第二次心跳时间确实推进 |
   | G2 | 新建 SSH 会话执行身份与 health 检查 | 13229 实登正确设备，health 200 |
   | G3 | 进程、boot_id、隧道实例检查 | mihomo=1、agent≥1；boot_id 未变；隧道实例及 PID 保持基线 |
   | G4 | 两次代理出口、gstatic、国内 DNS | 出口均为 GIA `154.36.139.155`；gstatic 204；国内 DNS 成功 |
   | G5 | 任务、三方版本、文件 SHA/mtime、旧 IP、respawn | 全部一致；旧 IP 两文件均零；双 respawn 正确 |
   | G6 | v3.1 MD5、cron、无活动事务、清 pause | 全部符合；pause 最终不存在 |

   **G1：**

   ```bash
   db -c "SELECT now(),last_heartbeat_at,
     round(extract(epoch FROM now()-last_heartbeat_at)) AS age_s
     FROM devices WHERE id='rtr-37cf72';"

   sleep 60

   db -c "SELECT now(),last_heartbeat_at,
     round(extract(epoch FROM now()-last_heartbeat_at)) AS age_s
     FROM devices WHERE id='rtr-37cf72';"
   ```

   **G2–G4：**

   ```bash
   r7 'grep "^DEVICE_ID=" /etc/routermgmt/agent.conf
   curl -fsk -m 5 -o /dev/null -w "%{http_code}\n" https://127.0.0.1:18443/api/v1/health
   pidof mihomo
   ps w | grep "[r]outermgmt-agent"'

   r7 'cat /proc/sys/kernel/random/boot_id' > "$BK/boot.after.txt"
   cmp "$BK/boot.before.txt" "$BK/boot.after.txt"

   r7 'ubus call service list "{\"name\":\"mgmt-tunnel\"}"' \
     > "$BK/tunnel.after.json"

   for phase in before after; do
     jq -S '."mgmt-tunnel".instances |
       with_entries(.value |= {pid,command})' \
       "$BK/tunnel.$phase.json" > "$BK/tunnel.$phase.summary.json"
   done
   diff -u "$BK/tunnel.before.summary.json" "$BK/tunnel.after.summary.json"

   r7 'curl -fsS -m 15 -x http://127.0.0.1:7890 https://api.ipify.org'
   r7 'curl -fsS -m 15 -x http://127.0.0.1:7890 https://api.ipify.org'
   r7 'curl -fsS -m 15 -x http://127.0.0.1:7890 -o /dev/null -w "%{http_code}\n" https://www.gstatic.com/generate_204'
   r7 'nslookup www.baidu.com 223.5.5.5'
   ```

   **G5：**

   ```bash
   r7 'stat /etc/mihomo/config.yaml
   sha256sum /etc/mihomo/config.yaml
   grep -cF "154.36.137.54" /etc/mihomo/config.yaml
   grep -cF "154.36.137.54" /etc/routermgmt/tunnel.conf
   grep respawn /etc/init.d/routermgmt /etc/init.d/mgmt-tunnel'

   FILE_SHA=$(r7 'sha256sum /etc/mihomo/config.yaml' | cut -d ' ' -f 1)
   test "$FILE_SHA" = "$SHA"

   r7 'md5sum /etc/routermgmt/tunnel.conf /etc/config/network /etc/config/wireless /etc/init.d/routermgmt /etc/init.d/mgmt-tunnel /etc/routermgmt/self-heal.sh' \
     > "$BK/protected.after.md5"
   diff -u "$BK/protected.before.md5" "$BK/protected.after.md5"

   r7 'iptables-save' |
     sed -E 's/\[[0-9]+:[0-9]+\]/[0:0]/g' \
     > "$BK/iptables.after.rules"
   diff -u "$BK/iptables.before.rules" "$BK/iptables.after.rules"
   ```

   文件 SHA 必须等于 preview SHA，mtime 应体现本次发布，9090 组状态也必须正确。iptables 最终相同只能证明**终态相同，不能证明过程中从未改写**。

   **G6，其他关卡通过、且没有活动事务后执行：**

   ```bash
   r7 'md5sum /etc/routermgmt/self-heal.sh
   crontab -l | grep self-heal
   ls /etc/routermgmt/state/txn/*/uncommitted 2>/dev/null'
   ```

   无 uncommitted 后：

   ```bash
   r7 'rm -f /tmp/selfheal.pause
   test ! -e /tmp/selfheal.pause
   curl -fsS -m 5 http://127.0.0.1:9090/version'
   ```

   **任一关失败：**停止并回报，不用其他设备的验收替代 R7。此轮没有做封锁演练，不宣称已经实测 R7 的全链故障接管。

13. **失败处置与回退令**

   先取脱敏诊断，不发新任务：

   ```bash
   api GET "/tasks?device_id=$DEV&page_size=100" > "$BK/tasks.failure.json"

   r7 'cat /etc/routermgmt/state/accepted_version
   ls /etc/routermgmt/state/txn/*/uncommitted 2>/dev/null
   cat /etc/routermgmt/state/txn/*/phase 2>/dev/null
   cat /etc/routermgmt/state/txn/*/final 2>/dev/null
   cat /etc/routermgmt/state/validate.log 2>/dev/null
   cat /etc/routermgmt/state/health.log 2>/dev/null
   curl -fsS -m 5 http://127.0.0.1:9090/version'
   ```

   | 现场 | 执行令 |
   |---|---|
   | apply 前失败 | 停止发布；绑定可从 CSV 恢复，设备仍用原配置 |
   | `rolled_back` | 核对设备配置 MD5 等于发布前备份、9090 与管理面正常；不要再次 apply |
   | `ok + skipped`、expired、unknown、超时 | 先核对 accepted、事务目录、文件和运行态；不得认定已回退或直接补发 |
   | `rollback_failed:true`、9090 不通 | 保留现场立即回报；不重启整机、mgmt-tunnel、网络，不运行应急脚本 |
   | 新配置已 accepted 后要求回旧配置 | 不能直接重发旧 v5；必须使用高于设备水位的新签名版本，另行制定恢复发布 |

   **仅在无 pending/running 任务、无 uncommitted，且决定恢复“平台绑定元数据”时**，可执行以下事务。它不会回退已发布的 desired 版本，也不会恢复设备 YAML：

   ```bash
   db -c "SELECT id,type,status FROM tasks
     WHERE device_id='rtr-37cf72' AND status IN ('pending','running');"
   ```

   确认为零行后：

   ```bash
   OLD_NODE=$(jq -r '.device.current_node_id' "$BK/device.before.json")
   [[ "$OLD_NODE" =~ ^[0-9]+$ ]]
   ```

   确认检查成功后：

   ```bash
   {
     printf '%s\n' \
       'BEGIN;' \
       "SELECT id FROM devices WHERE id='rtr-37cf72' FOR UPDATE;" \
       'CREATE TEMP TABLE r7_restore (LIKE node_assignments INCLUDING DEFAULTS);' \
       'COPY r7_restore (device_id,node_id,is_primary,failover_priority,created_at,updated_at) FROM STDIN WITH (FORMAT csv,HEADER true);'

     cat "$BK/bindings.before.csv"
     printf '\\.\n'

     printf '%s\n' \
       "DELETE FROM node_assignments WHERE device_id='rtr-37cf72';" \
       "INSERT INTO node_assignments SELECT * FROM r7_restore WHERE device_id='rtr-37cf72';" \
       "UPDATE devices SET current_node_id=$OLD_NODE,updated_at=now() WHERE id='rtr-37cf72';" \
       'COMMIT;'
   } | db
   ```

   恢复 current_node 同样可能触发自动策略，所以仍受第 5 步约束。恢复后重新查询绑定验证。

   **pause 处理：**确认事务已结束、回滚文件匹配、9090 和管理面正常后清除。事务未决时不盲清；立即回报，不能把自愈长期暂停后离场。

14. **收尾记录与回报**

   任何现网变更完成或中止，都执行变更三件套：

   - `docs/current/当前状态.md` 顶部新增：`2026-10-31 时段（人工执行／Codex指挥）：R7 回线配置升级……`，记录真实结果。
   - `docs/plans/R7回线升级执行记录-20261031.md`：记录检查点、绑定前后、revision/SHA、版本、task_id/status、G1–G6、失败与恢复动作。
   - 更新总览 §1 的 R7 状态／版本、§2 时间线、§4 未决事项；成功后补 SOP §十台账，并更新 §十一队列。

   凭据、原始配置、订阅 URL、完整任务原文不入文档或 git。最后：

   ```bash
   unset TOKEN SSHPASS
   ```

   每步按此格式回报即可：

   ```text
   步骤：
   PASS / STOP：
   关键输出：
   task_id / status / version（如有）：
   desired/current/highest/accepted：
   boot_id、隧道、pause 状态：
   ```
