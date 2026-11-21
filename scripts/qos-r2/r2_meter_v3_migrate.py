"""R2 proxy-meter v3 migration (metering only, within existing R2 maintenance scope).

Phase A (gate, read-only on production state):
  - upload meter-v3 + endpoints generator to /tmp
  - generator PREVIEW into /tmp/pm3-preview (dummy chains for meter dry-run)
  - verify preview rules sane (static endpoints present, airport unresolved listed)
Phase B (migration):
  - on-device backup dir + local pull (v2 script/state/rules/crontab/controller)
  - install /etc/routermgmt/proxy-meter-v3.sh + proxy-meter-endpoints.sh (LF, 0755)
  - generate real rules-v3/coverage-v3; first run -> baseline seq=1
  - cron: comment out v2 line, add v3 line (v2 kept commented for rollback)
  - contain the known-dangerous v1 controller (rename + chmod 000) after
    re-verifying it is referenced by nothing (cron/rc.local/handlers/process)
  - post-checks: groups unchanged, agent heartbeat intact (via GZ)
Never calls apply/release/emergency of any controller. No GZ writes.
"""
from pathlib import Path
import datetime
import json
import time
import paramiko

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "scripts/qos-r2"
OUT = ROOT / "logs/r2-meter-v3-migration-20261117.json"
LOCAL_BAK = ROOT / "backups/r2-meter-v3-migration-20261117"
BT = chr(96)


def controlled_row(path, name):
    for line in (ROOT / path).read_text(encoding="utf-8-sig").splitlines():
        cells = [x.strip() for x in line.strip().strip("|").split("|")]
        if line.startswith("|") and cells[0] == name:
            return "|".join(cells[1:])
    raise ValueError("controlled credential field missing: %s" % name)


def quoted(value):
    return __import__("re").findall(BT + "([^" + BT + "]+)" + BT, value)


DEV_PW = quoted(controlled_row("backups/SN-R系列凭证总览.md", "**root/SSH 口令**"))[0]


def connect(host, port, user, pw, jump=None):
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    sock = None
    if jump:
        sock = jump.get_transport().open_channel("direct-tcpip", (host, port), ("127.0.0.1", 0), timeout=15)
    try:
        c.connect(host, port=port, username=user, password=pw, sock=sock,
                  timeout=15, banner_timeout=15, auth_timeout=15,
                  look_for_keys=False, allow_agent=False)
    except Exception:
        c.close()
        if sock:
            sock.close()
        raise
    return c


def run(c, script, timeout=60):
    i, o, e = c.exec_command("sh -s", timeout=timeout)
    i.write(script)
    i.channel.shutdown_write()
    return {"rc": o.channel.recv_exit_status(),
            "stdout": o.read().decode("utf-8", "replace")[-12000:],
            "stderr": e.read().decode("utf-8", "replace")[-1000:]}


def write_remote(c, path, content, mode=0o755):
    i, o, e = c.exec_command("cat > %s && chmod %o %s" % (path, mode, path), timeout=30)
    i.write(content)
    i.channel.shutdown_write()
    rc = o.channel.recv_exit_status()
    assert rc == 0, "write_remote %s rc=%s" % (path, rc)


def read_remote(c, path):
    i, o, e = c.exec_command("cat %s" % path, timeout=30)
    data = o.read()
    rc = o.channel.recv_exit_status()
    return data if rc == 0 else None


RES = {"started": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
       "controller_invoked": False, "gz_writes": False}
STEP = []


def step(name, r):
    STEP.append({"step": name, "rc": r.get("rc"), "stdout_tail": r.get("stdout", "")[-1500:],
                 "stderr_tail": r.get("stderr", "")[-300:]})
    print("[%s] rc=%s" % (name, r.get("rc")), flush=True)
    if r.get("rc") != 0:
        print(r.get("stdout", "")[-1500:], flush=True)
        print("STDERR:", r.get("stderr", "")[-300:], flush=True)
    return r


meter_src = (SRC / "proxy-meter-v3.sh").read_text(encoding="utf-8").replace("\r\n", "\n")
gen_src = (SRC / "proxy-meter-endpoints.sh").read_text(encoding="utf-8").replace("\r\n", "\n")

gz = connect("134.175.151.213", 28722, "ubuntu", DEV_PW)
r2 = connect("127.0.0.1", 13223, "root", DEV_PW, jump=gz)

