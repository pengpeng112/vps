#!/bin/sh
# ratelimit-server-executor — 承载服务器侧"设备回复腿"整形执行器（部署后运行于 GIA/BWG）。
# 本批次只交付离线可测实现；未经独立 SOP/授权不部署、不在生产调用。
#
# 设计约束（review/R2准备报告再次核查与一次性整改交接单-20261117-Codex.md §2）：
#   - 只处理已注册 device+class 的"服务器→设备"下行回复腿；上传/SSH/管理/医院/网页不限。
#   - 附加式对象：clsact qdisc 与既有 root(fq 等)共存，绝不 del root/替换共享队列（G11）。
#   - police 按目的 IP=设备 WAN IP + 类别端口匹配：同类多连接共享同一 60kbit/s（G09），
#     两类各自独立 policer；独立 manifest/预算/恢复，与设备端控制器互不删对方对象。
#   - 幂等（job_id）、精确对象解除（pref+handle）、状态可审计；无后台循环。
#
# 输入（stdin JSON）：{"op":"apply|release","device_id":..,"cls":"video|download",
#   "bits_per_second":60000,"device_wan_ip":"1.2.3.4","job":"<job_id>"}
# 输出（stdout JSON）：{"ok":true|false,"job_id":..,"effective":true|false,"evidence":..}
# 环境覆盖（隔离测试）：RS_BASE RS_CFG RS_TC_BIN RS_DEV
# 退出码：0 ok；2 usage/bad input；5 config/param 错；6 冲突（对象已存在/未注册）；7 内核执行失败
set -u
BASE=${RS_BASE:-/etc/routermgmt/ratelimit-server}
CFG=${RS_CFG:-$BASE/config.json}
TC=${RS_TC_BIN:-tc}
DEV=${RS_DEV:-eth0}
MAN=$BASE/manifests.json
mkdir -p "$BASE" 2>/dev/null

usage() {
  cat <<EOF
ratelimit-server-executor — usage: stdin JSON op=apply|release
config: $CFG  {"classes":{"video":{"ports":[2054],"proto":"tcp"},"download":{...}}}
objects: clsact(共享附加) + egress u32/police filter（pref 9000+job 序号，均记入 $MAN）
safety: 永不替换/删除 root qdisc；只删 manifest 自有 filter；无守护进程。
EOF
  exit 2
}

reply() { # $1=ok $2=job_id $3=effective $4=evidence → JSON
  python3 -c 'import json,sys; print(json.dumps({"ok": sys.argv[1]=="1", "job_id": sys.argv[2], "effective": sys.argv[3]=="1", "evidence": sys.argv[4]}))' "$1" "$2" "$3" "$4"
  exit 0
}
fail() { python3 -c 'import json,sys; print(json.dumps({"ok": False, "job_id": sys.argv[2], "effective": False, "error": sys.argv[1]}))' "$1" "${2:-}"; exit "${3:-5}"; }

