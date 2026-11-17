#!/bin/sh
# ratelimit-ctl v1 (R2) - local rate-limit controller for proxy_ratelimit_v2.
# Subcommands:
#   status
#   preview <policy.json>
#   apply <policy.json> [--expected-version N] [--dry-run]
#   release <video|download|all> <manual|exempt|emergency> <reason...>
#   emergency-stop
# Default is dry-run for apply (no kernel/group changes) unless --enforce given.
# State: /etc/routermgmt/ratelimit/  (policy.json, policy.version, audit.jsonl, latch.json)
set -u
BASE=F:/vps/review/r2-qos-report-fixtures-20261117/controller
POLICY=$BASE/policy.json
VERSION=$BASE/policy.version
AUDIT=$BASE/audit.jsonl
LATCH=$BASE/latch.json
DAILY=F:/vps/review/r2-qos-report-fixtures-20261117/controller-daily.json
API=http://127.0.0.1:9090
TC_DEV=wan
ENDPOINT_IPS="154.36.139.155 74.82.193.113"
BULK_NODE="BULK-LAX-GIA-01"
NORMAL_VIDEO="NODE-PICK"
mkdir -p "$BASE"

audit() { # $1=action $2=detail(json-ish)
  printf '{"ts":"%s","action":"%s","detail":%s}\n' "$(date '+%F %T')" "$1" "$2" >> "$AUDIT"
}
cur_ver() { cat "$VERSION" 2>/dev/null || echo 0; }
usage() { echo "usage: ratelimit-ctl.sh status|preview <file>|apply <file> [--enforce] [--expected-version N]|release <scope> <mode> <reason...>|emergency-stop"; exit 2; }

validate_policy() { # $1=file ; echoes 0 ok / 1 bad with reason on stderr
  python3 - "$1" <<'PYEOF' 2>&1
import json, sys
try:
    p = json.load(open(sys.argv[1]))
except Exception as e:
    print("bad_json:", e); sys.exit(1)
errs = []
if p.get("schema") != 2: errs.append("schema!=2")
enf = p.get("enforcement")
if enf not in ("observe","limited","paused","exempt"): errs.append("bad_enforcement:"+str(enf))
sc = p.get("scope")
if sc not in ("download_video","video","download"): errs.append("bad_scope:"+str(sc))
q = p.get("quota", {})
for k in ("warn_gib","action_gib","hard_gib"):
    v = q.get(k)
    if not isinstance(v,(int,float)) or v <= 0: errs.append("bad_quota_"+k)
if not (q.get("warn_gib",0) < q.get("action_gib",0) < q.get("hard_gib",0)):
    errs.append("quota_order_bad")
a = p.get("actions", {})
for cls in ("video","download"):
    act = a.get(cls, {})
    m = act.get("mode")
    if m not in ("shape","unchanged","paused"): errs.append("bad_mode_"+cls)
    if m == "shape":
        d = act.get("down", {})
        if not (isinstance(d.get("value"),int) and d.get("value") > 0): errs.append("bad_rate_"+cls)
        if d.get("unit") != "kbps": errs.append("bad_unit_"+cls+":"+str(d.get("unit")))
if p.get("exempt") is True and enf != "exempt": errs.append("exempt_needs_exempt_mode")
if errs:
    print("invalid: "+"; ".join(errs)); sys.exit(1)
print("valid")
PYEOF
}

bytes_today() {
  python3 -c "
import json
try: print(int(json.load(open('$DAILY')).get('total',0)))
except Exception: print(0)"
}

tc_apply_htb() { # $1=rate_kbit
  tc qdisc del dev $TC_DEV root 2>/dev/null || true
  tc qdisc add dev $TC_DEV root handle 1: htb default 30 || return 1
  tc class add dev $TC_DEV parent 1: classid 1:10 htb rate ${1}kbit ceil ${1}kbit || return 1
  for ip in $ENDPOINT_IPS; do
    tc filter add dev $TC_DEV parent 1: protocol ip u32 match ip dst $ip/32 flowid 1:10 || return 1
  done
  return 0
}
tc_release() {
  tc qdisc del dev $TC_DEV root 2>/dev/null || true
}
set_usmedia() { # $1=node
  curl --noproxy '*' -sS -m 10 -X PUT -H 'Content-Type: application/json' \
    --data "{\"name\":\"$1\"}" -o /dev/null -w '%{http_code}' "$API/proxies/US-MEDIA"
}

[ -f F:/vps/review/r2-qos-report-fixtures-20261117/agent.conf ] || { echo "not on managed device"; exit 21; }
CMD=${1:-status}
case "$CMD" in
  status)
    echo "policy_version=$(cur_ver)"
    echo "policy=$(cat $POLICY 2>/dev/null || echo none)"
    echo "latch=$(cat $LATCH 2>/dev/null || echo none)"
    echo "bytes_today=$(bytes_today)"
    echo "tc=$(tc qdisc show dev $TC_DEV 2>/dev/null | head -1)"
    for g in PROXY US-MEDIA NODE-PICK; do
      echo "$g now=$(curl --noproxy '*' -fsS -m 5 $API/proxies/$g 2>/dev/null | jsonfilter -e '@.now')"
    done
    echo "audit_tail=$(tail -1 $AUDIT 2>/dev/null || echo none)"
    ;;
  preview)
    [ -f "${2:-}" ] || usage
    v=$(validate_policy "$2")
    echo "policy_validation=$v"
    if [ "$v" != "valid" ]; then exit 1; fi
    echo "dry-run classification:"
    python3 - "$2" <<'PYEOF'
