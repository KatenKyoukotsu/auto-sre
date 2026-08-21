# Auto SRE — Архитектурная документация

## Обзор системы

Auto SRE — платформа наблюдаемости двойного назначения:
1. **Детекция аномалий в логах** — сканирует Victoria Logs на предмет всплесков ошибок, анализирует с помощью LLM
2. **Анализ алертов** — принимает вебхуки Alertmanager, коррелирует алерты с помощью LLM

---

## Диаграмма компонентов

```mermaid
flowchart TB
    subgraph platform ["ПЛАТФОРМА AUTO SRE"]
        direction TB

        VL[("Victoria Logs<br/>прямой HTTP")]

        subgraph sreagent ["sre-agent"]
            direction LR
            SCANNER["Сканер<br/>(APScheduler)"]
            REST["REST API"]
            BLOGGEN["Генератор блога"]
        end

        PG[("PostgreSQL<br/>findings · blog_posts · outbox_events")]
        OUTBOX["Outbox-поллер<br/>(интервал 5с)"]
        KAFKA["Kafka<br/>(3 топика)"]
        CONSUMER["Kafka-консюмер<br/>(LLM-анализ)"]

        VL --> SCANNER
        SCANNER --> PG
        REST --> PG
        BLOGGEN --> PG
        PG --> OUTBOX
        OUTBOX --> KAFKA
        KAFKA --> CONSUMER
        CONSUMER --> PG

        subgraph alertanalyzer ["alert-analyzer"]
            direction LR
            WH["Приём вебхуков"] --> BATCHER["AlertBatcher<br/>(время + размер)"]
            BATCHER --> LLMA["LLM-анализ"]
            LLMA --> APG[("PostgreSQL<br/>alert_analysis")]
        end

        WH -.-> ALERTS["Kafka<br/>(алерты)"]
        BATCHER -.-> ALERTS
    end
```

---

## Потоки данных

### 1. Детекция аномалий в логах (sre-agent)

```mermaid
flowchart TD
    T["Каждые SCAN_INTERVAL_MINUTES (по умолчанию 15 мин)"]
    T --> A["1. Получить активные стримы из VL (за последние 15 мин)"]
    A --> B["2. Для каждого стрима (макс. 8):<br/>a. запросить историю количества ошибок (окна 6ч / 15 мин)<br/>b. вычислить базовую линию (среднее / std)<br/>c. сравнить текущее окно с базовой линией"]
    B --> C{"d. всплеск?"}
    C -- да --> D["получить сэмплы, отправить в LLM"]
    C -- нет --> E
    D --> E["3. Дедупликация по сервису (окно 60 мин)"]
    E --> F["4. Сохранить находку + outbox-событие (одна транзакция)"]
    F --> G["5. Outbox-поллер → Kafka (топик findings)"]
    G --> H["6. Консюмер забирает → LLM-анализ → обновление находки"]
```

**Формула детекции всплеска**:
```
threshold = max(mean + 3*std, 2*mean)
is_spike = current > threshold AND current >= 20
```

### 2. Генерация блога (sre-agent)

```mermaid
flowchart TD
    T["Ежедневно в 07:30 (Europe/Moscow)"]
    T --> A["1. Получить находки за последние 24ч"]
    A --> B["2. Собрать дайджест (количество, краткие описания)"]
    B --> C["3. Отправить в LLM с промптом для блога"]
    C --> D["4. Сохранить пост в PostgreSQL"]
```

### 3. Анализ алертов (alert-analyzer)

```mermaid
flowchart TD
    W["Вебхук Alertmanager → /webhook"]
    W --> A["1. Проверка Basic Auth (опционально)"]
    A --> B["2. Разбор AlertmanagerPayload (Pydantic)"]
    B --> C["3. Для каждого алерта:<br/>a. дедупликация по фингерпринту (окно 1ч)<br/>b. группировка по (alertname, severity, cluster, ns, service)<br/>c. добавление в AlertBatcher (временное окно 5 мин, макс. 20)"]
    C --> D{"4. Батч готов?"}
    D -- да --> E["LLM-анализ"]
    D -- "периодический flush (60с)" --> E
    E --> F["5. Сохранить alert_analysis по каждому фингерпринту"]
```

