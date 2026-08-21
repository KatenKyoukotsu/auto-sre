#!/bin/bash
# Рендерит pgbouncer.ini из переменных окружения и запускает pgbouncer в foreground.
# Такой подход позволяет скриптам гео-переключения менять цель (host) через .env
# и пересоздавать контейнер — конфиг не хранится в образе и не правится sed'ом.
set -euo pipefail

: "${PG_TARGET_HOST:?PG_TARGET_HOST не задан}"
: "${PG_TARGET_PORT:=15432}"
: "${PG_DATABASE:=auto_sre}"
: "${LISTEN_PORT:=6432}"
: "${POOL_MODE:=transaction}"
: "${MAX_CLIENT_CONN:=200}"
: "${DEFAULT_POOL_SIZE:=25}"
AUTH_FILE="${AUTH_FILE:-/etc/pgbouncer/userlist.txt}"

cat > /tmp/pgbouncer.ini <<EOF
[databases]
${PG_DATABASE} = host=${PG_TARGET_HOST} port=${PG_TARGET_PORT} dbname=${PG_DATABASE}

[pgbouncer]
listen_addr = 0.0.0.0
listen_port = ${LISTEN_PORT}
unix_socket_dir =
auth_type = scram-sha-256
auth_file = ${AUTH_FILE}
admin_users = ${PG_ADMIN_USER:-auto_sre}
stats_users = ${PG_ADMIN_USER:-auto_sre}
pool_mode = ${POOL_MODE}
max_client_conn = ${MAX_CLIENT_CONN}
default_pool_size = ${DEFAULT_POOL_SIZE}
reserve_pool_size = 5
reserve_pool_timeout = 3
; SQLAlchemy 2.x + asyncpg используют именованные prepared statements —
; без этого pgbouncer 1.21+ отбивает их в transaction-режиме
max_prepared_statements = 300
server_reset_query = DISCARD ALL
ignore_startup_parameters = extra_float_digits,options
log_connections = 0
log_disconnections = 0
log_pooler_errors = 1
pidfile = /tmp/pgbouncer.pid
EOF

exec /usr/sbin/pgbouncer -v /tmp/pgbouncer.ini