# ---------- 现势守门：无 pending/running 任务、心跳正常、无并行会话痕迹 ----------
gate = run(gz, """sudo -u postgres psql -X -A -t routermgmt <<'SQL'
SELECT 'tasks', count(*) FROM tasks WHERE device_id='rtr-a9bcba' AND status IN ('pending','running');
SELECT 'r2ver', current_config_version, highest_accepted_version FROM devices WHERE id='rtr-a9bcba';
SQL
""")
assert "tasks|0" in gate["stdout"], "R2 有未完成任务，可能并行会话操作中: %s" % gate["stdout"]
step("gate_gz", gate)

# ---------- Phase A: /tmp 干跑（不动生产链/生产文件） ----------
write_remote(r2, "/tmp/proxy-meter-v3.sh", meter_src)
write_remote(r2, "/tmp/proxy-meter-endpoints.sh", gen_src)
prev = run(r2, """rm -rf /tmp/pm3-preview; mkdir -p /tmp/pm3-preview
EP_RULES_OUT=/tmp/pm3-preview/rules.conf EP_COVER_OUT=/tmp/pm3-preview/coverage.json /tmp/proxy-meter-endpoints.sh
echo '=== rules ==='; cat /tmp/pm3-preview/rules.conf
echo '=== coverage ==='; cat /tmp/pm3-preview/coverage.json
""")
step("A_generator_preview", prev)
assert prev["rc"] == 0, "generator preview failed"
rules_text = read_remote(r2, "/tmp/pm3-preview/rules.conf").decode()
cov_text = read_remote(r2, "/tmp/pm3-preview/coverage.json").decode()
# R2 v36 已收窄：静态绑定仅 BWG-HY2 / BWG-VLESS / BULK-LAX-GIA（GIA/CF 不在其 config）
need_eps = ["74.82.193.113|udp|34567",     # BWG-HY2
            "74.82.193.113|tcp|443",       # BWG-VLESS
            "154.36.139.155|tcp|2054"]     # BULK GIA
missing = [e for e in need_eps if e not in rules_text]
assert not missing, "预览规则缺静态端点: %s" % missing
n_rules = len([l for l in rules_text.splitlines() if l.strip()])
assert n_rules >= 6, "规则数异常: %d" % n_rules
assert '"unresolved_names":"' in cov_text and len(cov_text) > 60, "机场域名节点未列入未解析名单: %s" % cov_text[:200]
# 干跑 meter：假链（PM_CHAIN_*）→ 不动真链；验证计算/状态/日账路径
dry = run(r2, """mkdir -p /tmp/pm3-preview/st
PM_STATE=/tmp/pm3-preview/st/state.json PM_DAY=/tmp/pm3-preview/st/daily.json \\
PM_LOG=/tmp/pm3-preview/st/meter.log PM_LOCK=/tmp/pm3-preview/st/lock \\
PM_RULES=/tmp/pm3-preview/rules.conf PM_COVER=/tmp/pm3-preview/coverage.json \\
PM_CHAIN_OUT=PM3_DRY_OUT PM_CHAIN_IN=PM3_DRY_IN PM_CHECK_ACTIVE=0 /tmp/proxy-meter-v3.sh; echo rc=$?
cat /tmp/pm3-preview/st/state.json; echo; cat /tmp/pm3-preview/st/daily.json; echo
""")
step("A_meter_dryrun", dry)
assert '"schema":3' in dry["stdout"] and '"seq":1' in dry["stdout"], dry["stdout"][-800:]

# ---------- Phase B: 备份 ----------
LOCAL_BAK.mkdir(parents=True, exist_ok=True)
bak = run(r2, """BK=/etc/routermgmt/backup-meter-v3-20261117
mkdir -p $BK
cp -a /etc/routermgmt/proxy-meter-v2.sh /etc/routermgmt/proxy-meter.sh \\
      /etc/routermgmt/proxy-meter-rules.conf /etc/routermgmt/ratelimit-ctl $BK/ 2>/dev/null
cp -a /etc/routermgmt/state/proxy-meter-v2.json /etc/routermgmt/state/proxy-meter-daily.json $BK/ 2>/dev/null
crontab -l > $BK/crontab.bak-20261117
sha256sum $BK/* | sed 's|$BK/||'
""")
step("B_backup_on_device", bak)
assert bak["rc"] == 0 and "crontab.bak" in bak["stdout"], bak["stdout"][-500:]
for name in ["proxy-meter-v2.sh", "proxy-meter.sh", "proxy-meter-rules.conf", "ratelimit-ctl", "crontab.bak-20261117"]:
    data = read_remote(r2, "/etc/routermgmt/backup-meter-v3-20261117/" + name)
    if data is not None:
        (LOCAL_BAK / name).write_bytes(data)
for name in ["state/proxy-meter-v2.json", "state/proxy-meter-daily.json"]:
    data = read_remote(r2, "/etc/routermgmt/backup-meter-v3-20261117/" + name)
    if data is not None:
        (LOCAL_BAK / Path(name).name).write_bytes(data)