# ---- 读 stdin 载荷 + config，生成执行计划（全部校验先行；任何不明确即拒绝，不猜） ----
PAYLOAD=$(cat)
PLAN=$(python3 - "$CFG" "$DEV" "$PAYLOAD" <<'PYEOF'
import json, sys
try: p = json.loads(sys.argv[3])
except Exception as e: print(json.dumps({"err": "bad_json:%s" % e})); raise SystemExit
try: cfg = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception: print(json.dumps({"err": "config_unreadable"})); raise SystemExit
op = p.get("op")
if op not in ("apply", "release"): print(json.dumps({"err": "bad_op"})); raise SystemExit
cls = p.get("cls")
cc = (cfg.get("classes") or {}).get(cls)
if op == "apply":
    if not cc: print(json.dumps({"err": "class_not_registered:%s" % cls})); raise SystemExit
    wan = p.get("device_wan_ip")
    if not wan: print(json.dumps({"err": "device_wan_ip_missing"})); raise SystemExit
    bps = p.get("bits_per_second")
    if not isinstance(bps, int) or bps <= 0 or bps > 10**12: print(json.dumps({"err": "bad_bps"})); raise SystemExit
    ports = cc.get("ports") or []
    if not ports: print(json.dumps({"err": "no_ports_for_class:%s" % cls})); raise SystemExit
    dev_id = p.get("device_id") or "?"
    job = "sv-%s-%s-%s" % (dev_id, cls, abs(hash((wan, cls, bps, tuple(ports)))) % 10**8)
    burst = max(4096, min(bps // 8, 262144))
    print(json.dumps({"op": "apply", "job": job, "cls": cls, "wan": wan, "bps": bps,
                      "proto": cc.get("proto", "tcp"), "ports": ports, "burst": burst}))
else:
    job = p.get("job")
    if not job: print(json.dumps({"err": "job_missing"})); raise SystemExit
    print(json.dumps({"op": "release", "job": job}))
PYEOF
)
ERR=$(printf '%s' "$PLAN" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("err",""))' 2>/dev/null)
[ -n "$ERR" ] && fail "$ERR" "" 2

OP=$(printf '%s' "$PLAN" | python3 -c 'import json,sys; print(json.load(sys.stdin)["op"])')
JOB=$(printf '%s' "$PLAN" | python3 -c 'import json,sys; print(json.load(sys.stdin)["job"])')

man_load() { cat "$MAN" 2>/dev/null || echo '{}'; }
man_save() {
  printf '%s\n' "$1" > "$MAN.tmp" && mv "$MAN.tmp" "$MAN" \
    && python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$MAN" 2>/dev/null \
    || fail "manifest_persist_failed" "$JOB" 7
}

if [ "$OP" = "apply" ]; then
  # 幂等：同 job 已存在 → 直接回 ok（effective 以 manifest 为证）
  EXIST=$(python3 -c 'import json,sys; m=json.load(open(sys.argv[1])); print("1" if sys.argv[2] in m else "0")' "$MAN" "$JOB" 2>/dev/null || echo 0)
  if [ "$EXIST" = "1" ]; then reply 1 "$JOB" 1 "idempotent: already in manifest"; fi
  WAN=$(printf '%s' "$PLAN" | python3 -c 'import json,sys; print(json.load(sys.stdin)["wan"])')
  BPS=$(printf '%s' "$PLAN" | python3 -c 'import json,sys; print(json.load(sys.stdin)["bps"])')
  PROTO=$(printf '%s' "$PLAN" | python3 -c 'import json,sys; print(json.load(sys.stdin)["proto"])')
  BURST=$(printf '%s' "$PLAN" | python3 -c 'import json,sys; print(json.load(sys.stdin)["burst"])')
  PORTS=$(printf '%s' "$PLAN" | python3 -c 'import json,sys; print(",".join(str(x) for x in json.load(sys.stdin)["ports"]))')
  PREF=$(printf '%s' "$JOB" | tr -d 'a-z-' | cut -c1-4)
  [ -n "$PREF" ] || PREF=9100
  PREF=$((9000 + PREF % 900))
  # 1) 确保附加 clsact（与任何既有 root 共存；已存在不报错）
  $TC qdisc replace dev "$DEV" clsact 2>/dev/null || fail "clsact_add_failed" "$JOB" 7
  # 2) egress police filter：dst=设备WAN IP（同类所有连接共享同一 policer）
  MATCHES="match ip dst $WAN/32"
  IFS=","
  for pt in $PORTS; do
    MATCHES="$MATCHES match ip dport $pt 0xffff"
  done
  unset IFS
  # shellcheck disable=SC2086
  $TC filter add dev "$DEV" egress protocol ip pref "$PREF" u32 $MATCHES \
    action police rate "$BPS"bit burst "$BURST" conform-exceed drop/pipe index "$PREF" 2>/dev/null \
    || fail "filter_add_failed" "$JOB" 7
  NEWMAN=$(python3 - "$MAN" "$JOB" "$DEV" "$WAN" "$BPS" "$PREF" "$PROTO" "$PORTS" "$(date '+%F %T%z')" <<'PYM'
import json, sys
m = {}
try: m = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception: pass
m[sys.argv[2]] = {"dev": sys.argv[3], "wan": sys.argv[4], "bps": int(sys.argv[5]), "pref": int(sys.argv[6]),
                  "proto": sys.argv[7], "ports": sys.argv[8].split(","), "at": sys.argv[9]}
print(json.dumps(m))
PYM
)
  man_save "$NEWMAN"
  reply 1 "$JOB" 1 "clsact egress pref=$PREF police rate=${BPS}bit/s dst=$WAN ports=$PORTS"
else
  REC=$(python3 -c 'import json,sys; m=json.load(open(sys.argv[1])); print(json.dumps(m.get(sys.argv[2],"")))' "$MAN" "$JOB" 2>/dev/null)
  [ "$REC" = '""' ] && fail "job_not_in_manifest" "$JOB" 6
  PREF=$(printf '%s' "$REC" | python3 -c 'import json,sys; print(json.load(sys.stdin)["pref"])')
  # 只删自有 pref 的 filter；clsact 可能仍被他人使用，不删 qdisc 本体
  $TC filter del dev "$DEV" egress pref "$PREF" u32 2>/dev/null || fail "filter_del_failed" "$JOB" 7
  NEWMAN=$(python3 - "$MAN" "$JOB" <<'PYM'
import json, sys
m = json.load(open(sys.argv[1], encoding="utf-8"))
m.pop(sys.argv[2], None)
print(json.dumps(m))
PYM
)
  man_save "$NEWMAN"
  reply 1 "$JOB" 0 "released filter pref=$PREF"
fi
