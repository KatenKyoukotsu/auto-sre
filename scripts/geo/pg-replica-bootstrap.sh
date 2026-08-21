#!/usr/bin/env bash
# Заливка реплики с нуля: pg_basebackup от текущего PRIMARY в PGDATA целевого узла.
# Использование:
#   pg-replica-bootstrap.sh <target-site> [--wipe]   # --wipe стирает существующие данные реплики
# Цель должна быть standby-кандидатом; источник берётся из state ACTIVE_PG и обязан быть primary.
set -euo pipefail
# shellcheck disable=SC1091
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

[[ $# -ge 1 ]] || die "использование: $0 <nsk|msk> [--wipe]"
TARGET="$1"; shift
WIPE=false
[[ "${1:-}" == "--wipe" ]] && WIPE=true

ACTIVE=$(state_get ACTIVE_PG)
[[ -n "$ACTIVE" ]] || die "state ACTIVE_PG пуст — сначала зафиксируйте текущий первичный узел: echo nsk > .state/ACTIVE_PG"
SOURCE="$ACTIVE"
[[ "$TARGET" != "$SOURCE" ]] || die "цель совпадает с активным мастером ($TARGET)"

: "${AUTO_SRE_PG_REPLICATION_PASSWORD:?заполните AUTO_SRE_PG_REPLICATION_PASSWORD в geo.env}"
VOLUME="auto-sre-pg_pg-data"

info "Источник: $SOURCE ($(site_host "$SOURCE")), цель: $TARGET ($(site_host "$TARGET"))"
ROLE_SOURCE=$(pg_role "$SOURCE")
[[ "$ROLE_SOURCE" == "primary" ]] || die "источник $SOURCE — не primary ($ROLE_SOURCE), переключение запрещено"
info "Роль источника подтверждена: primary"

info "Останавливаю postgres на цели..."
ssh_run "$TARGET" "cd ${STACK_DIR} && docker compose -f docker-compose.pg.yml stop postgres" >/dev/null

if [[ "$WIPE" == true ]]; then
    info "Стираю данные на цели (--wipe)..."
    ssh_run "$TARGET" "docker run --rm -v ${VOLUME}:/data alpine sh -c 'rm -rf /data/* /data/..?* /data/.[!.]*' " >/dev/null
else
    EMPTY=$(ssh_run "$TARGET" "docker run --rm -v ${VOLUME}:/data alpine sh -c 'ls -A /data | head -1 | wc -l'")
    [[ "$EMPTY" == "0" || "$EMPTY" == "" ]] || die "PGDATA на цели не пуст (нужен --wipe для перезаливки)"
fi

info "Убираю старый слот на источнике (если был)..."
pg_sql "$SOURCE" "SELECT pg_drop_replication_slot('auto_sre_replica') WHERE EXISTS (SELECT 1 FROM pg_replication_slots WHERE slot_name='auto_sre_replica')" >/dev/null || true

info "pg_basebackup (это может занять время пропорционально объёму БД)..."
ssh_run "$TARGET" "docker run --rm --network host \
    -e PGPASSWORD=${AUTO_SRE_PG_REPLICATION_PASSWORD} \
    -v ${VOLUME}:/var/lib/postgresql/data \
    postgres:16-alpine \
    pg_basebackup -h $(site_host "$SOURCE") -p 15432 -U replicator \
    -D /var/lib/postgresql/data -Fp -Xs -P -C -S auto_sre_replica -R"

info "Запускаю postgres на цели..."
ssh_run "$TARGET" "cd ${STACK_DIR} && docker compose -f docker-compose.pg.yml up -d postgres" >/dev/null

info "Жду потоковую репликацию..."
for _ in $(seq 1 30); do
    ROLE=$(pg_role "$TARGET")
    STREAMING=$(pg_sql "$TARGET" "SELECT COALESCE((SELECT status FROM pg_stat_wal_receiver),'none')" 2>/dev/null || echo none)
    if [[ "$ROLE" == "standby" && "$STREAMING" == "streaming" ]]; then
        info "Готово: $TARGET — standby, потоковая репликация с $SOURCE активна."
        exit 0
    fi
    sleep 2
done
die "реплика не вошла в streaming за 60с — проверьте логи: ssh $(site_host "$TARGET") docker logs pg-node"
