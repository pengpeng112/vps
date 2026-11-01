本令只生成 runbook，**尚未登录或变更现网**。执行顺序固定为 **R1→R2→R3→R5→R6→R7→R8**；R4 只读核对，禁止重发。每台签收后才进入下一台。

**先指出两个必须由实时预检消除的冲突：**

- 历史记录中 R7 的节点 4 是 **fp0**；本次要求是 **fp1**。`assignments/batch` 不会修改已有绑定的 fp。本令不夹带修正其他绑定，实时仍为 fp0 则预检停止回报。
- R7 保留节点 13 自然排序，同时要求第二成员为 BWG-HY2：两者只有在实际排序相容时才能同时满足。若 13 排到 8 前面，停止回报，禁止修改 13 或节点 priority。

接口依据：[绑定副作用](/F:/vps/mgmt/backend/internal/httpserver/nodes.go:408)、[实际渲染排序及发布复用逻辑](/F:/vps/mgmt/backend/internal/httpserver/configs.go:29)、[上一轮复核](/F:/vps/review/codex-fix-verify-20261101.md)。

1. **确认独占窗口与截止时间**

   执行者先回报：

   ```text
   执行会话=<身份>
   广州后端本窗口独占，其他会话已停止写入
   R7 节点13冻结
   本轮仅做节点8绑定、节点7发布、恢复AUTO及验收
   ```

   未确认独占，不执行写入。不开启后台全舰队循环。

   时间按北京时间：

   - 重启点：**2026-11-02 00:00**。
   - **23:30 后不开始新的正常发布**。
   - **23:50 前完成全部验收或回退**，留出重启缓冲。
   - 开工预算按剩余每台 10 分钟，另留 20 分钟处理回退；不足则不开始第一笔写入。
   - 不修改定时重启，不提前重启设备、agent 或隧道。

2. **建立执行环境**

   以下 Python 块在执行者本机的**同一个 Python 控制会话**中分段执行。先检查已有工具：

   ```powershell
   python -c "import paramiko,yaml,scp; print('TOOLS_OK')"
   ```

   预期：`TOOLS_OK`。缺工具先补齐执行环境，不对设备降级操作方式。

   载入公共函数；JWT 使用你现成 TOTP 脚本取得的广州令牌，不回显：

   ```python
   import getpass, hashlib, http.client, json, re, shlex
   import shutil, subprocess, time, uuid
   from datetime import datetime, timedelta, timezone
   from pathlib import Path

   import paramiko, yaml
   from scp import SCPClient

   RUN = "bwg8-" + uuid.uuid4().hex
   OUT = Path("F:/vps/backups") / ("." + RUN)
   OUT.mkdir(exist_ok=False)       # 受控、忽略入库的取证目录
   CST = timezone(timedelta(hours=8))
   DEADLINE = datetime(2026, 11, 2, 0, 0, tzinfo=CST).timestamp()
   NORMAL_STOP = DEADLINE - 30*60
   RECOVERY_STOP = DEADLINE - 10*60

   TARGETS = [
       ("R1", "rtr-d3847d", 13222, 26),
       ("R2", "rtr-a9bcba", 13223, 31),
       ("R3", "rtr-38aa54", 13227, 29),
       ("R5", "rtr-c46a25", 13225, 19),
       ("R6", "rtr-673568", 13226, 21),
       ("R7", "rtr-37cf72", 13229, 7),
       ("R8", "rtr-1f6f0f", 13230, 7),
   ]
   PASSED = []

   gz = paramiko.SSHClient()
   gz.load_system_host_keys()
   gz.set_missing_host_key_policy(paramiko.RejectPolicy())
   gz.connect(
       "134.175.151.213", port=28722,
       username=input("广州 SSH 用户: ").strip(),
       password=getpass.getpass("广州 SSH 密码: "),
       look_for_keys=False, allow_agent=False, timeout=20,
   )
   TOKEN = getpass.getpass("广州 TOTP 登录所得 JWT: ")

   def run(client, script):
       # 原始 LF 脚本经 stdin 执行；不套嵌套 SSH，不在广州展开设备变量。
       i, o, e = client.exec_command("sh -s", timeout=180)
       i.channel.sendall(script.replace("\r\n", "\n").encode("utf-8"))
       i.channel.shutdown_write()
       out, err = o.read(), e.read()
       rc = o.channel.recv_exit_status()
       if rc:
           raise RuntimeError("远端命令失败 rc=%d；停止并收集脱敏错误" % rc)
       return out.decode("utf-8")

   def G(script):
       return run(gz, script)

   def D(script):
       return run(router, script)

   def SQL(text):
       return G(
           "sudo -n -u postgres psql -X -qAt "
           "-v ON_ERROR_STOP=1 -d routermgmt <<'BWG8_SQL'\n"
           + text + "\nBWG8_SQL\n"
       ).strip()

   def api(method, path, body=None):
       ch = gz.get_transport().open_channel(
           "direct-tcpip", ("127.0.0.1", 8080), ("127.0.0.1", 0)
       )
       c = http.client.HTTPConnection("127.0.0.1", 8080, timeout=20)
       c.sock = ch
       headers = {"Authorization": "Bearer " + TOKEN,
                  "Content-Type": "application/json"}
       c.request(method, "/api/v1" + path,
                 body=None if body is None else json.dumps(body),
                 headers=headers)
       r = c.getresponse()
       raw, status = r.read(), r.status
       c.close()
       if status != 200:
           raise RuntimeError("API HTTP=%d；停止，禁止盲目重试写请求" % status)
       return json.loads(raw)

   def pull(remote, local):
       with SCPClient(router.get_transport(), socket_timeout=30) as scp:
           scp.get(remote, str(local), preserve_times=True)

   def dump(path, obj):
       path.write_text(json.dumps(obj, ensure_ascii=False, indent=2),
                       encoding="utf-8")

   def window(recovery=False):
       now = int(G("date +%s\n").strip())
       assert abs(now - time.time()) < 60, "本机/广州时间偏差"
       assert now < (RECOVERY_STOP if recovery else NORMAL_STOP), "窗口不足"
       assert G("systemctl show gz-api -p MainPID --value\n").strip() == API_PID
   ```

   设备主机密钥未知或不匹配时停止；通过既有可信记录核实，禁止关闭校验绕过。