import json, sys
p = json.load(open(sys.argv[1]))
for cls in ("video","download"):
    act = p.get("actions", {}).get(cls, {})
    if act.get("mode") == "shape":
        print(f"  {cls}: shape down={act['down']['value']}{act['down']['unit']} (=> {act['down']['value']*1000} bit/s)")
    else:
        print(f"  {cls}: mode={act.get('mode')}")
print(f"  web: unchanged")
print(f"  trigger: daily_total >= {p.get('quota',{}).get('action_gib')}GiB")
PYEOF
    echo "NO_CHANGES_APPLIED (preview only)"
    ;;
  apply)
    shift
    ENFORCE=0; EXPECTED=""
    POLICY_FILE=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --enforce) ENFORCE=1 ;;
        --dry-run) ENFORCE=0 ;;
        --expected-version) EXPECTED=${2:-}; shift ;;
        -*) echo "unknown flag $1"; exit 2 ;;
        *) POLICY_FILE=$1 ;;
      esac
      shift
    done
    [ -n "$POLICY_FILE" ] && [ -f "$POLICY_FILE" ] || usage
    v=$(validate_policy "$POLICY_FILE")
    echo "policy_validation=$v"
    [ "$v" = "valid" ] || exit 1
    CV=$(cur_ver)
    if [ -n "$EXPECTED" ] && [ "$EXPECTED" != "$CV" ]; then
      echo "CAS_REJECT expected=$EXPECTED current=$CV"; exit 3
    fi
    NEWVER=$((CV + 1))
    ENF=$(python3 -c "import json;print(json.load(open('$POLICY_FILE')).get('enforcement'))")
    LATCH_ENF=$(python3 -c "import json;print(json.load(open('$LATCH')).get('enforcement',''))" 2>/dev/null || echo "")
    if [ "$LATCH_ENF" = "emergency" ] || [ "$LATCH_ENF" = "exempt" ]; then
      echo "LATCHED device is $LATCH_ENF; use release/resume first (latch=$LATCH)"; exit 4
    fi
    if [ "$ENFORCE" != "1" ]; then
      echo "DRY_RUN (no kernel/group changes). Re-run with --enforce to commit policy_version=$NEWVER"
      exit 0
    fi
    case "$ENF" in
      observe)
        tc_release
        set_usmedia "$NORMAL_VIDEO" >/dev/null
        ;;
      limited)
        RATE=$(python3 -c "import json;p=json.load(open('$POLICY_FILE'));a=p.get('actions',{});r=[]
for c in ('video','download'):
    act=a.get(c,{})
    if act.get('mode')=='shape': r.append(int(act.get('down',{}).get('bits_per_second',0)))
print(min([x for x in r if x>0] or [0])//1000)")
        [ "$RATE" -gt 0 ] || RATE=60
        tc_apply_htb "$RATE" || { echo "TC_APPLY_FAILED"; tc_release; exit 5; }
        set_usmedia "$BULK_NODE" >/dev/null
        ;;
      paused)
        tc_release
        set_usmedia "$NORMAL_VIDEO" >/dev/null
        ;;
      exempt)
        tc_release
        ;;
    esac
    cp "$POLICY_FILE" "$POLICY.tmp" && mv "$POLICY.tmp" "$POLICY"
    echo "$NEWVER" > "$VERSION"
    audit "apply" "{\"version\":$NEWVER,\"enforcement\":\"$ENF\",\"enforced\":$ENFORCE,\"rate_kbit\":\"$RATE\"}"
    echo "APPLIED version=$NEWVER enforcement=$ENF enforced=$ENFORCE"
    ;;
  release)
    SCOPE=${2:-}; MODE=${3:-}; shift 3 2>/dev/null
    REASON=${*:-unspecified}
    case "$SCOPE" in video|download|all) ;; *) usage ;; esac
    case "$MODE" in manual|exempt|emergency) ;; *) usage ;; esac
    # actions: remove local shaping; return video group to normal path
    tc_release
    set_usmedia "$NORMAL_VIDEO" >/dev/null
    GEN=$(python3 -c "
import json
try: l=json.load(open('$LATCH')); print(int(l.get('release_generation',0))+1)
except Exception: print(1)")
    printf '{"enforcement":"observe","release_generation":%d,"scope":"%s","mode":"%s","reason":"%s","released_at":"%s"}\n' \
      "$GEN" "$SCOPE" "$MODE" "$REASON" "$(date '+%F %T')" > "$LATCH"
    audit "release" "{\"scope\":\"$SCOPE\",\"mode\":\"$MODE\",\"reason\":\"$REASON\",\"generation\":$GEN}"
    echo "RELEASED scope=$SCOPE mode=$MODE generation=$GEN (state=observe)"
    ;;
  emergency-stop)
    tc_release
    set_usmedia "$NORMAL_VIDEO" >/dev/null
    GEN=$(python3 -c "
import json
try: l=json.load(open('$LATCH')); print(int(l.get('release_generation',0))+1)
except Exception: print(1)")
    printf '{"enforcement":"observe","release_generation":%d,"mode":"emergency","released_at":"%s"}\n' \
      "$GEN" "$(date '+%F %T')" > "$LATCH"
    audit "emergency_stop" "{\"generation\":$GEN}"
    echo "EMERGENCY_STOP done (generation=$GEN, all local shaping removed)"
    ;;
  *) usage ;;
esac
