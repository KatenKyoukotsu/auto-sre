#!/usr/bin/env bash
# АВАРИЙНОЕ переключение PRIMARY: promote реплики без согласования с источником.
# Использование: geo-pg-failover.sh --to <nsk|msk> [--force]
#   Источник считается недоступным. Если он на самом деле жив и primary —
#   скрипт откажется (без --force): живой мастер = используйте geo-pg-switchover.sh.
#
# ВНИМАНИЕ: async-репликация => возможна потеря хвоста записей (RPO > 0).
# После стабилизации реинтегрируйте старый узел: ./pg-replica-bootstrap.sh <old> --wipe
set -euo pipefail
# shellcheck disable=SC1091
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

[[ "${1:-}" == "--to" && -n "${2:-}" ]] || die "использование: $0 --to <nsk|msk> [--force]"
TARGET="$2"; FORCE=false
[[ "${3:-}" == "--force" ]] && FORCE=true

ACTIVE=$(state_get ACTIVE_PG)
[[ -n "$ACTIVE" ]] || die "state ACTIVE_PG пуст"
[[ "$TARGET" != "$ACTIVE" ]] || die "$TARGET уже активный"
SOURCE="$ACTIVE"

ROLE_TARGET=$(pg_role "$TARGET")
[[ "$ROLE_TARGET" == "standby" || "$FORCE" == true ]] || die "цель $TARGET не standby ($ROLE_TARGET) — трогать нечего?"

ROLE_SOURCE=$(pg_role "$SOURCE")
if [[ "$ROLE_SOURCE" == "primary" && "$FORCE" != true ]]; then
    die "источник $SOURCE ЖИВ и primary. Аварийный promote создаст split-brain. Используйте geo-pg-switchover.sh либо добавьте --force осознанно."
fi

info "Аварийный переключение на $TARGET ($(site_name "$TARGET")). Источник: $ROLE_SOURCE."

info "Останавливаю писателей где могу..."
app_stop "$TARGET" 2>/dev/null || warn "не удалось остановить писателей на цели"
if ! app_stop "$SOURCE" 2>/dev/null; then
    warn "писатели на источнике недоступны — при его возврате в сеть немедленно изолируйте postgres!"
fi

info "Promote $TARGET..."
PROMOTED=$(pg_sql "$TARGET" "SELECT pg_promote(true, 60)")
[[ "$PROMOTED" == "t" ]] || die "pg_promote не подтвердился"

info "Создаю слот для будущей реинтеграции источника..."
pg_sql "$TARGET" "SELECT pg_create_physical_replication_slot('auto_sre_replica') WHERE NOT EXISTS (SELECT 1 FROM pg_replication_slots WHERE slot_name='auto_sre_replica')" >/dev/null || true

info "Перенацеливаю pgbouncer-rw..."
for SITE in nsk msk; do
    if env_set "$SITE" PG_RW_TARGET_HOST "$(site_host "$TARGET")" 2>/dev/null \
        && ssh_run "$SITE" "cd ${STACK_DIR} && docker compose -f docker-compose.pg.yml up -d pgbouncer-rw" >/dev/null 2>&1; then
        info "  $SITE: ok"
    else
        warn "  $SITE: недоступен — поправьте PG_RW_TARGET_HOST вручную при восстановлении"
    fi
done

info "Запускаю писателей на цели..."
app_start "$TARGET"

state_set ACTIVE_PG "$TARGET"
info "Готово. PRIMARY теперь $TARGET."
warn "Реинтеграция бывшего мастера после его восстановления:"
warn "  ./pg-replica-bootstrap.sh ${SOURCE} --wipe"