3. **全局平台预检**

   ```python
   print(G(r"""
   date -u
   hostname
   systemctl is-active nginx gz-api
   systemctl is-active gz-relay || true
   ss -ltnp | awk 'NR==1 || $4 ~ /:(8080|8443|13222|13223|13225|13226|13227|13229|13230)$/'
   curl --noproxy '*' -fsS -m 8 -o /dev/null \
     -w 'http_health=%{http_code}\n' \
     http://127.0.0.1:8080/api/v1/health
   curl --noproxy '*' -kfsS -m 8 -o /dev/null \
     -w 'tls_health=%{http_code}\n' \
     https://127.0.0.1:8443/api/v1/health
   """))
   API_PID = G("systemctl show gz-api -p MainPID --value\n").strip()
   print(SQL("SELECT current_database();"))
   ```

   预期：

   ```text
   nginx/gz-api：均 active
   gz-relay：不得 active
   http_health=200
   tls_health=200
   数据库：routermgmt
   8080：只监听回环
   7 个目标反向端口：全部 LISTEN
   ```

   不匹配即停止，不重启平台、不切 DMIT、不改安全组或密钥。

   节点 8 的 UDP 存活采用用户本轮实测；**不调用平台 TCP 探测接口判定 HY2 生死**。

4. **全舰队绑定、版本及排序预检——全部通过后才允许第一笔写入**

   定义只返回必要字段的查询：

   ```python
   def state(dev):
       return json.loads(SQL(f"""
       SELECT row_to_json(x) FROM (
         SELECT d.id,d.lifecycle_status,d.current_node_id,
                d.current_config_version,d.desired_config_version,
                d.highest_accepted_version,d.last_heartbeat_at,
                extract(epoch FROM now()-d.last_heartbeat_at) AS hb_age_s,
                (SELECT max(version) FROM configs WHERE device_id=d.id) AS maxv,
                c.sha256 AS desired_sha256
         FROM devices d
         LEFT JOIN configs c
           ON c.device_id=d.id AND c.version=d.desired_config_version
         WHERE d.id='{dev}'
       ) x;
       """))

   def bindings(dev):
       return json.loads(SQL(f"""
       SELECT coalesce(json_agg(x ORDER BY
                x.is_primary DESC,x.failover_priority,
                x.priority DESC,x.node_id),'[]'::json)
       FROM (
         SELECT a.node_id,a.is_primary,a.failover_priority,a.updated_at,
                n.name,n.proto,n.host,n.port,n.priority,n.enabled
         FROM node_assignments a JOIN nodes n ON n.id=a.node_id
         WHERE a.device_id='{dev}'
       ) x;
       """))

   def active_tasks(dev):
       return json.loads(SQL(f"""
       SELECT coalesce(json_agg(x),'[]'::json) FROM (
         SELECT id,type,status,config_version,deadline
         FROM tasks WHERE device_id='{dev}'
           AND status IN ('pending','running','unknown')
         ORDER BY created_at
       ) x;
       """))

   def semantic(rows):
       return [{k:v for k,v in x.items() if k!="updated_at"} for x in rows]

   N8 = json.loads(SQL("""
   SELECT row_to_json(x) FROM (
     SELECT id AS node_id,name,proto,host,port,priority,enabled
     FROM nodes WHERE id=8
   ) x;
   """))
   assert N8["enabled"] is True
   assert N8["host"] == "74.82.193.113" and N8["port"] == 34567
   assert N8["priority"] == 20
   assert "BWG-HY2" in N8["name"]
   print({k:N8[k] for k in ("node_id","name","proto","host","port","priority")})

   PRE = {}
   for label, dev, port, old in TARGETS:       # 本循环只读
       s, a = state(dev), bindings(dev)
       assert s["lifecycle_status"] == "approved"
       assert s["current_node_id"] == 7
       assert s["hb_age_s"] is not None and 0 <= s["hb_age_s"] <= 90
       assert (s["current_config_version"] ==
               s["desired_config_version"] ==
               s["highest_accepted_version"] == s["maxv"] == old)
       assert active_tasks(dev) == []
       assert api("GET", f"/devices/{dev}")["device"]["service_active"] is True

       byid = {x["node_id"]:x for x in a}
       oldset = {7,9,4,10,11} | ({13} if label=="R7" else set())
       assert set(byid) == oldset, f"{label}: 绑定集合变化"
       assert all(x["enabled"] for x in a)
       assert [x["node_id"] for x in a if x["is_primary"]] == [7]

       for node_id, fp in {9:0,4:1,10:2,11:3}.items():
           assert byid[node_id]["failover_priority"] == fp, \
               f"{label}: node {node_id} fp 不符合目标，停止"
       assert byid[9]["priority"] == 15
       assert byid[7]["name"] == "US666-CN2GIA-01"
       if label == "R7":
           assert byid[13]["failover_priority"] == 0

       sim = a + [dict(N8, is_primary=False, failover_priority=0)]
       sim.sort(key=lambda x: (
           -int(x["is_primary"]), x["failover_priority"],
           -x["priority"], x["node_id"]
       ))
       ids = [x["node_id"] for x in sim]
       assert [x for x in ids if x!=13] == [7,8,9,4,10,11]
       assert ids[0:2] == [7,8], f"{label}: 自然排序不满足第二成员=8"
       PRE[dev] = {"state":s, "bindings":a, "sim":sim}
       dump(OUT / (label+".precheck.json"), PRE[dev])
       print(label, "PRECHECK_PASS", ids)

   R4_BEFORE = bindings("rtr-cd473f")
   R4_STATE = state("rtr-cd473f")
   assert any(x["node_id"]==8 for x in R4_BEFORE)

   remaining = DEADLINE - int(G("date +%s\n").strip())
   assert remaining >= len(TARGETS)*600 + 1200, "全批窗口预算不足"
   window()
   ```

   预期：七行 `PRECHECK_PASS`。普通设备模拟顺序为：

   ```text
   [7,8,9,4,10,11]
   ```

   R7 含 13，以实际排序为准，但去掉 13 后仍须为上述序列，且头两项必须为 `[7,8]`。

   **任何断言失败，停在这里回报原值；不得通过调整 fp、priority 或删除 13 消除告警。**

