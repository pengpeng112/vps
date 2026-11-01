#!/bin/sh
# connlog — 每 5 分钟从 mihomo /connections 记录按 源IP+域名 的下行增量（≥1MB 才记行）
# 2026-10-04 部署（R4/R1 实战版）：回答"谁在什么时段用了什么网站"，配合限速决策。
# 局限：两次采样间的短连接会漏计；长流量（视频/下载）统计准确。
LOG=/etc/routermgmt/logs/connlog.log
mkdir -p /etc/routermgmt/logs
curl -s -m 6 http://127.0.0.1:9090/connections | tr '{' '\n' | grep '"id"' | awk '
{
  id=""; host=""; src=""; dl=0
  if (match($0, /"id":"[^"]+"/))        { id=substr($0,RSTART+7,RLENGTH-8) }
  if (match($0, /"host":"[^"]+"/))      { host=substr($0,RSTART+8,RLENGTH-9) }
  if (match($0, /"sourceIP":"[^"]+"/))  { src=substr($0,RSTART+12,RLENGTH-13) }
  if (match($0, /"download":[0-9]+/))   { dl=substr($0,RSTART+11,RLENGTH-11)+0 }
  if (id != "") print id "|" src "|" host "|" dl
}' > /tmp/conn_now.txt 2>/dev/null
[ -s /tmp/conn_now.txt ] || exit 0
if [ -f /tmp/connstate ]; then
  awk -F'|' 'NR==FNR{old[$1]=$4; next}{d=$4-old[$1]; if(d>=1048576) printf "%.1fMB|%s|%s\n", d/1048576, $2, ($3==""?"(no-host)":$3)}' /tmp/connstate /tmp/conn_now.txt | sort -rn > /tmp/connlog.delta
  if [ -s /tmp/connlog.delta ]; then
    awk -v t="$(date '+%F %T')" '{print t " " $0}' /tmp/connlog.delta >> "$LOG"
  fi
  rm -f /tmp/connlog.delta
fi
cp /tmp/conn_now.txt /tmp/connstate
TOTAL=$(cat "$LOG" 2>/dev/null | wc -l)
[ "$TOTAL" -gt 3000 ] && tail -n 2000 "$LOG" > /tmp/cl.tmp && cat /tmp/cl.tmp > "$LOG" && rm -f /tmp/cl.tmp
exit 0
