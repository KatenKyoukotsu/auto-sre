# Auto SRE — Architecture Documentation

## System Overview

Auto SRE is a dual-purpose observability platform:
1. **Log Anomaly Detection** — Scans Victoria Logs for error spikes, analyzes with LLM
2. **Alert Analysis** — Consumes Alertmanager webhooks, correlates alerts with LLM

---

## Component Diagram

```mermaid
flowchart TB
    subgraph platform ["AUTO SRE PLATFORM"]
        direction TB

        VL[("Victoria Logs<br/>direct HTTP")]

        subgraph sreagent ["sre-agent"]
            direction LR
            SCANNER["Scanner<br/>(APScheduler)"]
            REST["REST API"]
            BLOGGEN["Blog Generator"]
        end

        PG[("PostgreSQL<br/>findings · blog_posts · outbox_events")]
        OUTBOX["Outbox Poller<br/>(5s interval)"]
        KAFKA["Kafka<br/>(3 topics)"]
        CONSUMER["Kafka Consumer<br/>(LLM analysis)"]

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
            WH["Webhook Receiver"] --> BATCHER["AlertBatcher<br/>(time + size)"]
            BATCHER --> LLMA["LLM analysis"]
            LLMA --> APG[("PostgreSQL<br/>alert_analysis")]
        end

        WH -.-> ALERTS["Kafka<br/>(alerts)"]
        BATCHER -.-> ALERTS
    end
```

---

## Data Flow

### 1. Log Anomaly Detection (sre-agent)

```mermaid
flowchart TD
    T["Every SCAN_INTERVAL_MINUTES (default 15 min)"]
    T --> A["1. Get active streams from VL (last 15 min)"]
    A --> B["2. For each stream (max 8):<br/>a. query error count history (6h / 15 min windows)<br/>b. compute baseline (mean / std)<br/>c. check current window vs baseline"]
    B --> C{"d. spike?"}
    C -- yes --> D["fetch samples, send to LLM"]
    C -- no --> E
    D --> E["3. Deduplicate by service (60 min window)"]
    E --> F["4. Store finding + outbox event (single transaction)"]
    F --> G["5. Outbox poller → Kafka (findings topic)"]
    G --> H["6. Consumer picks up → LLM analysis → update finding"]
```

**Spike Detection Formula**:
```
threshold = max(mean + 3*std, 2*mean)
is_spike = current > threshold AND current >= 20
```

### 2. Blog Generation (sre-agent)

```mermaid
flowchart TD
    T["Daily at 07:30 (Europe/Moscow)"]
    T --> A["1. Fetch findings from last 24h"]
    A --> B["2. Build digest (count, summaries)"]
    B --> C["3. Send to LLM with blog prompt"]
    C --> D["4. Store blog post in PostgreSQL"]
```

### 3. Alert Analysis (alert-analyzer)

```mermaid
flowchart TD
    W["Alertmanager webhook → /webhook"]
    W --> A["1. Validate Basic Auth (optional)"]
    A --> B["2. Parse AlertmanagerPayload (Pydantic)"]
    B --> C["3. For each alert:<br/>a. deduplicate by fingerprint (1h window)<br/>b. group by (alertname, severity, cluster, ns, service)<br/>c. add to AlertBatcher (time window 5 min, max 20)"]
    C --> D{"4. Batch ready?"}
    D -- yes --> E["LLM analysis"]
    D -- "periodic flush (60s)" --> E
    E --> F["5. Store alert_analysis per fingerprint"]
```

---

## Database Schema

### PostgreSQL Tables

```sql
-- Core findings from log scanning
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

-- Blog posts
CREATE TABLE blog_posts (
    id          SERIAL PRIMARY KEY,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    title       VARCHAR(500) NOT NULL,
    content     TEXT NOT NULL
);

CREATE INDEX idx_blog_created ON blog_posts(created_at DESC);

-- Transactional outbox for Kafka
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

-- Alert analysis (alert-analyzer)
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
    correlated_group TEXT,  -- JSON array of fingerprints
    root_cause      TEXT,
    suggested_actions TEXT,
    confidence      TEXT,  -- JSON with scores
    raw_alerts      TEXT NOT NULL,  -- JSON
    llm_model       VARCHAR(100)
);

CREATE INDEX idx_alert_analysis_created ON alert_analysis(created_at DESC);
CREATE INDEX idx_alert_analysis_fingerprint ON alert_analysis(alert_fingerprint);
CREATE INDEX idx_alert_analysis_alertname ON alert_analysis(alertname);
CREATE INDEX idx_alert_analysis_status ON alert_analysis(status);
```

---

## Kafka Topics

| Topic | Partitions | Retention | Producer | Consumer |
|-------|------------|-----------|----------|----------|
| `auto-sre.findings` | 1 | 168h | sre-agent (outbox) | sre-agent consumer |
| `auto-sre.blog` | 1 | 168h | sre-agent (outbox) | sre-agent consumer |
| `auto-sre.scan-events` | 1 | 168h | sre-agent (outbox) | sre-agent consumer |
| `auto-sre.alerts` | 1 | 168h | alert-analyzer (webhook) | *future* |

**Producer Config**: `acks=all`, `enable_idempotence=true`, `compression_type=snappy`

---