RES["local_backup_dir"] = str(LOCAL_BAK)

# ---------- 安装 + 生成真实规则 + 首跑 ----------
write_remote(r2, "/etc/routermgmt/proxy-meter-v3.sh", meter_src)
write_remote(r2, "/etc/routermgmt/proxy-meter-endpoints.sh", gen_src)
gen = run(r2, "/etc/routermgmt/proxy-meter-endpoints.sh && wc -l /etc/routermgmt/proxy-meter-rules-v3.conf")
step("B_generate_rules", gen)
assert gen["rc"] == 0, gen["stdout"][-500:]
first = run(r2, "/etc/routermgmt/proxy-meter-v3.sh; echo rc=$?; cat /etc/routermgmt/state/proxy-meter-v3.json; echo; cat /etc/routermgmt/state/proxy-meter-daily-v3.json")
step("B_first_run", first)
assert '"schema":3' in first["stdout"] and '"seq":1' in first["stdout"], first["stdout"][-800:]

# ---------- cron 切换（v2 注释保留回退） ----------
cur_cron = read_remote(r2, "/etc/crontabs/root").decode()
assert "/etc/routermgmt/proxy-meter-v2.sh #proxy-meter" in cur_cron, "cron v2 行不在，现场变化，中止"
new_cron = cur_cron.replace(
    "*/5 * * * * /etc/routermgmt/proxy-meter-v2.sh #proxy-meter",
    "# 20261117 v3 迁移: v2 停用保留回退\n#*/5 * * * * /etc/routermgmt/proxy-meter-v2.sh #proxy-meter\n*/5 * * * * /etc/routermgmt/proxy-meter-v3.sh #proxy-meter")
write_remote(r2, "/tmp/newcrontab", new_cron, mode=0o644)
cron = run(r2, "crontab /tmp/newcrontab && crontab -l")
step("B_cron_switch", cron)
assert "proxy-meter-v3.sh" in cron["stdout"] and cron["stdout"].count("proxy-meter-v2.sh") >= 1, cron["stdout"]

# ---------- 危险 v1 控制器隔离（重验无引用后 rename+chmod 000，可逆） ----------
refs = run(r2, """grep -rn "ratelimit-ctl" /etc/crontabs/ /etc/rc.local /etc/init.d/ /etc/routermgmt/handlers/ /etc/routermgmt/routermgmt-agent.sh /etc/routermgmt/self-heal.sh /etc/routermgmt/mgmt-port-discovery.sh 2>/dev/null || echo no-refs
ps | grep ratelimit | grep -v grep || echo no-process
""")
step("B_controller_refs_recheck", refs)
assert "no-refs" in refs["stdout"] and "no-process" in refs["stdout"], refs["stdout"][:500]
contain = run(r2, """mv /etc/routermgmt/ratelimit-ctl /etc/routermgmt/ratelimit-ctl.disabled-20261117
chmod 000 /etc/routermgmt/ratelimit-ctl.disabled-20261117
ls -la /etc/routermgmt/ratelimit-ctl* 2>/dev/null
""")
step("B_controller_contained", contain)
assert contain["rc"] == 0, contain["stdout"][-300:]

# ---------- 立即后检：组未动、agent 心跳、干跑垃圾清理 ----------
post = run(r2, """rm -rf /tmp/pm3-preview /tmp/proxy-meter-v3.sh /tmp/proxy-meter-endpoints.sh /tmp/newcrontab
for g in PROXY US-MEDIA NODE-PICK AUTO; do
  printf '%s=' "$g"; curl --noproxy '*' -sS -m 5 http://127.0.0.1:9090/proxies/$g | jsonfilter -e '@.now' || echo unreachable
done
ps | grep -c "[r]outermgmt-agent.sh"
iptables -S PROXY_METER_OUT | wc -l; iptables -S PROXY_METER_IN | wc -l
""")
step("B_post_check_r2", post)
hb = run(gz, """sudo -u postgres psql -X -A -t routermgmt -c "SELECT last_heartbeat_at, agent_version FROM devices WHERE id='rtr-a9bcba';"
""")
step("B_post_check_gz_hb", hb)

RES["steps"] = STEP
RES["first_state"] = first["stdout"][-1000:]
RES["finished_phase_b"] = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
r2.close()
gz.close()
OUT.write_text(json.dumps(RES, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("PHASE_B_DONE — 等待 cron 周期后运行 r2_meter_v3_verify.py")
print(json.dumps({"cron": cron["stdout"], "first_state": first["stdout"][:600]}, ensure_ascii=False, indent=1))