---

## Схема базы данных

### Таблицы PostgreSQL

```sql
-- Основные находки из сканирования логов
CREATE TABLE findings (
    id              SERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    severity        VARCHAR(20) NOT NULL DEFAULT 'low',
    service         VARCHAR(255),
    title           VARCHAR(500) NOT NULL DEFAULT 'Аномалия',
    summary         TEXT,
    possible_cause  TEXT,
    recommended_action TEXT,
    confidence      FLOAT,
    raw_data        TEXT,
    acknowledged    BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX idx_findings_created ON findings(created_at DESC);
CREATE INDEX idx_findings_service ON findings(service);

-- Посты блога
CREATE TABLE blog_posts (
    id          SERIAL PRIMARY KEY,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    title       VARCHAR(500) NOT NULL,
    content     TEXT NOT NULL
);

CREATE INDEX idx_blog_created ON blog_posts(created_at DESC);

-- Транзакционный outbox для Kafka
CREATE TABLE outbox_events (
    id            SERIAL PRIMARY KEY,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    topic         VARCHAR(255) NOT NULL,
    key           VARCHAR(255),
    payload       TEXT NOT NULL,
    processed_at  TIMESTAMPTZ,
    retry_count   INTEGER NOT NULL DEFAULT 0,
    last_error    TEXT
);

CREATE INDEX idx_outbox_unprocessed ON outbox_events(processed_at, created_at);

-- Анализ алертов (alert-analyzer)
CREATE TABLE alert_analysis (
    id              SERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    alert_fingerprint VARCHAR(64) NOT NULL,
    alertname       VARCHAR(255) NOT NULL,
    severity        VARCHAR(50) NOT NULL,
    cluster         VARCHAR(255),
    namespace       VARCHAR(255),
    service         VARCHAR(255),
    status          VARCHAR(20) NOT NULL,  -- firing, resolved
    correlated_group TEXT,  -- JSON-массив фингерпринтов
    root_cause      TEXT,
    suggested_actions TEXT,
    confidence      TEXT,  -- JSON со скорами
    raw_alerts      TEXT NOT NULL,  -- JSON
    llm_model       VARCHAR(100)
);

CREATE INDEX idx_alert_analysis_created ON alert_analysis(created_at DESC);
CREATE INDEX idx_alert_analysis_fingerprint ON alert_analysis(alert_fingerprint);
CREATE INDEX idx_alert_analysis_alertname ON alert_analysis(alertname);
CREATE INDEX idx_alert_analysis_status ON alert_analysis(status);
```

---

## Топики Kafka

| Топик | Партиции | Хранение | Продюсер | Консюмер |
|-------|------------|-----------|----------|----------|
| `auto-sre.findings` | 1 | 168ч | sre-agent (outbox) | консюмер sre-agent |
| `auto-sre.blog` | 1 | 168ч | sre-agent (outbox) | консюмер sre-agent |
| `auto-sre.scan-events` | 1 | 168ч | sre-agent (outbox) | консюмер sre-agent |
| `auto-sre.alerts` | 1 | 168ч | alert-analyzer (вебхук) | *в будущем* |

**Конфигурация продюсера**: `acks=all`, `enable_idempotence=true`, `compression_type=snappy`

---

## Сервисы

### sre-agent (порт 8096)

