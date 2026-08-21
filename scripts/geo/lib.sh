#!/usr/bin/env bash
# Общая библиотека гео-скриптов. Не запускать напрямую.
# Все проверки ролей читаются с живых узлов (pg_is_in_recovery) —
# state-файл используется только как подсказка ожидаемой топологии.

set -euo pipefail

GEO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_DIR="$GEO_DIR/.state"

if [[ -f "$GEO_DIR/geo.env" ]]; then
    # shellcheck disable=SC1091
    source "$GEO_DIR/geo.env"
else
    echo "ОШИБКА: нет $GEO_DIR/geo.env — скопируйте geo.env.example и заполните" >&2
    exit 1
fi

: "${NSK_HOST:?NSK_HOST не задан}"
: "${MSK_HOST:?MSK_HOST не задан}"
SSH_USER="${SSH_USER:-root}"
STACK_DIR="${STACK_DIR:-/opt/docker/auto-sre}"
PG_USER="${PG_USER:-auto_sre}"
PG_DB="${PG_DB:-auto_sre}"
MAX_LAG_SECONDS="${MAX_LAG_SECONDS:-5}"
LSN_SYNC_TIMEOUT="${LSN_SYNC_TIMEOUT:-120}"

info()  { printf '\033[32m[INFO]\033[0m %s\n' "$*"; }
warn()  { printf '\033[33m[WARN]\033[0m %s\n' "$*"; }
die()   { printf '\033[31m[FAIL]\033[0m %s\n' "$*" >&2; exit 1; }

site_host() {
    case "$1" in
        nsk) echo "$NSK_HOST" ;;
        msk) echo "$MSK_HOST" ;;
        *) die "неизвестная площадка '$1' (ожидается nsk|msk)" ;;
    esac
}

site_name() {
    case "$(site_host "$1")" in
        "$NSK_HOST") echo "Новосибирск" ;;
        *) echo "Москва" ;;
    esac
}

ssh_run() {
    local site="$1"; shift
    ssh -o BatchMode=yes -o ConnectTimeout=10 "${SSH_USER}@$(site_host "$site")" "$@"
}

state_get() { cat "$STATE_DIR/$1" 2>/dev/null || true; }
state_set() {
    mkdir -p "$STATE_DIR"
    echo "$2" > "$STATE_DIR/$1"
}

# SQL на узле через docker exec — пароли админской машине не нужны
pg_sql() {
    local site="$1"; local sql="$2"
    ssh_run "$site" "docker exec pg-node psql -U ${PG_USER} -d ${PG_DB} -tAc \"${sql}\""
}

# 'primary' | 'standby' | unreachable
pg_role() {
    local role
    if ! role=$(pg_sql "$1" "SELECT CASE WHEN pg_is_in_recovery() THEN 'standby' ELSE 'primary' END" 2>/dev/null); then
        echo "unreachable"
        return
    fi
    echo "$role"
}

# Лаг репликации в секундах (только для standby)
pg_lag_seconds() {
    pg_sql "$1" "SELECT COALESCE(EXTRACT(EPOCH FROM now()-pg_last_xact_replay_timestamp())::int, 999999)"
}

app_stop()  { ssh_run "$1" "cd ${STACK_DIR} && docker compose stop ${APP_SERVICES}" >/dev/null; }
app_start() { ssh_run "$1" "cd ${STACK_DIR} && docker compose up -d ${APP_SERVICES}" >/dev/null; }

# Точечная правка KEY=value в .env стека на узле
env_set() {
    local site="$1"; local key="$2"; local value="$3"
    ssh_run "$site" "cd ${STACK_DIR} && sed -i 's|^${key}=.*|${key}=${value}|' .env"
}

container_state() {
    ssh_run "$1" "docker inspect -f '{{.State.Status}}' \"$2\" 2>/dev/null" || echo "absent"
}