5. **逐台循环入口：选中唯一下一台，实登、备份并建立保护基线**

   每完成一台后，重新执行本步骤。禁止自行增加 `PASSED`。

   ```python
   LABEL, DEV, PORT, OLD = TARGETS[len(PASSED)]
   K = OUT / LABEL
   K.mkdir(exist_ok=False)
   print("CURRENT", LABEL, DEV, PORT, OLD)
   window()

   assert semantic(bindings(DEV)) == semantic(PRE[DEV]["bindings"])
   assert active_tasks(DEV) == []
   BEFORE = state(DEV)
   assert BEFORE["current_config_version"] == BEFORE["desired_config_version"] == OLD

   channel = gz.get_transport().open_channel(
       "direct-tcpip", ("127.0.0.1", PORT), ("127.0.0.1", 0)
   )
   router = paramiko.SSHClient()
   router.load_system_host_keys()
   router.set_missing_host_key_policy(paramiko.RejectPolicy())
   router.connect(
       "127.0.0.1", port=PORT, username="root", sock=channel,
       password=getpass.getpass(LABEL+" SSH 密码: "),
       look_for_keys=False, allow_agent=False, timeout=20,
   )

   identity = D("grep '^DEVICE_ID=' /etc/routermgmt/agent.conf\n")
   assert identity.strip() == "DEVICE_ID="+DEV

   print(D(r"""
   set -eu
   [ ! -e /tmp/selfheal.pause ]
   [ ! -L /tmp/selfheal.pause ]
   for t in /etc/routermgmt/state/txn/*/uncommitted; do
       [ ! -f "$t" ]
   done
   date -u
   cat /etc/routermgmt/state/accepted_version
   stat -c 'config_mtime=%Y size=%s' /etc/mihomo/config.yaml
   md5sum /etc/mihomo/config.yaml /etc/routermgmt/self-heal.sh
   grep -E 'self-heal[.]sh|routermgmt-reboot|0 0 .*reboot' /etc/crontabs/root
   grep -E '^[[:space:]]*procd_set_param[[:space:]]+respawn' \
       /etc/init.d/routermgmt /etc/init.d/mgmt-tunnel
   curl --noproxy '*' -kfsS -m 8 -o /dev/null \
       -w 'health=%{http_code}\n' \
       https://127.0.0.1:18443/api/v1/health
   """))

   BOOT_BEFORE = D("cat /proc/sys/kernel/random/boot_id\n").strip()
   PROTECTED_COMMAND = r"""
   md5sum /etc/config/network /etc/config/wireless \
     /etc/routermgmt/tunnel.conf \
     /etc/init.d/routermgmt /etc/init.d/mgmt-tunnel \
     /etc/routermgmt/self-heal.sh
   """
   PROTECTED_BEFORE = D(PROTECTED_COMMAND)
   (K/"protected.before.md5").write_text(PROTECTED_BEFORE, encoding="utf-8")
   pull("/etc/mihomo/config.yaml", K/"config.before.yaml")

   raw_before = (K/"config.before.yaml").read_bytes()
   assert hashlib.sha256(raw_before).hexdigest() == BEFORE["desired_sha256"]
   assert int(D("cat /etc/routermgmt/state/accepted_version\n").strip()) == OLD
   BEFORE_DOC = yaml.safe_load(raw_before)
   assert isinstance(BEFORE_DOC["proxy-providers"]["airport-sub"], dict)
   ```

   预期：

   - 身份与当前台一致；health `200`；无 pause、无 uncommitted。
   - accepted 等于表中起始版本。
   - self-heal 完整 MD5 为 `329c6081caaa05595c13ff380687187b`。
   - 两个 init 文件均为 `respawn 3600 0 0`；午夜 reboot 与 self-heal cron 在位。
   - 配置备份 SHA 与当前已发布配置一致。

   不一致即停止，**不顺手修 WiFi、网络、隧道、init 或 agent**。