| Эндпоинт | Auth | Описание |
|--------|------|-------------|
| `GET /` | Basic | UI стены аномалий |
| `GET /blog` | Basic | UI дайджеста блога |
| `GET /api/findings` | Basic | Список находок |
| `GET /api/findings/{id}` | Basic | Одна находка |
| `POST /api/findings/{id}/ack` | Basic | Подтверждение (ack) находки |
| `GET /api/blog` | Basic | Список постов блога |
| `GET /api/blog/status` | Basic | Статус генерации блога |
| `POST /api/trigger/scan` | Basic | Ручной скан |
| `POST /api/trigger/full-scan` | Basic | Полный исторический скан |
| `POST /api/trigger/blog` | Basic | Ручная генерация блога |
| `GET /api/health` | Нет | Проверка здоровья |
| `GET /metrics` | Нет | Метрики Prometheus |

**Фоновые задачи**:
- `scan_job` — интервал (15 мин): запускает `Agent.scan()`
- `daily_blog` — cron (07:30 MSK): запускает `Agent.generate_daily_blog()`

### alert-analyzer (порт 8097)

| Эндпоинт | Auth | Описание |
|--------|------|-------------|
| `POST /webhook` | Basic* | Вебхук Alertmanager |
| `POST /webhook/test` | Basic* | Тестовый вебхук |
| `GET /api/analyses` | Basic | Список анализов алертов |
| `GET /api/analyses/{id}` | Basic | Один анализ |
| `GET /api/stats` | Basic | Статистика буфера, конфигурация |
| `POST /api/flush` | Basic | Ручной flush |
| `GET /api/health` | Нет | Проверка здоровья |
| `GET /metrics` | Нет | Метрики Prometheus |

*Опционально: `AUTH_ENABLED=false` отключает аутентификацию*

---

## Конфигурация

Всё через переменные окружения (рендерятся из `templates/env.j2` средствами Ansible):

### Victoria Logs
```bash
VL_URL=http://10.148.14.12:9428
VL_USERNAME=admin
VL_PASSWORD=${victorialogs_password}
VL_TIMEOUT=60
VL_MAX_RETRIES=3
VL_RETRY_BASE_DELAY=1.0
VL_CIRCUIT_BREAKER_THRESHOLD=5
VL_CIRCUIT_BREAKER_TIMEOUT=30
```

### LLM (LiteLLM)
```bash
LITELLM_URL=http://10.148.14.10:4000
LITELLM_API_KEY=sk-litellm-master-key
LITELLM_MODEL=gemma-4-12B-it-qat-q4_0-gguf
LLM_TEMPERATURE=0.2
LLM_MAX_TOKENS=2000
LLM_TIMEOUT=180
LLM_MAX_RETRIES=3
LLM_RETRY_BASE_DELAY=2.0
LLM_CIRCUIT_BREAKER_THRESHOLD=5
LLM_CIRCUIT_BREAKER_TIMEOUT=60
```

### Kafka
```bash
KAFKA_BOOTSTRAP_SERVERS=kafka:9092
KAFKA_TOPIC_FINDINGS=auto-sre.findings
KAFKA_TOPIC_BLOG=auto-sre.blog
KAFKA_TOPIC_SCAN_EVENTS=auto-sre.scan-events
KAFKA_CONSUMER_GROUP=auto-sre-worker
OUTBOX_POLL_INTERVAL=5
```

### PostgreSQL
```bash
POSTGRES_DB=auto_sre
POSTGRES_USER=auto_sre
POSTGRES_PASSWORD=${auto_sre_postgres_password}
DATABASE_URL=postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB}
```

### Планирование
```bash
SCAN_INTERVAL_MINUTES=15
BLOG_HOUR=7
BLOG_MINUTE=30
TZ=Europe/Moscow
ERROR_PATTERN="i(error*) OR i(exception*) OR i(panic*) OR i(fatal*) OR i(traceback*)"
HISTORY_HOURS=6
WINDOW_MINUTES=15
MIN_ABS_SPIKE=20
SPIKE_STD_MULTIPLIER=3.0
SPIKE_MEAN_MULTIPLIER=2.0
SAMPLE_LIMIT=40
MAX_STREAMS=8
DEDUP_MINUTES=60
```

### Alert Analyzer
```bash
ALERT_BATCH_WINDOW_SEC=300
ALERT_BATCH_MAX=20
ALERT_DEDUP_WINDOW=3600
FLUSH_INTERVAL=60
```

