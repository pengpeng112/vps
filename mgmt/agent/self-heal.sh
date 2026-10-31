#!/bin/sh
# self-heal.sh v3.1 - safe version (2026-10-30 v3.1 fleet patch)
# Runs from cron every minute. Only restarts dead services, never reboots.
# v3.1 changes (R6 回滚事故复盘 2026-10-30 晚):
#   - pidof 检测在本平台不可靠（agent 以 /bin/sh 运行，进程名是 sh）——误判会在
#     配置应用中途重启 agent 导致事务回滚（R6 实证）。改用 ps + /proc/cmdline 双检。
#   - 应用事务进行中（state/txn/*/uncommitted 存在）绝不重启 agent。
#   - 18443 不通但心跳文件新鲜（10 分钟内）= 平台侧问题而非 agent 死，跳过重启。
# v3 changes (field test on R4):
#   - port-based detection instead of ps grep (ps unreliable on this BusyBox)
#   - mihomo: check port 9090 API responding
#   - agent: check port 18443 management responding (tunnel alive = agent alive)
# v2 changes (Codex review):
#   - removed auto-reboot (midnight cron is the fallback)
#   - removed tunnel restart (preserves rssh rescue channel)
#   - mkdir-based atomic lock
#   - 300s cooldown per service (prevents crash-restart loops)
#   - suppress during maintenance window (touch /tmp/selfheal.pause)

# Maintenance suppression
[ -f /tmp/selfheal.pause ] && exit 0

# Atomic lock via mkdir
LOCK=/tmp/selfheal.lock.d
mkdir "$LOCK" 2>/dev/null || {
    _now=$(date +%s)
    _created=$(cat "$LOCK/ts" 2>/dev/null || echo 0)
    _diff=$((_now - _created))
    [ "$_diff" -lt 180 ] && exit 0
    rm -rf "$LOCK"
    mkdir "$LOCK" 2>/dev/null || exit 0
}
echo "$(date +%s)" > "$LOCK/ts"
trap 'rm -rf "$LOCK"' EXIT

# 1) mihomo dead? Check API port 9090 responding (not ps grep)
if ! curl -s -m 3 -o /dev/null -w '' http://127.0.0.1:9090/version 2>/dev/null; then
    _cd=/tmp/selfheal.mihomo.cd
    _now=$(date +%s)
    _last=$(cat $_cd 2>/dev/null || echo 0)
    if [ $(($_now - _last)) -ge 300 ]; then
        echo $_now > $_cd
        logger -t self-heal "mihomo API not responding, restarting"
        /etc/init.d/mihomo restart 2>/dev/null
    fi
fi

# 2) agent dead? (v3.1: 可靠进程检测 + 事务保护 + 心跳新鲜度)
_agent_dead=0
if ! ps w 2>/dev/null | grep -v grep | grep -q "routermgmt-agent"; then
    _found=0
    for _d in /proc/[0-9]*/cmdline; do
        grep -q "routermgmt-agent" "$_d" 2>/dev/null && { _found=1; break; }
    done
    [ "$_found" = "0" ] && { _agent_dead=1; _reason="process dead"; }
elif ! curl -sk -m 3 -o /dev/null https://127.0.0.1:18443/api/v1/health 2>/dev/null; then
    if [ -z "$(find /etc/routermgmt/state/hb.json -mmin -10 2>/dev/null)" ]; then
        _agent_dead=1
        _reason="mgmt port not responding and heartbeat stale"
    fi
fi
if [ "$_agent_dead" = "1" ]; then
    if [ -n "$(ls /etc/routermgmt/state/txn/*/uncommitted 2>/dev/null)" ]; then
        logger -t self-heal "apply txn in progress, skip agent restart"
    else
        _cd=/tmp/selfheal.agent.cd
        _now=$(date +%s)
        _last=$(cat $_cd 2>/dev/null || echo 0)
        if [ $(($_now - _last)) -ge 300 ]; then
            echo $_now > $_cd
            logger -t self-heal "agent ${_reason}, restarting"
            /etc/init.d/routermgmt restart 2>/dev/null
        fi
    fi
fi

# NOTE: No tunnel restart (preserves rssh rescue channel).
# No auto-reboot (midnight 00:00 cron is the fallback).

exit 0