6. **只绑定节点 8，不发布**

   精确报文：

   ```http
   POST /api/v1/assignments/batch
   Content-Type: application/json

   {"device_ids":["当前DEV"],"node_id":8,"is_primary":false,"apply":false}
   ```

   执行：

   ```python
   window()
   BIND = api("POST", "/assignments/batch", {
       "device_ids":[DEV], "node_id":8,
       "is_primary":False, "apply":False,
   })
   dump(K/"bind.result.json", BIND)
   assert BIND["apply"] is False and len(BIND["items"]) == 1
   b = BIND["items"][0]
   assert b["device_id"] == DEV and b["ok"] is True
   assert not b.get("error") and not b.get("task_id")

   AFTER_BIND = bindings(DEV)
   assert semantic(AFTER_BIND) == semantic(PRE[DEV]["sim"])
   assert state(DEV)["current_node_id"] == 7
   assert state(DEV)["desired_config_version"] == OLD
   assert active_tasks(DEV) == []
   ```

   预期：

   ```json
   {"items":[{"device_id":"当前DEV","ok":true}],"apply":false}
   ```

   新绑定必须是 `8 / is_primary=false / fp=0`，其他绑定语义不变。R7 的 13 整行还须与预检一致：

   ```python
   if LABEL == "R7":
       assert next(x for x in AFTER_BIND if x["node_id"]==13) == \
              next(x for x in PRE[DEV]["bindings"] if x["node_id"]==13)
   ```

   HTTP 200 但 `items[0].ok=false` 也算失败；接口可能已经部分提交，必须查库，不能假定无变更。

7. **Preview 闸门：确认排序、节点参数、provider 与其他配置**

   ```python
   P = api("POST", f"/devices/{DEV}/configs/preview", {})
   PRAW = P["masked_yaml"]
   PDOC = yaml.safe_load(PRAW)
   EXPECTED = [x["name"] for x in AFTER_BIND]

   def group(doc, name):
       xs = [x for x in doc["proxy-groups"] if x["name"]==name]
       assert len(xs)==1
       return xs[0]

   def check_provider(raw, doc):
       assert b"\r" not in raw
       text = raw.decode("utf-8")
       assert re.search(r"(?m)^proxy-providers:\s*$", text)
       assert re.search(r"(?m)^  airport-sub:\s*$", text)
       assert re.search(r"(?m)^    type: http\s*$", text)
       p = doc["proxy-providers"]["airport-sub"]
       assert isinstance(p, dict) and p["type"]=="http"
       assert p["proxy"]=="NODE-PICK"
       assert isinstance(p["health-check"], dict)
       assert p["health-check"]["enable"] is True

   check_provider(PRAW.encode(), PDOC)
   assert PDOC["proxy-providers"] == BEFORE_DOC["proxy-providers"]
   assert group(PDOC,"NODE-PICK")["proxies"] == EXPECTED
   assert group(PDOC,"NODE-PICK")["type"] == "fallback"
   assert group(PDOC,"AUTO")["proxies"] == ["NODE-PICK","AIRPORT","DIRECT"]
   assert len(EXPECTED) == (7 if LABEL=="R7" else 6)
   assert EXPECTED[0]=="US666-CN2GIA-01"
   assert "BWG-HY2" in EXPECTED[1]
   assert "154.36.137.54" not in PRAW

   p8 = [x for x in PDOC["proxies"] if x["name"]==N8["name"]]
   assert len(p8)==1
   assert p8[0]["type"]=="hysteria2"
   assert p8[0]["server"]=="74.82.193.113" and p8[0]["port"]==34567

   dump(K/"preview.summary.json", {
       "revision":P["revision"], "sha256":P["sha256"],
       "current_version":P["current_version"],
       "next_version":P["next_version"],
       "nodepick":EXPECTED, "provider_multiline":True,
   })
   print("PREVIEW_PASS", EXPECTED)
   ```

   人工复核 diff 的变更范围：新增节点 8 定义、相关组成员及相应自动生成直连规则；机场 provider、AUTO 层次和其他功能不得意外丢失。

   **不要把完整 `masked_yaml`、diff 或 API 原文贴进回报**：当前掩码函数不会遮蔽机场订阅 URL 中的 token。

   Preview 任一不通过，禁止 apply，进入第 13 步。

8. **暂停 self-heal，单次发布节点 7**

   先建立本会话持有的 pause，再复核 preview 未变化：

   ```python
   print(D("RUN="+shlex.quote(RUN)+"\n"+r"""
   set -eu
   for t in /etc/routermgmt/state/txn/*/uncommitted; do
       [ ! -f "$t" ]
   done
   [ ! -e /tmp/selfheal.pause ]
   [ ! -L /tmp/selfheal.pause ]
   (umask 077; set -C; printf '%s\n' "$RUN" > /tmp/selfheal.pause)
   for t in /etc/routermgmt/state/txn/*/uncommitted; do
       [ ! -f "$t" ]
   done
   echo PAUSE_OWNED
   """))

   P2 = api("POST", f"/devices/{DEV}/configs/preview", {})
   assert P2["revision"]==P["revision"] and P2["sha256"]==P["sha256"]
   assert active_tasks(DEV)==[]
   window()
   PUBLISH_START = int(G("date +%s\n").strip())

   PUB = api("POST", "/assignments/batch", {
       "device_ids":[DEV], "node_id":7,
       "is_primary":True, "apply":True,
   })
   dump(K/"publish.result.json", PUB)
   assert PUB["apply"] is True and len(PUB["items"])==1
   r = PUB["items"][0]
   assert r["device_id"]==DEV and r["ok"] is True and not r.get("error")
   TASK, VERSION = r["task_id"], int(r["version"])
   assert TASK and VERSION > OLD
   print("PUBLISHED", TASK, VERSION)
   ```

   精确发布报文：

   ```json
   {"device_ids":["当前DEV"],"node_id":7,"is_primary":true,"apply":true}
   ```

   预期：单台 `ok:true`、非空 `task_id`、版本高于原 accepted。无其他发布时通常为：

   ```text
   R1 27；R2 32；R3 30；R5 20；R6 22；R7 8；R8 8
   ```

   以接口返回和数据库实值为准，禁止手工填版本号。

   **本接口没有 preview revision 校验，也没有使用请求幂等键。超时或响应丢失时禁止直接重发。**先查该台新任务、configs、desired 和绑定实际提交情况。

