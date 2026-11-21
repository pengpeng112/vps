#!/bin/sh
# Reference for the next executor. Default mode is read-only CHECK.
# Execute via Paramiko direct-tcpip and sh -s -- DEVICE_ID MODE EXPECTED_SHA.
# Do not deploy or run apply without the user's maintenance authorization.
set -eu

expected_id=${1:-}
mode=${2:-check}
expected_sha=${3:-}
case "$expected_id" in rtr-*) ;; *) echo 'ERROR expected device ID required'; exit 20 ;; esac
case "$expected_id" in *[!a-z0-9-]*) echo 'ERROR invalid device ID'; exit 20 ;; esac
case "$mode" in check|apply) ;; *) echo 'ERROR mode must be check or apply'; exit 20 ;; esac

conf=/etc/routermgmt/agent.conf
actual_id=$(awk -F= '$1=="DEVICE_ID" {print $2; exit}' "$conf" | tr -d '\r\042\047')
[ "$actual_id" = "$expected_id" ] || { echo 'ERROR wrong device identity'; exit 21; }
[ -z "$(uci changes dhcp)" ] || { echo 'ERROR uncommitted DHCP changes'; exit 22; }
original_sha=$(sha256sum /etc/config/dhcp | awk '{print $1}')
servers=$(uci -q get dhcp.@dnsmasq[0].server || true)
case " $servers " in *' 127.0.0.1#1053 '*) ;; *) echo 'ERROR unexpected default DNS upstream'; exit 23 ;; esac

domains='lol.qq.com gamesafe.qq.com anticheatexpert.com ied-tqos-tgp.qq.com ied-tqos.qq.com'
upstreams='223.5.5.5 119.29.29.29'
missing=0
for domain in $domains; do
    for upstream in $upstreams; do
        entry="/$domain/$upstream"
        case " $servers " in *" $entry "*) ;; *) missing=$((missing+1)) ;; esac
    done
done
printf 'DEVICE=%s MODE=%s DHCP_SHA=%s MISSING_RULES=%s\n' "$actual_id" "$mode" "$original_sha" "$missing"

addresses() {
    nslookup "$1" 127.0.0.1 2>/dev/null | awk '
      /^Name[ \t]*:/ { answer=1; next }
      answer && /^Address/ {
        for (i=1;i<=NF;i++) if ($i ~ /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/) print $i
      }'
}
check_answers() {
    bad=0
    for host in gpp-tj100-1.lol.qq.com tqos.gamesafe.qq.com tqos.anticheatexpert.com ied-tqos-tgp.qq.com ied-tqos.qq.com; do
        ips=$(addresses "$host" || true)
        printf 'DNS %s %s\n' "$host" "${ips:-NO_ANSWER}"
        [ -n "$ips" ] || bad=1
        if printf '%s\n' "$ips" | grep -q '^198\.18\.'; then bad=1; fi
    done
    [ "$bad" -eq 0 ]
}

if [ "$mode" = check ]; then
    if check_answers; then echo 'CHECK_REAL_DNS_OK'; else echo 'CHECK_NEEDS_DNS_FIX'; fi
    exit 0
fi

[ "${#expected_sha}" -eq 64 ] || { echo 'ERROR apply requires expected SHA-256'; exit 24; }
case "$expected_sha" in *[!0-9a-f]*) echo 'ERROR invalid expected SHA-256'; exit 24 ;; esac
[ "$original_sha" = "$expected_sha" ] || { echo 'ERROR concurrent DHCP change'; exit 25; }
if [ "$missing" -eq 0 ]; then
    check_answers || { echo 'ERROR rules already present but DNS verification failed'; exit 26; }
    echo 'ALREADY_APPLIED_NO_RELOAD'
    exit 0
fi

dns_pid=$(pidof dnsmasq | awk '{print $1}')
[ -n "$dns_pid" ] || { echo 'ERROR dnsmasq not running'; exit 27; }
generated=$(tr '\000' '\n' < "/proc/$dns_pid/cmdline" | awk 'previous=="-C" {print; exit} {previous=$0}')
[ -f "$generated" ] || { echo 'ERROR cannot identify generated dnsmasq config'; exit 27; }
set -- --test -C "$generated"
for domain in $domains; do
    for upstream in $upstreams; do set -- "$@" "--server=/$domain/$upstream"; done
done
dnsmasq "$@"

lock=/tmp/codex-game-dns.lock
mkdir "$lock" 2>/dev/null || { echo 'ERROR another DNS maintenance lock exists'; exit 28; }
stamp=$(date '+%Y%m%d-%H%M%S')
backup="/etc/config/dhcp.bak-game-dns-$actual_id-$stamp"
[ ! -e "$backup" ] || { rmdir "$lock"; echo 'ERROR backup already exists'; exit 29; }
cp -p /etc/config/dhcp "$backup" || { rmdir "$lock"; exit 29; }
protected_hashes() {
    for file in /etc/config/wireless /etc/mihomo/config.yaml /etc/mihomo/run.yaml /etc/routermgmt/tunnel.conf /etc/routermgmt/self-heal.sh; do
        [ ! -f "$file" ] || sha256sum "$file"
    done
}
protected_before=$(protected_hashes)
committed=0
finish() {
    result=$?
    trap - EXIT HUP INT TERM
    if [ "$committed" -ne 1 ]; then
        uci revert dhcp 2>/dev/null || true
        rollback_ok=1
        if cp -p "$backup" /etc/config/dhcp; then
            /etc/init.d/dnsmasq reload || rollback_ok=0
        else
            rollback_ok=0
        fi
        restored_sha=$(sha256sum /etc/config/dhcp 2>/dev/null | awk '{print $1}')
        [ "$restored_sha" = "$original_sha" ] || rollback_ok=0
        if [ "$rollback_ok" -eq 1 ]; then
            echo 'ROLLED_BACK_DHCP'
        else
            printf 'ERROR_ROLLBACK_FAILED_MANUAL_RECOVERY_REQUIRED BACKUP=%s\n' "$backup"
            result=91
        fi
        [ "$result" -ne 0 ] || result=1
    fi
    rmdir "$lock" 2>/dev/null || true
    exit "$result"
}
trap finish EXIT
trap 'exit 130' HUP INT TERM

# Recheck immediately before the first mutation.
[ "$(sha256sum /etc/config/dhcp | awk '{print $1}')" = "$expected_sha" ]
[ -z "$(uci changes dhcp)" ]
for domain in $domains; do
    for upstream in $upstreams; do
        entry="/$domain/$upstream"
        case " $servers " in
            *" $entry "*) ;;
            *) uci add_list "dhcp.@dnsmasq[0].server=$entry" ;;
        esac
    done
done
uci commit dhcp
/etc/init.d/dnsmasq reload
sleep 3
dnsmasq --test -C "$generated"
check_answers
[ -z "$(uci changes dhcp)" ]
[ "$(protected_hashes)" = "$protected_before" ]
health=$(curl --noproxy '*' -sk -m 5 -o /dev/null -w '%{http_code}' https://127.0.0.1:18443/api/v1/health)
[ "$health" = 200 ] || { echo 'ERROR management health verification failed'; exit 30; }
new_sha=$(sha256sum /etc/config/dhcp | awk '{print $1}')
committed=1
printf 'APPLIED BACKUP=%s BEFORE_SHA=%s AFTER_SHA=%s HEALTH=%s\n' "$backup" "$original_sha" "$new_sha" "$health"