## Services

### sre-agent (port 8096)

| Endpoint | Auth | Description |
|--------|------|-------------|
| `GET /` | Basic | Anomaly wall UI |
| `GET /blog` | Basic | Blog digest UI |
| `GET /api/findings` | Basic | List findings |
| `GET /api/findings/{id}` | Basic | Single finding |
| `POST /api/findings/{id}/ack` | Basic | Acknowledge finding |
| `GET /api/blog` | Basic | List blog posts |
| `GET /api/blog/status` | Basic | Blog generation status |
| `POST /api/trigger/scan` | Basic | Manual scan |
| `POST /api/trigger/full-scan` | Basic | Full historical scan |
| `POST /api/trigger/blog` | Basic | Manual blog generation |
| `GET /api/health` | None | Health check |
| `GET /metrics` | None | Prometheus metrics |

**Background Jobs**:
- `scan_job` — Interval (15min): runs `Agent.scan()`
- `daily_blog` — Cron (07:30 MSK): runs `Agent.generate_daily_blog()`

### alert-analyzer (port 8097)

| Endpoint | Auth | Description |
|--------|------|-------------|
| `POST /webhook` | Basic* | Alertmanager webhook |
| `POST /webhook/test` | Basic* | Test webhook |
| `GET /api/analyses` | Basic | List alert analyses |
| `GET /api/analyses/{id}` | Basic | Single analysis |
| `GET /api/stats` | Basic | Buffer stats, config |
| `POST /api/flush` | Basic | Manual flush |
| `GET /api/health` | None | Health check |
| `GET /metrics` | None | Prometheus metrics |

*Optional: `AUTH_ENABLED=false` disables auth*

---

## Configuration

All via environment variables (rendered from `templates/env.j2` by Ansible):

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

### Scheduling
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

### Auth
```bash
AUTH_ENABLED=true
AUTH_USERNAME=admin
AUTH_PASSWORD=${auto_sre_auth_password}
```

### Other
```bash
LOG_LEVEL=INFO
SHUTDOWN_TIMEOUT=30
```

---

## Deployment

### Production (Ansible)
```bash
ansible-playbook -i inventory/all-01-prod auto-sre.yaml
```

Deploys to `/opt/docker/auto-sre/` with:
- `docker-compose.yml` (rendered from template)
- `.env` (rendered from template)
- PostgreSQL data: `/opt/data/auto-sre/postgres`
- Kafka data: `/opt/data/auto-sre/kafka`

### Local Development

```bash
# Production-like stack
cd /opt/docker/auto-sre
docker compose up -d --build

# Development stack (with test containers)
cd /opt/docker/auto-sre
docker compose -f docker-compose.dev.yml up -d --build
```

---

## Monitoring

### Key Metrics to Alert On

| Metric | Warning | Critical |
|--------|---------|----------|
| `auto_sre_up` | < 1 | < 1 |
| `auto_sre_last_scan_error` | = 1 | = 1 |
| `auto_sre_vl_circuit_breaker_state` | = 1 | = 2 |
| `auto_sre_llm_circuit_breaker_state` | = 1 | = 2 |
| `auto_sre_kafka_consumer_lag` | > 1000 | > 10000 |
| `auto_sre_kafka_outbox_pending` | > 100 | > 1000 |
| `auto_sre_db_pool_checked_out / auto_sre_db_pool_size` | > 0.8 | > 0.95 |
| `auto_sre_http_requests_total{status=~"5.."}` | rate > 0.05 | rate > 0.1 |

### Prometheus Rules

See `files/sre-agent/alerting/auto-sre-rules.yaml` for 25+ pre-defined rules.

---

## Security

- **Basic Auth** on all endpoints except `/api/health`, `/metrics`, `/static/*`
- **Credentials** via Ansible inventory (not in repo)
- **Network**: Bridge network `auto-sre-net`, no external exposure except sre-agent:8096
- **TLS**: Not configured in compose (add for production)

---

## Scaling Considerations

| Component | Current | Scaling Strategy |
|-----------|---------|------------------|
| sre-agent API | 1 replica | Horizontal (stateless) |
| sre-agent scanner | 1 instance | Single (APScheduler) |
| Kafka consumer | 1 instance | Partition by finding key |
| alert-analyzer | 1 instance | Horizontal (stateless webhook) |
| PostgreSQL | 1 instance | Read replicas for queries |
| Kafka | 1 broker (KRaft) | Add brokers, increase partitions |

---

## Failure Modes

| Failure | Detection | Recovery |
|---------|-----------|----------|
| VL unreachable | Circuit breaker open | Auto-retry after timeout |
| LLM unavailable | Circuit breaker open | Skip analysis, store raw |
| Kafka down | Producer errors | Outbox retains events |
| PostgreSQL down | Health check fail | Auto-reconnect |
| Scanner crash | No scans | `/api/health` shows stale |

---

## Future Enhancements

1. **Multi-tenant support** — namespace isolation
2. **Alert correlation ML** — replace LLM with trained model
3. **Dashboard** — Grafana dashboards for findings/alerts
4. **Notification** — Slack/Telegram/PagerDuty integration
5. **Historical trends** — Seasonal baseline adjustment
6. **Runbook linking** — Auto-attach runbooks to findings