9. **只轮询本次 task_id**

   ```python
   def wait_task(dev, task, version):
       end = time.monotonic()+160
       while time.monotonic() < end:
           items = api("GET", f"/tasks?device_id={dev}&page_size=100")["items"]
           found = [x for x in items if x["id"]==task]
           if found:
               t = found[0]
               print(task, t["status"])
               if t["status"]=="ok":
                   result = t.get("result") or {}
                   assert result.get("skipped") in (None, False), "skipped 禁签收"
                   assert t["type"]=="switch_node"
                   assert int(t["config_version"])==version
                   return t
               if t["status"] not in ("pending","running"):
                   raise RuntimeError("任务异常终态，停止："+t["status"])
           time.sleep(5)
       raise RuntimeError("任务超时/结果未知，停止；不得叠加新发布")

   TASK_RESULT = wait_task(DEV, TASK, VERSION)
   dump(K/"task.result.json", TASK_RESULT)
   ```

   唯一通过条件：

   ```text
   精确 task_id 匹配
   status=ok
   result.skipped 不为 true
   config_version=本次 VERSION
   ```

   `failed / rolled_back / expired / cancelled / unknown`、超时、查询失败均停止下一台。仍为 pending/running 或存在 uncommitted 时，先查清原任务，禁止叠加回退任务。

10. **发布后恢复 AUTO，并取得缓存写穿证据**

   恢复 AUTO 属于本轮发布收尾，最多执行一次：

   ```python
   print(D(r"""
   set -eu
   for t in /etc/routermgmt/state/txn/*/uncommitted; do
       [ ! -f "$t" ]
   done
   h=$(curl --noproxy '*' -sS -m 10 -X PUT \
       -H 'Content-Type: application/json' --data '{"name":"AUTO"}' \
       -o /dev/null -w '%{http_code}' \
       http://127.0.0.1:9090/proxies/PROXY)
   printf 'AUTO_PUT=%s\n' "$h"
   [ "$h" = 204 ]
   """))

   def runtime_group(name):
       return json.loads(D(
           "curl --noproxy '*' -fsS -m 8 "
           + shlex.quote("http://127.0.0.1:9090/proxies/"+name) + "\n"
       ))

   assert runtime_group("PROXY")["now"]=="AUTO"
   assert runtime_group("AUTO")["all"]==["NODE-PICK","AIRPORT","DIRECT"]
   ```

   预期：`AUTO_PUT=204`，随后 `PROXY.now=AUTO`。

   对每台制作稳定的 cache 副本；R7、R8 尤其不得省略：

   ```python
   REMOTE_BK = "/tmp/"+RUN
   print(D("BK="+shlex.quote(REMOTE_BK)+"\n"+r"""
   set -eu
   umask 077
   mkdir "$BK"
   h() { sha256sum "$1" | awk '{print $1}'; }
   stable=0
   for i in 1 2 3; do
       h0=$(h /etc/mihomo/cache.db)
       cp -p /etc/mihomo/cache.db "$BK/cache.after.db"
       hc=$(h "$BK/cache.after.db")
       h1=$(h /etc/mihomo/cache.db)
       if [ "$h0" = "$hc" ] && [ "$h0" = "$h1" ]; then
           stable=1
           printf 'CACHE_SHA256=%s\n' "$hc"
           break
       fi
       sleep 2
   done
   [ "$stable" = 1 ]
   """))

   cache_sha = D("sha256sum "+shlex.quote(REMOTE_BK+"/cache.after.db")+"\n").split()[0]
   pull(REMOTE_BK+"/cache.after.db", K/"cache.after.db")
   assert hashlib.sha256((K/"cache.after.db").read_bytes()).hexdigest()==cache_sha

   BBOLT = shutil.which("bbolt") or input("已有 bbolt 可执行文件绝对路径: ").strip()
   subprocess.run([BBOLT,"check",str(K/"cache.after.db")],
                  check=True, capture_output=True)
   cached = subprocess.run(
       [BBOLT,"get",str(K/"cache.after.db"),"selected","PROXY"],
       check=True, capture_output=True, text=True,
   ).stdout.strip()
   assert cached=="AUTO", "缓存未写穿，禁止签收"
   print("CACHE_CHECK_OK selected/PROXY=AUTO")
   ```

   只检查副本；禁止在线改写、删除或替换 `cache.db`。缓存失败即停，不能以任务 ok 或即时 API=AUTO 代替持久化证据。

