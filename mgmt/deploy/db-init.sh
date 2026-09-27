#!/bin/sh
# Postgres 首次初始化：双账号（任务书 §19.4）
#  mgmt_migrate：迁移（DDL）；mgmt_app：运行态（audit_log 仅 INSERT/SELECT，由迁移 0002 收紧）
#  密码来自 .env（MIGRATE_PASSWORD / APP_DB_PASSWORD），不出现在文件里
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" <<-EOSQL
CREATE ROLE mgmt_migrate LOGIN PASSWORD '${MIGRATE_PASSWORD}';
CREATE ROLE mgmt_app LOGIN PASSWORD '${APP_DB_PASSWORD}';
CREATE DATABASE routermgmt OWNER mgmt_migrate;
GRANT ALL PRIVILEGES ON DATABASE routermgmt TO mgmt_migrate;
EOSQL
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname routermgmt <<-EOSQL
GRANT USAGE ON SCHEMA public TO mgmt_app;
EOSQL
