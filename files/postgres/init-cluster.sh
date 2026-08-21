#!/bin/bash
# Первичная инициализация мастера PG-кластера (docker-entrypoint-initdb.d, только свежий PGDATA).
# Создаёт служебные роли и открывает репликацию. Пароли приходят из окружения контейнера.
set -euo pipefail

: "${PG_REPLICATION_PASSWORD:?PG_REPLICATION_PASSWORD не задан}"
: "${PG_POOL_PASSWORD:?PG_POOL_PASSWORD не задан}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE ROLE replicator WITH LOGIN REPLICATION PASSWORD '${PG_REPLICATION_PASSWORD}';
    CREATE ROLE pgpool WITH LOGIN PASSWORD '${PG_POOL_PASSWORD}';
    GRANT CONNECT ON DATABASE ${POSTGRES_DB} TO pgpool;
EOSQL

# Репликация с любого адреса — пароль scram, наружу порт 15432 должен быть закрыт файрволом
echo "host replication replicator all scram-sha-256" >> "$PGDATA/pg_hba.conf"

# Юзерлист для pgbouncer из SCRAM-верификаторов ЭТОГО кластера: при ре-инициализации
# верификаторы пересоздаются с новой солью, а захардкоженный юзерлист пула протухает.
if [[ -n "${AUTH_OUTPUT_DIR:-}" && -d "${AUTH_OUTPUT_DIR}" ]]; then
    if psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -tAc \
        "SELECT concat('\"', rolname, '\" \"', rolpassword, '\"') FROM pg_authid WHERE rolname IN ('${POSTGRES_USER}', 'pgpool')" \
        > "${AUTH_OUTPUT_DIR}/userlist.txt" 2>/dev/null; then
        echo "userlist.txt записан: ${AUTH_OUTPUT_DIR}/userlist.txt"
    else
        echo "WARN: не удалось записать userlist.txt в ${AUTH_OUTPUT_DIR} (нет прав) — сгенерируйте вручную:"
        echo "  docker exec pg-node psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} -tAc \"SELECT concat('\\\"',rolname,'\\\" \\\"',rolpassword,'\\\"') FROM pg_authid WHERE rolname IN ('${POSTGRES_USER}','pgpool')\" > files/pgbouncer/auth/userlist.txt"
    fi
fi
