#!/usr/bin/env bash
# Сводка состояния обеих площадок: роли PG, лаг репликации, контейнеры, активная площадка.
set -euo pipefail
# shellcheck disable=SC1091
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

print_site() {
    local site="$1"
    local host; host=$(site_host "$site")
    echo "=== $(site_name "$site") ($host) ==="

    local role; role=$(pg_role "$site")
    if [[ "$role" == "unreachable" ]]; then
        echo "  PG:            НЕДОСТУПЕН"
    else
        echo "  PG:            $role"
        if [[ "$role" == "standby" ]]; then
            echo "  Лаг репликации: $(pg_lag_seconds "$site") сек"
        fi
    fi

    for c in pg-node auto-sre-kafka sre-agent alert-analyzer pgbouncer-rw pgbouncer-ro; do
        printf '  %-16s %s\n' "$c:" "$(container_state "$site" "$c")"
    done

    local rw_target
    rw_target=$(ssh_run "$site" "grep -E '^PG_RW_TARGET_HOST=' ${STACK_DIR}/.env 2>/dev/null | cut -d= -f2" || true)
    [[ -n "$rw_target" ]] && echo "  pgbouncer-rw → $rw_target:15432"

    local bs
    bs=$(ssh_run "$site" "grep -E '^KAFKA_BOOTSTRAP_SERVERS=' ${STACK_DIR}/.env | cut -d= -f2" || true)
    echo "  Kafka bootstrap: ${bs:-?} (active-active: каждая площадка пишет в свой брокер)"
    echo
}

echo "Активная площадка PG (state): $(state_get ACTIVE_PG || echo '?') (Kafka active-active — переключение не требуется)"
echo
print_site nsk
print_site msk
