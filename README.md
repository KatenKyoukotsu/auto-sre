# Auto SRE Ansible Role

Роль разворачивает платформу **Auto SRE** — набор Docker-сервисов, которые:

- анализируют логи продуктовой системы (Victoria Logs) и находят всплески ошибок относительно скользящего базового уровня;
- прогоняют находки через LLM (LiteLLM, OpenAI-совместимый API) и публикуют их на «стене» аномалий;
- принимают алерты Alertmanager через webhook, коррелируют их группами и анализируют тем же LLM;
- ведут ежедневный мини-блог с дайджестом инцидентов.

## Архитектура

| Сервис | Порт | Назначение |
| :--- | :---: | :--- |
| **postgres** | 5432 | PostgreSQL 16 — findings, blog_posts, alert_analysis, outbox |
| **kafka** | 9092 | Kafka (KRaft, single-node) — топики `auto-sre.findings`, `auto-sre.blog`, `auto-sre.scan-events` |
| **sre-agent** | 8096 | FastAPI + APScheduler: скан логов VL, детекция аномалий, LLM-анализ, web UI (`/` — стена, `/blog`), метрики |
| **alert-analyzer** | 8097 | FastAPI: приём Alertmanager webhook (`/webhook`), батчинг, LLM-корреляция алертов |

Ключевые паттерны:
- **Transactional outbox**: finding + событие Kafka пишутся в одной транзакции PostgreSQL; поллер отправляет каждые 5 c.
- **Circuit breaker + retry** в клиентах VL и LLM.
- **Дедупликация** находок по стриму (`DEDUP_MINUTES`) и алертов по fingerprint (`ALERT_DEDUP_WINDOW`).

## Структура

```
roles/auto-sre/
├── files/
│   ├── common/
│   │   └── llm_client.py          # общий LLM-клиент (retry + circuit breaker)
│   ├── sre-agent/
│   │   ├── app.py                 # FastAPI + APScheduler + Basic Auth + метрики
│   │   ├── agent.py               # rolling-baseline детекция аномалий
│   │   ├── vl.py                  # прямой HTTP-клиент Victoria Logs
│   │   ├── llm.py                 # LLM-анализ находок и блогов
│   │   ├── store.py               # SQLAlchemy 2.0 async (PostgreSQL)
│   │   ├── kafka_producer.py      # idempotent producer + outbox poller
│   │   ├── kafka_consumer.py      # воркер LLM-анализа из Kafka
│   │   ├── metrics.py             # Prometheus-метрики
│   │   ├── migrations/01_init.sql # схема findings/blog/outbox
│   │   ├── alerting/auto-sre-rules.yaml  # PrometheusRule (25+ правил)
│   │   ├── templates/ static/     # web UI
│   │   └── Dockerfile
│   └── alert-analyzer/
│       ├── app.py                 # webhook + auth + flush-таск
│       ├── analyzer.py            # AlertBatcher + LLM-корреляция
│       ├── models.py              # Pydantic-модель Alertmanager payload
│       ├── store.py               # таблица alert_analysis
│       ├── metrics.py
│       ├── migrations/01_init.sql # схема alert_analysis
│       └── Dockerfile             # контекст сборки — корень files/
├── tasks/main.yml
├── templates/
│   ├── env.j2
│   ├── docker-compose.yml.j2      # prod
│   └── docker-compose.dev.yml.j2  # dev (hot reload)
└── README.md
```

## Требования

- Хост с Docker; плейбук `auto-sre.yaml`, hosts: `log`.
- Доступность Victoria Logs (`VL_URL`) и LiteLLM (`LITELLM_URL`).
- Секреты (`victorialogs_password`, пароли PostgreSQL и Basic Auth) задаются в инвентаре.

## Запуск

```bash
ansible-playbook -i inventory/all-01-prod auto-sre.yaml
```

Ручной триггер скана после установки:

```bash
curl -u user:pass -X POST http://<host>:8096/api/trigger/scan
```

Локальная разработка (hot reload, тестовые postgres/kafka):

```bash
cd /opt/docker/auto-sre   # каталог с отрендеренным dev-compose
docker compose -f docker-compose.dev.yml up -d --build
```