11. **本台六项验收，加做 SOP G1–G6**

   六项验收标准：

   | 项 | 必须通过 |
   |---|---|
   | ①任务与版本 | 本次 task ok、非 skipped；current=desired=highest=设备 accepted=VERSION |
   | ②NODE-PICK | 普通台 6、R7 7；首成员 US666-CN2GIA-01、第二含 BWG-HY2；DB/YAML/运行时完整顺序一致 |
   | ③出口 | 连续三次 `154.36.139.155`；gstatic 204 |
   | ④自动选择 | PROXY.now=AUTO；AUTO 成员正确；缓存 selected/PROXY=AUTO |
   | ⑤管理健康 | 设备 health 200；实登有效；心跳连续推进 |
   | ⑥文件结构 | provider 保持多行；无 CR；`mihomo -t -d /etc/mihomo` 成功 |

   先查落盘配置与运行时：

   ```python
   pull("/etc/mihomo/config.yaml", K/"config.after.yaml")
   raw_after = (K/"config.after.yaml").read_bytes()
   AFTER_DOC = yaml.safe_load(raw_after)

   check_provider(raw_after, AFTER_DOC)
   assert AFTER_DOC["proxy-providers"]==BEFORE_DOC["proxy-providers"]
   assert hashlib.sha256(raw_after).hexdigest()==P["sha256"]
   assert raw_after != raw_before
   assert group(AFTER_DOC,"NODE-PICK")["proxies"]==EXPECTED
   assert semantic(bindings(DEV))==semantic(AFTER_BIND)

   np = runtime_group("NODE-PICK")
   px = runtime_group("PROXY")
   au = runtime_group("AUTO")
   assert np["all"]==EXPECTED
   assert len(np["all"])==(7 if LABEL=="R7" else 6)
   assert np["all"][0]=="US666-CN2GIA-01"
   assert "BWG-HY2" in np["all"][1]
   assert px["now"]=="AUTO"
   assert au["all"]==["NODE-PICK","AIRPORT","DIRECT"]

   dump(K/"runtime.groups.json", {"NODE-PICK":np,"PROXY":px,"AUTO":au})
   print("ORDER_PASS", np["all"])
   ```

   SHA 不同时停止核查；策略可能覆盖 YAML 的 `mode:`，不能直接认定压扁，也不能未经解释就放行。

   再执行设备侧检查。**R8 与其他台执行相同完整检查，不以今晨已修复免检：**

   ```python
   print(D("EXPECTED_VERSION="+str(VERSION)+"\n"+r"""
   set -eu
   CFG=/etc/mihomo/config.yaml

   for t in /etc/routermgmt/state/txn/*/uncommitted; do
       [ ! -f "$t" ]
   done
   [ "$(cat /etc/routermgmt/state/accepted_version)" = "$EXPECTED_VERSION" ]

   set -- $(pidof mihomo)
   [ "$#" -eq 1 ]
   p=$1
   agent=$(ps w | awk '/routermgmt-agent/ && !/awk/ {n++} END{print n+0}')
   [ "$agent" -ge 1 ]
   printf 'mihomo=1 agent=%s\n' "$agent"

   args=$(tr '\000' '\n' < "/proc/$p/cmdline")
   home=$(printf '%s\n' "$args" | awk '$0=="-d"{getline;print;exit}')
   file=$(printf '%s\n' "$args" | awk '$0=="-f"{getline;print;exit}')
   [ "$home" = /etc/mihomo ]
   [ -n "$file" ] || file="$home/config.yaml"
   [ "$file" = "$CFG" ]

   timeout 30 "/proc/$p/exe" -t -d /etc/mihomo \
       -f /etc/mihomo/config.yaml >/dev/null 2>&1
   echo MIHOMO_VALIDATE_OK

   ! grep -qF '154.36.137.54' "$CFG"
   ! grep -qF '154.36.137.54' /etc/routermgmt/tunnel.conf

   for svc in routermgmt mgmt-tunnel; do
       grep -Eq '^[[:space:]]*procd_set_param[[:space:]]+respawn[[:space:]]+3600[[:space:]]+0[[:space:]]+0[[:space:]]*$' \
           "/etc/init.d/$svc"
       echo "$svc file_respawn=3600,0,0"
       ubus call service list "{\"name\":\"$svc\"}" |
           jsonfilter -e "@['$svc'].instances[*].respawn"
   done

   md5=$(md5sum /etc/routermgmt/self-heal.sh | awk '{print $1}')
   [ "$md5" = 329c6081caaa05595c13ff380687187b ]
   grep -Eq '^[*][[:space:]]+[*][[:space:]]+[*][[:space:]]+[*][[:space:]]+[*].*self-heal[.]sh' \
       /etc/crontabs/root

   h=$(curl --noproxy '*' -kfsS -m 8 -o /dev/null \
       -w '%{http_code}' https://127.0.0.1:18443/api/v1/health)
   [ "$h" = 200 ]
   echo HEALTH=200

   for i in 1 2 3; do
       ip=$(curl -4 --noproxy '' -fsS -m 15 \
           -x http://127.0.0.1:7890 https://api.ipify.org)
       printf 'EGRESS_%s=%s\n' "$i" "$ip"
       [ "$ip" = 154.36.139.155 ]
       sleep 2
   done
   h=$(curl --noproxy '' -fsS -m 15 -x http://127.0.0.1:7890 \
       -o /dev/null -w '%{http_code}' https://www.gstatic.com/generate_204)
   [ "$h" = 204 ]
   echo GSTATIC=204
   nslookup www.baidu.com 127.0.0.1 >/dev/null 2>&1
   echo DNS_OK
   stat -c 'config_mtime=%Y size=%s' "$CFG"
   """))

   assert D("cat /proc/sys/kernel/random/boot_id\n").strip()==BOOT_BEFORE
   assert D(PROTECTED_COMMAND)==PROTECTED_BEFORE
   assert int(D("stat -c '%Y' /etc/mihomo/config.yaml\n").strip()) >= PUBLISH_START

   s = state(DEV)
   assert (s["current_config_version"] ==
           s["desired_config_version"] ==
           s["highest_accepted_version"] == VERSION)
   assert s["desired_sha256"]==P["sha256"]
   ```

   `file_respawn=3600,0,0` 与 procd 内存参数分开记录；内存旧值留待午夜正常重启加载，**本轮不得为此 restart**。

   G1 连续观察两个心跳周期：

   ```python
   h = state(DEV)
   for cycle in (1,2):
       limit = time.monotonic()+100
       while True:
           time.sleep(10)
           n = state(DEV)
           assert n["hb_age_s"] is not None and 0 <= n["hb_age_s"] <= 90
           if n["last_heartbeat_at"] != h["last_heartbeat_at"]:
               print("HEARTBEAT_CYCLE", cycle, n["last_heartbeat_at"])
               h = n
               break
           assert time.monotonic() < limit, "心跳未推进，停止"
   ```

   预期：上述断言全过，出现两行 `HEARTBEAT_CYCLE`。WiFi、网络、隧道和保护文件散列不变，boot_id 不变。

