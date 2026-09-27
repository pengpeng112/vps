#!/bin/sh
# netprobe.sh v3 — 链路质量探针（每5分钟 cron，2026-09-27 统一优化 M04 升级）
#   1) ICMP 三目标：国内基线 / DO-VPS / 腾讯SG —— 丢包与 RTT（线路层）
#   2) http_proxy  ：经本机 mihomo(7890) 访问 google.com（按分流走节点）
#   3) http_gstatic：经 mihomo 访问 gstatic.com（2026-09-27 起规则已改为 PROXY，
#      此行与 http_proxy 同为代理路径；注释不再声称记录直连路径）
# v3 变化（对齐网络计划 N05 §7.1，保持每轮 5 行——collect_diag tail -12 语义不变）：
#   - 每行带 run=<epoch><pid>（区分重复/手工首跑）、ts=<epoch>（免时区歧义）
#   - ICMP：sent/recv 逐项记录；ping 不可用/无输出记 state=unknown，不再伪装 loss=100
#   - HTTP：记录 curl_exit 分层归因（6=DNS 7=连接 28=超时 35=TLS 60=证书）与
#     dns/tcp/tls/total 分段耗时；status 只在拿到 2xx 时为 ok
#   - http 行带 sel=<NODE-PICK 当前选择>，切节点可追溯
#   - 互斥锁防重入；保留 7 整天（10800/10080 行滚动）
# busybox 兼容：不用 timeout/xxd/互斥用 mkdir；超时靠 ping -w 与 curl -m/-w。
LOG=/etc/routermgmt/logs/netprobe.log
LOCK=/tmp/netprobe.lock
mkdir -p /etc/routermgmt/logs

# 互斥：上一轮未结束则记一行并退出（一行异常标记，正常轮仍为 5 行）
if ! mkdir "$LOCK" 2>/dev/null; then
    echo "$(date '+%F %T') ts=$(date +%s) probe_overlap_skip" >> "$LOG"
    exit 0
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT INT TERM

RUN="$(date +%s)$$"
TS() { printf '%s ts=%s run=%s' "$(date '+%F %T')" "$(date +%s)" "$RUN"; }
UPT=$(cut -d. -f1 /proc/uptime 2>/dev/null || echo 0)

probe() { # $1=名称 $2=目标IP（ICMP）
    _out=$(ping -c 20 -q -w 25 "$2" 2>/dev/null)
    if [ -z "$_out" ]; then
        echo "$(TS) up=${UPT} icmp_$1 target=$2 state=unknown sent=- recv=- loss=- avg=-" >> "$LOG"
        return
    fi
    _tx=$(echo "$_out" | sed -n 's/^\([0-9]*\) packets transmitted.*/\1/p')
    _rx=$(echo "$_out" | sed -n 's/.*transmitted, \([0-9]*\).*/\1/p')
    _loss=$(echo "$_out" | sed -n 's/.* \([0-9]*\)% packet loss.*/\1/p')
    _rtt=$(echo "$_out" | sed -n 's#.*= [0-9.]*/\([0-9.]*\)/.*#\1#p')
    [ -n "$_tx" ] || _tx=0
    [ -n "$_rx" ] || _rx=0
    [ -n "$_loss" ] || _loss=unknown
    [ -n "$_rtt" ] || _rtt=0
    echo "$(TS) up=${UPT} icmp_$1 target=$2 state=ok sent=$_tx recv=$_rx loss=${_loss}% avg=${_rtt}ms" >> "$LOG"
}

SEL=""
_sel=$(curl -fsS -m 3 http://127.0.0.1:9090/proxies/NODE-PICK 2>/dev/null | sed -n 's/.*"now":"\([^"]*\)".*/\1/p')
[ -n "$_sel" ] && SEL="sel=$_sel "

http_probe() { # $1=标签 $2=URL（经本机 mihomo 入口，按分流规则选路）
    _r=$(curl -x http://127.0.0.1:7890 -s -o /dev/null -m 8 \
         -w '%{http_code} %{time_namelookup} %{time_connect} %{time_appconnect} %{time_total}' "$2" 2>/dev/null)
    _rc=$?
    _code=$(echo "$_r" | awk '{print $1}')
    _dns=$(echo "$_r" | awk '{print $2}')
    _tcp=$(echo "$_r" | awk '{print $3}')
    _tls=$(echo "$_r" | awk '{print $4}')
    _t=$(echo "$_r" | awk '{print $5}')
    [ -n "$_code" ] || _code=000
    for _v in _dns _tcp _tls _t; do eval "[ -n \"\$_v\" ] || $_v=0"; done
    case "$_code" in
        200|204) _st=ok ;;
        *) _st=fail ;;
    esac
    # curl_exit: 0=成功 6=DNS 7=连接 28=超时 35=TLS握手 60=证书 56=重置/中断
    echo "$(TS) ${SEL}http_$1 status=${_st} code=$_code curl_exit=$_rc dns=${_dns} tcp=${_tcp} tls=${_tls} total=${_t}s" >> "$LOG"
}

probe cn 223.5.5.5
probe vps 159.223.36.119
probe tx 43.134.46.44
http_probe proxy https://www.google.com/generate_204
http_probe gstatic https://www.gstatic.com/generate_204

# 滚动：5行/5分钟=1440行/天，7天=10080行 → 超10800行时保留最近10080行
_total=$(wc -l < "$LOG" 2>/dev/null || echo 0)
[ "$_total" -gt 10800 ] && tail -n 10080 "$LOG" > "$LOG.tmp" && mv -f "$LOG.tmp" "$LOG"
exit 0
