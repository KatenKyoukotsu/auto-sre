#!/usr/bin/env bash
# Прогон тестового набора Auto SRE в контейнере поверх образа sre-agent.
# Юнит-тесты изолированы по сервисам (отдельные процессы — у alert-analyzer и
# sre-agent одноимённые модули store/metrics, им нельзя жить в одном интерпретаторе).
set -euo pipefail
cd "$(dirname "$0")/.."

IMAGE=auto-sre-tests:latest
NETWORK=${AUTO_SRE_NET:-auto-sre_auto-sre-dev-net}

docker build -q -f Dockerfile.tests -t "$IMAGE" .

run() {
  docker run --rm --network "$NETWORK" \
    -v "$(pwd)/files:/srv/src:ro" \
    -v "$(pwd)/tests:/srv/tests:ro" \
    -e SRE_AGENT_URL=http://sre-agent:8096 \
    -e ALERT_ANALYZER_URL=http://alert-analyzer:8097 \
    "$IMAGE" pytest -c /srv/tests/pytest.ini "$@"
}

echo "=== Юнит: sre-agent ==="
run /srv/tests/unit/sre-agent -v

echo "=== Юнит: alert-analyzer + common ==="
run /srv/tests/unit/alert-analyzer -v

echo "=== Интеграция (сами скипаются, если стек не поднят) ==="
run /srv/tests/integration -v