12. **释放本会话 pause，签收本台，再进入下一台**

   ```python
   print(D("RUN="+shlex.quote(RUN)+"\n"+r"""
   set -eu
   for t in /etc/routermgmt/state/txn/*/uncommitted; do
       [ ! -f "$t" ]
   done
   [ "$(cat /tmp/selfheal.pause)" = "$RUN" ]
   rm /tmp/selfheal.pause
   [ ! -e /tmp/selfheal.pause ]
   echo PAUSE_CLEAR
   """))

   assert runtime_group("PROXY")["now"]=="AUTO"
   assert D(PROTECTED_COMMAND)==PROTECTED_BEFORE
   window(recovery=True)

   dump(K/"acceptance.json", {
       "device":DEV, "task_id":TASK, "version":VERSION,
       "nodepick":EXPECTED, "egress":"154.36.139.155",
       "proxy":"AUTO", "health":200,
       "provider_multiline":True, "mihomo_validate":True,
       "cache_proxy":"AUTO", "G1_G6":"PASS",
       "boot_id":BOOT_BEFORE,
   })
   PASSED.append(LABEL)
   router.close()
   print(LABEL, "PASS；允许进入下一台")
   ```

   执行者逐台回报：

   ```text
   R? PASS
   task=<id> status=ok skipped=false
   current/desired/highest/accepted=<V>/<V>/<V>/<V>
   NODE-PICK=<完整顺序>，count=<6或7>
   egress=154.36.139.155×3；gstatic=204
   PROXY=AUTO；cache selected/PROXY=AUTO
   health=200；provider多行；mihomo -t -d=PASS
   G1-G6=PASS；pause=无；uncommitted=无；保护散列不变
   ```

   **任一项失败，不追加 PASSED，不进入下一台。**

13. **失败动作与回退——只处理当前这一台**

   首先停止全批，保留已成功设备。回报失败步骤、当前绑定、任务终态、版本四元组、pause/txn 状态。

   - 写请求超时或接口 `ok:false`：先查实际提交情况。
   - 原任务仍 pending/running、结果 unknown 或存在 uncommitted：先等待并取证，禁止叠加发布、删除事务标记或强制重启。
   - 以下删除与回退，只能在原任务已查清、无在途任务、无 uncommitted 后执行。

   **13.1 删除当前台新增的节点 8 绑定**

   ```python
   assert active_tasks(DEV)==[]
   print(D(r"""
   set -eu
   for t in /etc/routermgmt/state/txn/*/uncommitted; do
       [ ! -f "$t" ]
   done
   echo TXN_CLEAR
   """))
   window(recovery=True)

   SQL(f"""
   BEGIN;
   SET LOCAL lock_timeout='5s';
   DO $bwg8$
   DECLARE affected integer;
   BEGIN
     DELETE FROM node_assignments
     WHERE device_id='{DEV}' AND node_id=8
       AND is_primary=false AND failover_priority=0;
     GET DIAGNOSTICS affected = ROW_COUNT;
     IF affected <> 1 THEN
       RAISE EXCEPTION 'expected exactly one owned node8 assignment';
     END IF;
   END
   $bwg8$;
   COMMIT;
   """)
   assert semantic(bindings(DEV))==semantic(PRE[DEV]["bindings"])
   ```

   预期：仅删除该台节点 8 一行；R7 节点 13 不变。若节点 8 根本未写入，跳过 DELETE，查明后记录，禁止扩大删除条件。

   **13.2 回退也必须 preview**

   ```python
   RB = api("POST", f"/devices/{DEV}/configs/preview", {})
   RBDOC = yaml.safe_load(RB["masked_yaml"])
   check_provider(RB["masked_yaml"].encode(), RBDOC)
   RB_NAMES = [x["name"] for x in PRE[DEV]["bindings"]]
   assert group(RBDOC,"NODE-PICK")["proxies"]==RB_NAMES
   assert RBDOC["proxy-providers"]==BEFORE_DOC["proxy-providers"]
   assert N8["name"] not in RB_NAMES
   print("ROLLBACK_PREVIEW_PASS", RB_NAMES)
   ```

   **13.3 已经发布过含 8 的配置：重新发布**

   确认 pause 为本会话持有；若已释放，按第 8 步重新建立。然后：

   ```python
   window(recovery=True)
   RBPUB = api("POST", "/assignments/batch", {
       "device_ids":[DEV], "node_id":7,
       "is_primary":True, "apply":True,
   })
   rr = RBPUB["items"][0]
   assert rr["ok"] is True and not rr.get("error")
   RB_TASK, RB_VERSION = rr["task_id"], int(rr["version"])
   assert RB_VERSION > int(D(
       "cat /etc/routermgmt/state/accepted_version\n").strip())
   RB_RESULT = wait_task(DEV, RB_TASK, RB_VERSION)
   ```

   随后重复 AUTO、cache、provider、解析、出口、health、G1–G6 验收；成员恢复为**原始基线**，普通台 5、R7 6。验证通过后清除本会话 pause。

   **尚未产生新发布版本就失败的分支：**删绑定并 preview 对齐原态后，设备配置没有收到本次变更。此时重发布可能复用旧 SHA/版本，必然或可能 `skipped`；记录为“仅撤销待发布绑定，设备未下发”，不要制造有效回退的假证据。若已提交配置、版本或任务的情况无法明确，保持停止并回报。

   **禁止**降低 accepted/highest、删除 configs、手工恢复设备 YAML、修改 13，或反复重试以绕过 `_rb` 等已知问题。回退失败也停在当前台，不修 agent、不推进全批。

