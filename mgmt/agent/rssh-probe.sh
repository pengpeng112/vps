#!/bin/bash
# rssh-probe — 排障口存活探测 + 数据库更新 (2026-10-31)
# 由 cron 每分钟调起，纯 bash + psql，无 Python 依赖

DB_NAME=routermgmt
RESULTS=/tmp/rssh-probe-$$

# 获取所有设备 ID 和排障口
sudo -u postgres psql $DB_NAME -t -A -F'|' -c \
  "SELECT id, remote_ssh_port FROM devices WHERE remote_ssh_port IS NOT NULL AND remote_ssh_port != '' AND remote_ssh_port ~ '^[0-9]+$';" \
  > $RESULTS 2>/dev/null

while IFS='|' read -r dev_id port; do
    [ -z "$port" ] && continue

    # 检查 SSH banner
    BANNER=$(timeout 3 bash -c "exec 3<>/dev/tcp/127.0.0.1/$port && head -c 4 <&3" 2>/dev/null)

    if [ "$BANNER" = "SSH-" ]; then
        ALIVE="true"
    else
        ALIVE="false"
    fi

    # 更新数据库
    sudo -u postgres psql $DB_NAME -q -c \
      "UPDATE devices SET remote_ssh_alive = $ALIVE, last_remote_ssh_check_at = now() WHERE id = '$dev_id';" \
      2>/dev/null

    echo "$dev_id port=$port alive=$ALIVE"
done < $RESULTS

rm -f $RESULTS