### Аутентификация
```bash
AUTH_ENABLED=true
AUTH_USERNAME=admin
AUTH_PASSWORD=${auto_sre_auth_password}
```

### Прочее
```bash
LOG_LEVEL=INFO
SHUTDOWN_TIMEOUT=30
```

---

## Развёртывание

### Прод (Ansible)
```bash
ansible-playbook -i inventory/all-01-prod auto-sre.yaml
```

Разворачивает в `/opt/docker/auto-sre/`:
- `docker-compose.yml` (рендерится из шаблона)
- `.env` (рендерится из шаблона)
- Данные PostgreSQL: `/opt/data/auto-sre/postgres`
- Данные Kafka: `/opt/data/auto-sre/kafka`

### Локальная разработка

```bash
# Прод-подобный стек
cd /opt/docker/auto-sre
docker compose up -d --build

# Дев-стек (с тестовыми контейнерами)
cd /opt/docker/auto-sre
docker compose -f docker-compose.dev.yml up -d --build
```

---

## Мониторинг

### Ключевые метрики для алертинга

| Метрика | Предупреждение | Критический |
|--------|---------|----------|
| `auto_sre_up` | < 1 | < 1 |
| `auto_sre_last_scan_error` | = 1 | = 1 |
| `auto_sre_vl_circuit_breaker_state` | = 1 | = 2 |
| `auto_sre_llm_circuit_breaker_state` | = 1 | = 2 |
| `auto_sre_kafka_consumer_lag` | > 1000 | > 10000 |
| `auto_sre_kafka_outbox_pending` | > 100 | > 1000 |
| `auto_sre_db_pool_checked_out / auto_sre_db_pool_size` | > 0.8 | > 0.95 |
| `auto_sre_http_requests_total{status=~"5.."}` | rate > 0.05 | rate > 0.1 |

### Правила Prometheus

См. `files/sre-agent/alerting/auto-sre-rules.yaml` — более 25 готовых правил.

---

## Безопасность

- **Basic Auth** на всех эндпоинтах, кроме `/api/health`, `/metrics`, `/static/*`
- **Учётные данные** через Ansible inventory (не в репозитории)
- **Сеть**: bridge-сеть `auto-sre-net`, без внешнего доступа, кроме sre-agent:8096
- **TLS**: не настроен в compose (добавьте для прода)

---

## Масштабирование

| Компонент | Сейчас | Стратегия масштабирования |
|-----------|---------|------------------|
| sre-agent API | 1 replica | Горизонтальное (stateless) |
| sre-agent scanner | 1 instance | Один экземпляр (APScheduler) |
| Kafka consumer | 1 instance | Партиционирование по ключу находки |
| alert-analyzer | 1 instance | Горизонтальное (stateless-вебхук) |
| PostgreSQL | 1 instance | Read-реплики для запросов |
| Kafka | 1 broker (KRaft) | Добавить брокеров, увеличить число партиций |

---

## Сценарии отказов

| Отказ | Детекция | Восстановление |
|---------|-----------|----------|
| VL недоступен | Circuit breaker открыт | Автоповтор после таймаута |
| LLM недоступен | Circuit breaker открыт | Пропустить анализ, сохранить сырые данные |
| Kafka лежит | Ошибки продюсера | Outbox сохраняет события |
| PostgreSQL лежит | Провал проверки здоровья | Автопереподключение |
| Падение сканера | Нет сканов | `/api/health` показывает устаревшие данные |

---

## Планы развития

1. **Мультиарендность** — изоляция по namespace
2. **ML для корреляции алертов** — заменить LLM обученной моделью
3. **Дашборды** — Grafana-дашборды для находок/алертов
4. **Уведомления** — интеграция со Slack/Telegram/PagerDuty
5. **Исторические тренды** — сезонная корректировка базовой линии
6. **Привязка runbook'ов** — автоматическое прикрепление runbook'ов к находкам