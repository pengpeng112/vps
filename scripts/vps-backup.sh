#!/bin/bash
# 管理台每日备份 v2（2026-09-23）：v1 基础上补齐 DR 全量件
# 新增：sing-box 配置(全队列代理出口，丢了=所有路由器代理断)、root/jump 的
#       authorized_keys(路由器隧道+Claude跳板的入口)、sshd 配置、ufw 规则、
#       /opt/mgmt 全量源码(排除 backups)。
# 恢复步骤见《docs/服务器访问通道总览-20260923.md》DR 章节。
# VPS 落位：/opt/mgmt/backup.sh；cron: 30 3 * * *
set -u
DIR=/opt/mgmt/backups
TS=$(date +%Y%m%d-%H%M)
LOG=$DIR/backup.log
mkdir -p "$DIR"
ok()   { echo "$(date "+%F %T") ok  mgmt-backup-$TS.tar.gz $1" >> "$LOG"; }
fail() { echo "$(date "+%F %T") FAIL ($2)" >> "$LOG"; exit 1; }

STAGE=$(mktemp -d) || fail "" stage
trap "rm -rf $STAGE" EXIT

# --- v1 原有：数据库 + 部署密钥 ---
docker exec routermgmt-db-1 pg_dump -U postgres routermgmt > "$STAGE/db.sql" 2>>"$LOG" || fail "" pg_dump
cp -a /opt/mgmt/deploy/.env "$STAGE/" 2>>"$LOG" || fail "" env
cp -a /opt/mgmt/deploy/prod-certs "$STAGE/" 2>/dev/null
cp -a /opt/mgmt/deploy/pkg-athena.json "$STAGE/" 2>/dev/null
cp -a /opt/mgmt/deploy/docker-compose.yml /opt/mgmt/deploy/docker-compose.prod.yml "$STAGE/" 2>/dev/null
cp -a /etc/wireguard/wg0.conf "$STAGE/" 2>/dev/null

# --- v2 新增 ---
mkdir -p "$STAGE/singbox" "$STAGE/ssh" "$STAGE/ufw"
cp -a /usr/local/etc/sing-box/config.json "$STAGE/singbox/" 2>>"$LOG" || fail "" singbox-config
cp -a /etc/systemd/system/sing-box.service "$STAGE/singbox/" 2>/dev/null
cp -a /root/.ssh/authorized_keys "$STAGE/ssh/root.authorized_keys" 2>/dev/null
cp -a /home/jump/.ssh/authorized_keys "$STAGE/ssh/jump.authorized_keys" 2>/dev/null
cp -a /etc/ssh/sshd_config "$STAGE/ssh/" 2>/dev/null
cp -a /etc/ssh/sshd_config.d "$STAGE/ssh/" 2>/dev/null
tar -czf "$STAGE/ufw/etc-ufw.tar.gz" -C /etc ufw 2>/dev/null
tar -czf "$STAGE/mgmt-src.tar.gz" -C /opt --exclude='mgmt/backups' mgmt 2>>"$LOG" || fail "" mgmt-src

# --- v3 新增：腾讯第二节点配置（VPS 拉取；节点不可达时告警不中断主备份） ---
mkdir -p "$STAGE/tx-node"
scp -i /root/.ssh/id_ed25519_txbackup -o StrictHostKeyChecking=accept-new -o BatchMode=yes -o ConnectTimeout=10 ubuntu@43.134.46.44:.tx-singbox-config "$STAGE/tx-node/singbox-config.json" 2>>"$LOG" || echo "$(date '+%F %T') WARN tx-node-config pull failed" >> "$LOG"

tar -czf "$DIR/mgmt-backup-$TS.tar.gz" -C "$STAGE" . || fail "" tar
chmod 600 "$DIR/mgmt-backup-$TS.tar.gz"
# 保留最近 14 份
ls -1t "$DIR"/mgmt-backup-*.tar.gz 2>/dev/null | tail -n +15 | xargs -r rm -f
ok "$(du -h "$DIR/mgmt-backup-$TS.tar.gz" | cut -f1)"
