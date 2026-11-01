#!/bin/sh
# fleet-inspect v1 —— 标准巡检片段（来源：Codex 复核 2026-11-01，review/codex-fix-verify-20261101.md）
# 用法：经排灾口将本文件推入设备后 `sh /tmp/fleet-inspect.sh`；采集失败=UNKNOWN，单次失败不判设备故障。
# 原则：不输出凭据；CR 用 od 字节计数（避开 ash ANSI-C 陷阱）；mihomo -t 必须用运行中进程的实际 -d/-f。
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