## REST API sre-agent (:8096)

| Метод | Путь | Auth | Описание |
| :--- | :--- | :--- | :--- |
| GET | `/api/findings` | Basic | Список находок |
| GET | `/api/findings/{id}` | Basic | Находка по id |
| POST | `/api/findings/{id}/ack` | Basic | Подтвердить находку |
| GET | `/api/blog` | Basic | Блог-посты |
| GET | `/api/blog/status` | Basic | Статус генерации блога |
| POST | `/api/trigger/scan` | Basic | Ручной скан |
| POST | `/api/trigger/full-scan` | Basic | Полный исторический скан |
| POST | `/api/trigger/blog` | Basic | Ручная генерация блога |
| GET | `/api/health` | — | Статус сервиса |
| GET | `/metrics` | — | Prometheus-метрики |
| GET | `/` , `/blog` | Basic | Web UI |

## REST API alert-analyzer (:8097)

| Метод | Путь | Auth | Описание |
| :--- | :--- | :--- | :--- |
| POST | `/webhook` | Basic* | Приём Alertmanager webhook |
| POST | `/webhook/test` | Basic* | Тест без записи в БД |
| GET | `/api/analyses` | Basic* | Список анализов (фильтры `alertname`, `severity`, `status`, `since`) |
| GET | `/api/analyses/{id}` | Basic* | Анализ по id |
| GET | `/api/stats` | Basic* | Буфер + счётчики |
| POST | `/api/flush` | Basic* | Принудительный анализ буфера |
| GET | `/api/health` | — | Статус сервиса |
| GET | `/metrics` | — | Prometheus-метрики |

\* управляется `AUTH_ENABLED`.

Пример настройки Alertmanager:

```yaml
route:
  receiver: auto-sre
receivers:
  - name: auto-sre
    webhook_configs:
      - url: http://auto-sre-host:8097/webhook
        basic_auth:
          username: ...
          password: ...
```

## Переменные окружения (templates/env.j2)

### Victoria Logs (sre-agent)
`VL_URL`, `VL_USERNAME`, `VL_PASSWORD`, `VL_TIMEOUT`, `VL_MAX_RETRIES`, `VL_RETRY_BASE_DELAY`, `VL_CIRCUIT_BREAKER_THRESHOLD`, `VL_CIRCUIT_BREAKER_TIMEOUT`

### LLM (оба сервиса)
`LITELLM_URL`, `LITELLM_API_KEY`, `LITELLM_MODEL`, `LLM_TIMEOUT`, `LLM_MAX_RETRIES`, `LLM_RETRY_BASE_DELAY`, `LLM_CIRCUIT_BREAKER_THRESHOLD`, `LLM_CIRCUIT_BREAKER_TIMEOUT`

### PostgreSQL
`POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`

### Scheduling & детекция (sre-agent)
`SCAN_INTERVAL_MINUTES` (15), `BLOG_HOUR` (7), `BLOG_MINUTE` (30), `TZ` (Europe/Moscow), `ERROR_PATTERN`, `HISTORY_HOURS`, `WINDOW_MINUTES`, `SPIKE_STD_MULTIPLIER`, `MIN_ABS_SPIKE` (20), `DEDUP_MINUTES` (60), `MAX_STREAMS`

### Alert-analyzer
`ALERT_BATCH_WINDOW_SEC` (300), `ALERT_BATCH_MAX` (20), `ALERT_DEDUP_WINDOW` (3600), `FLUSH_INTERVAL` (60)

### Auth
`AUTH_ENABLED` (true), `AUTH_USERNAME`, `AUTH_PASSWORD`

### Прочее
`LOG_LEVEL` (INFO)

## Данные

- PostgreSQL: `/opt/data/auto-sre/postgres`
- Kafka: `/opt/data/auto-sre/kafka`
- Исходники: `/opt/docker/auto-sre/`

Схема БД создаётся автоматически: миграции монтируются в `/docker-entrypoint-initdb.d/` плюс `init_db()` при старте сервисов.

## Мониторинг

Метрики обоих сервисов (`/metrics`) собираются внешним Prometheus. Готовые правила алертинга — `files/sre-agent/alerting/auto-sre-rules.yaml`.
