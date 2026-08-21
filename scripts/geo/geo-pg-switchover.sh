#!/usr/bin/env bash
# Плановое переключение PRIMARY PostgreSQL между площадками (zero-data-loss).
# Использование: geo-pg-switchover.sh --to <nsk|msk>
#
# Шаги: проверки ролей и лага -> стоп писателей -> стоп исходного PostgreSQL ->
# полный слив WAL на реплике -> promote -> старый мастер становится репликой
# (правка файлов через helper-контейнер, пока основной остановлен) ->
# перенацеливание pgbouncer-rw -> старт бывшего мастера как реплики -> писатели.
set -euo pipefail
# shellcheck disable=SC1091
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

[[ "${1:-}" == "--to" && -n "${2:-}" ]] || die "использование: $0 --to <nsk|msk>"
TARGET="$2"
ACTIVE=$(state_get ACTIVE_PG)
[[ -n "$ACTIVE" ]] || die "state ACTIVE_PG пуст (echo <site> > .state/ACTIVE_PG)"
[[ "$TARGET" != "$ACTIVE" ]] || die "$TARGET уже является активным мастером"
SOURCE="$ACTIVE"

info "Плановый свитчовер: $SOURCE ($(site_name "$SOURCE")) -> $TARGET ($(site_name "$TARGET"))"

ROLE_SOURCE=$(pg_role "$SOURCE")
[[ "$ROLE_SOURCE" == "primary" ]] || die "ожидался primary на $SOURCE, фактически: $ROLE_SOURCE. Топология не соответствует state — начните с ./geo-status.sh"
ROLE_TARGET=$(pg_role "$TARGET")
[[ "$ROLE_TARGET" == "standby" ]] || die "ожидался standby на $TARGET, фактически: $ROLE_TARGET"

LAG=$(pg_lag_seconds "$TARGET")
info "Лаг реплики: ${LAG}с (порог ${MAX_LAG_SECONDS}с)"
(( LAG <= MAX_LAG_SECONDS )) || die "лаг ${LAG}с выше порога ${MAX_LAG_SECONDS}с — дождитесь синхронизации или используйте geo-pg-failover.sh осознанно"

: "${AUTO_SRE_PG_REPLICATION_PASSWORD:?заполните AUTO_SRE_PG_REPLICATION_PASSWORD в geo.env}"
# Экранирование одинарных кавычек для primary_conninfo
CONN_PASSWORD=${AUTO_SRE_PG_REPLICATION_PASSWORD//\'/\'\\\'\'}
NEW_CONNINFO="user=replicator password='${CONN_PASSWORD}' host=$(site_host "$TARGET") port=15432 sslmode=prefer sslcompression=0"

info "Останавливаю писателей на обеих площадках..."
app_stop "$SOURCE"
app_stop "$TARGET"

info "Останавливаю PostgreSQL на источнике $SOURCE (чистый shutdown = финальный checkpoint)..."
# Критично остановить источник ДО промоута: живой мастер продолжает генерировать
# фоновый WAL (autovacuum, контрольные точки), реплика уходит дальше точки форка
# таймлайна и после promote уже не может следовать за новым мастером.
ssh_run "$SOURCE" "cd ${STACK_DIR} && docker compose -f docker-compose.pg.yml stop postgres" >/dev/null

info "Жду полного слива WAL на $TARGET (receive == replay, таймаут ${LSN_SYNC_TIMEOUT}с)..."
DEADLINE=$((SECONDS + LSN_SYNC_TIMEOUT))
PREV=""
while true; do
    RECV=$(pg_sql "$TARGET" "SELECT COALESCE(pg_last_wal_receive_lsn()::text,'')")
    REPLAY=$(pg_sql "$TARGET" "SELECT COALESCE(pg_last_wal_replay_lsn()::text,'')")
    if [[ -n "$RECV" && "$RECV" != "0/0" && "$RECV" == "$REPLAY" && "$RECV" == "$PREV" ]]; then
        break
    fi
    PREV="$RECV"
    (( SECONDS < DEADLINE )) || die "реплика не слила WAL за ${LSN_SYNC_TIMEOUT}с — прерываю БЕЗ переключения, писатели и источник остановлены"
    sleep 2
done
info "Реплика вычерпала WAL до $RECV."

info "Promote $TARGET до primary..."
PROMOTED=$(pg_sql "$TARGET" "SELECT pg_promote(true, 60)")
[[ "$PROMOTED" == "t" ]] || die "pg_promote не подтвердился"

info "Создаю слот репликации на новом мастере для бывшего источника..."
pg_sql "$TARGET" "SELECT pg_create_physical_replication_slot('auto_sre_replica') WHERE NOT EXISTS (SELECT 1 FROM pg_replication_slots WHERE slot_name='auto_sre_replica')" >/dev/null || true

# Контейнер источника остановлен — правим файлы данных helper-контейнером
# поверх тома (docker exec на остановленном контейнере невозможен).
# postgres:16-alpine работает под uid 70.
info "Перевожу бывший мастер ($SOURCE) в режим реплики..."
ssh_run "$SOURCE" "docker run --rm -v auto-sre-pg_pg-data:/d alpine sh -c 'touch /d/standby.signal && chown 70:70 /d/standby.signal'" >/dev/null
printf "primary_conninfo = '%s'\nprimary_slot_name = auto_sre_replica\n" "$NEW_CONNINFO" \
    | ssh_run "$SOURCE" "docker run --rm -i -v auto-sre-pg_pg-data:/d alpine sh -c 'cat >> /d/postgresql.auto.conf'"

info "Перенацеливаю pgbouncer-rw обеих площадок на $(site_host "$TARGET")..."
for SITE in nsk msk; do
    env_set "$SITE" PG_RW_TARGET_HOST "$(site_host "$TARGET")"
    ssh_run "$SITE" "cd ${STACK_DIR} && docker compose -f docker-compose.pg.yml up -d pgbouncer-rw" >/dev/null
done

info "Стартую бывший мастер ($SOURCE) уже в роли реплики..."
ssh_run "$SOURCE" "cd ${STACK_DIR} && docker compose -f docker-compose.pg.yml start postgres" >/dev/null

info "Запускаю писателей..."
app_start "$SOURCE"
app_start "$TARGET"

state_set ACTIVE_PG "$TARGET"
info "Готово. Активный PRIMARY теперь: $TARGET ($(site_name "$TARGET")). State обновлён."
warn "Не забудьте поправить auto_sre_pg_primary_host в ansible-инвентаре, чтобы следующий деплой не откатил pgbouncer."