14. **全批完成后的只读汇总与午夜复验**

   ```python
   assert PASSED==["R1","R2","R3","R5","R6","R7","R8"]
   window(recovery=True)

   for label, dev, port, old in TARGETS:
       s, a = state(dev), bindings(dev)
       assert active_tasks(dev)==[]
       assert s["current_node_id"]==7
       assert (s["current_config_version"] ==
               s["desired_config_version"] ==
               s["highest_accepted_version"])
       assert s["hb_age_s"] is not None and 0 <= s["hb_age_s"] <= 90
       assert any(x["node_id"]==8 and not x["is_primary"]
                  and x["failover_priority"]==0 for x in a)
       print(label, s["current_config_version"],
             [x["node_id"] for x in a], s["hb_age_s"])

   assert bindings("rtr-cd473f")==R4_BEFORE
   r4 = state("rtr-cd473f")
   assert r4["desired_config_version"]==R4_STATE["desired_config_version"]
   print("R4_UNCHANGED")
   ```

   提交矩阵，填实测值，禁止提前填 PASS：

   | 设备 | 起始→最终版本 | task / 非skipped | NODE-PICK数/完整顺序 | current=desired=accepted | 出口×3 | AUTO/缓存 | health | provider/-t | G1–G6 |
   |---|---|---|---|---|---|---|---|---|---|
   | R1 | 26→实值 | 待填 | 6 / 待填 | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 |
   | R2 | 31→实值 | 待填 | 6 / 待填 | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 |
   | R3 | 29→实值 | 待填 | 6 / 待填 | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 |
   | R5 | 19→实值 | 待填 | 6 / 待填 | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 |
   | R6 | 21→实值 | 待填 | 6 / 待填 | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 |
   | R7 | 7→实值 | 待填 | 7 / 含13自然落位 | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 |
   | R8 | 7→实值 | 待填 | 6 / 待填 | 待填 | 待填 | 待填 | 待填 | **多行及-d必验** | 待填 |
   | R4 | 实值→不变 | 未下发 | 原绑定不变 | 只读核对 | — | — | — | — | 跳过写入 |

   **23:50 前**确认七台均已清 pause、无 uncommitted、无在途任务。

   **00:05、00:15**再逐台只读复验：boot_id 已改变、版本四元组对齐、PROXY 仍 AUTO、成员顺序不变、出口/health 正常、provider 多行且 `-t -d` 通过、procd 内存 respawn 为 `3600/0/0`。R7 同时衔接已有午夜验收，确认 VPS4 不再建连。

   午夜复验失败只回报，不自动热修。重启前签收写“发布验收完成”；跨重启通过后再关闭对应遗留项。

15. **记录义务——成功、部分完成或回退都必须记录**

   按总览 §7 的固定顺序完成：

   1. `docs/current/当前状态.md` 顶部追加本会话条目：完成设备、版本/task、矩阵结论、失败/回退、未完成设备及午夜待验事项；保留旧条目。
   2. 新建 `docs/plans/节点8-BWG-HY2全舰队绑定执行记录-20261101.md`：保存逐步结果、脱敏证据索引、配置及缓存副本 SHA、R7 的 13 排序、R8 多行与解析证据、截止时间和回退情况。
   3. 更新总览：
      - §1：实际绑定覆盖、链序、版本；注明节点 9 与 7 同服务器、节点 8 才是独立 BWG UDP 层。
      - §2：追加本轮时间线及执行会话身份。
      - §4：仅在七台全过后关闭 N1/4b；部分完成则列明余项。R7/R8 跨重启事项须等午夜实测后关闭。
   4. 按 SOP 更新升级台账。

   记录中只放脱敏摘要和 SHA；JWT、密码、订阅 URL、原始 YAML、cache 副本及密钥不得入文档或 Git。
