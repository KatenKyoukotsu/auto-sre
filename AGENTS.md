# Auto SRE — Ansible Role for LLM-Powered Log Anomaly Detection

## Project Type
Ansible role that deploys three Docker services (defined in `templates/docker-compose.yml.j2`):
- **postgres** (port 5432): PostgreSQL 16 for findings/blog/outbox storage
- **kafka** (port 9092): KRaft mode Kafka for event streaming (findings, blog, scan events)
- **sre-agent** (port 8096): FastAPI + APScheduler for periodic log scanning, LLM analysis, web UI, metrics

> **Note**: The `README.md` is outdated — it references `mcp-vl` and SQLite, but the actual deployment uses PostgreSQL + Kafka + direct Victoria Logs HTTP (no MCP proxy).

## Key Commands

### Deploy (production)
```bash
ansible-playbook -i inventory/all-01-prod auto-sre.yaml
```

### Manual scan trigger after deploy
```bash
curl -X POST http://<host>:8096/api/trigger/scan
```

### Local development (run containers directly)
```bash
cd /opt/docker/auto-sre  # or wherever docker-compose.yml lives
docker compose up -d --build
docker compose logs -f sre-agent
```

> The compose expects `files/sre-agent/migrations/01_init.sql` for PostgreSQL schema init (mounted at `/docker-entrypoint-initdb.d`).

### Health checks
```bash
curl http://<host>:8096/api/health       # sre-agent (public)
curl http://<host>:8096/metrics          # Prometheus metrics (public)
```

## Architecture Notes

| Component | Path | Purpose |
|-----------|------|---------|
| Ansible tasks | `tasks/main.yml` | Copies sources, renders `.env` + `docker-compose.yml`, runs `docker compose up` |
| sre-agent API | `files/sre-agent/app.py` | FastAPI + APScheduler + Basic Auth + Prometheus middleware |
| SRE logic | `files/sre-agent/agent.py` | Rolling baseline anomaly detection + LLM analysis + dedup + Kafka outbox |
| LLM client | `files/sre-agent/llm.py` | LiteLLM (OpenAI-compatible) with retry + circuit breaker |
| VL client | `files/sre-agent/vl.py` | Direct HTTP to Victoria Logs with retry + circuit breaker (httpx) |
| Kafka producer | `files/sre-agent/kafka_producer.py` | Idempotent producer + transactional outbox pattern |
| Kafka consumer | `files/sre-agent/kafka_consumer.py` | Background worker for LLM analysis + lag metrics |
| Storage | `files/sre-agent/store.py` | PostgreSQL via SQLAlchemy 2.0 async (findings, blog, outbox) |
| Metrics | `files/sre-agent/metrics.py` | 90+ Prometheus metrics definitions |
| Alerting | `files/sre-agent/alerting/auto-sre-rules.yaml` | PrometheusRule: 25+ rules |
| Templates | `templates/env.j2`, `docker-compose.yml.j2` | Rendered by Ansible with inventory vars |

## Entrypoints
- **postgres**: `postgres` (healthcheck via `pg_isready`)
- **kafka**: `kafka` KRaft (healthcheck via `kafka-broker-api-versions`)
- **sre-agent**: `uvicorn app:app --host 0.0.0.0 --port 8096`

## Environment Variables (from `templates/env.j2`)
All injected via Ansible inventory. Key ones:

### Victoria Logs
- `VL_URL`, `VL_USERNAME`, `VL_PASSWORD` — Victoria Logs connection
- `VL_TIMEOUT`, `VL_MAX_RETRIES`, `VL_RETRY_BASE_DELAY` — VL client tuning
- `VL_CIRCUIT_BREAKER_THRESHOLD`, `VL_CIRCUIT_BREAKER_TIMEOUT` — circuit breaker

### LLM (LiteLLM)
- `LITELLM_URL`, `LITELLM_API_KEY`, `LITELLM_MODEL` — LLM backend
- `LLM_TIMEOUT`, `LLM_MAX_RETRIES`, `LLM_RETRY_BASE_DELAY` — LLM client tuning
- `LLM_CIRCUIT_BREAKER_THRESHOLD`, `LLM_CIRCUIT_BREAKER_TIMEOUT` — circuit breaker

### Kafka
- `KAFKA_BOOTSTRAP_SERVERS` — default `kafka:9092`
- `KAFKA_TOPIC_FINDINGS`, `KAFKA_TOPIC_BLOG`, `KAFKA_TOPIC_SCAN_EVENTS`
- `KAFKA_CONSUMER_GROUP` — default `auto-sre-worker`

### PostgreSQL
- `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`

### Scheduling & Detection
- `SCAN_INTERVAL_MINUTES` — periodic scan interval (default 15)
- `BLOG_HOUR`, `BLOG_MINUTE`, `TZ` — daily blog schedule (default 07:30 Europe/Moscow)
- `ERROR_PATTERN`, `HISTORY_HOURS`, `WINDOW_MINUTES`, `SPIKE_STD_MULTIPLIER`, `MIN_ABS_SPIKE`, `DEDUP_MINUTES`, `MAX_STREAMS`

### Auth
- `AUTH_ENABLED` — `true`/`false` (default true)
- `AUTH_USERNAME`, `AUTH_PASSWORD` — Basic Auth credentials

### Other
- `LOG_LEVEL` — default `INFO`
- `SHUTDOWN_TIMEOUT` — graceful shutdown wait (default 30s)

## Data Persistence
- **PostgreSQL**: `/opt/data/auto-sre/postgres` (volume)
- **Kafka**: `/opt/data/auto-sre/kafka` (volume)
- **App sources**: `/opt/docker/auto-sre/`

## REST API (sre-agent, port 8096)
| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/findings` | Basic | List findings |
| GET | `/api/findings/{id}` | Basic | Single finding |
| POST | `/api/findings/{id}/ack` | Basic | Acknowledge finding |
| GET | `/api/blog` | Basic | Blog posts |
| GET | `/api/blog/status` | Basic | Blog generation status |
| POST | `/api/trigger/scan` | Basic | Manual anomaly scan |
| POST | `/api/trigger/full-scan` | Basic | Full historical scan |
| POST | `/api/trigger/blog` | Basic | Manual blog generation |
| GET | `/api/health` | **None** | Service status + last scan info |
| GET | `/metrics` | **None** | Prometheus metrics |
| GET | `/` | Basic | Web UI: anomaly wall |
| GET | `/blog` | Basic | Web UI: blog digest |
| GET | `/static/*` | **None** | Static assets |

## Detection Logic (agent.py)
1. Fetches error counts per stream in rolling windows (`HISTORY_HOURS` back, `WINDOW_MINUTES` each)
2. Computes baseline (mean/std), flags spike if `current > max(mean + 3*std, 2*mean)` AND `current >= MIN_ABS_SPIKE (20)`
3. Creates finding + outbox event atomically in PostgreSQL transaction
4. Outbox poller sends to Kafka topic `auto-sre.findings`
5. Consumer worker picks up, runs LLM analysis, updates finding in DB
6. Deduplicates by stream within `DEDUP_MINUTES` (default 60)

## Development Notes
- **Python version**: 3.12 (slim base image)
- **Dependencies**: See `files/sre-agent/requirements.txt`
- **No test suite** — verify via manual API calls and log inspection
- **Lint/typecheck**: None configured
- **Code style**: Standard Python, async/await throughout

## Common Tasks for Agents
- **Modify detection thresholds** → Edit constants in `agent.py` (lines ~25-38) or add env vars
- **Change LLM prompt** → Edit `llm.py` methods `analyze_logs` / `write_blog_post`
- **Add API endpoint** → Edit `app.py`, follow existing patterns (add to `AUTH_EXCLUDE_PATHS` if public)
- **Adjust scheduling** → Change `SCAN_INTERVAL_MINUTES` or cron in `app.py` lifespan
- **Debug VL connectivity** → Check `vl.py` logs at `LOG_LEVEL=DEBUG`
- **Debug Kafka** → Check `kafka_producer.py` / `kafka_consumer.py` logs, consumer lag via metrics
- **Add Prometheus metric** → Define in `metrics.py`, import and use in relevant module
- **Add alert rule** → Edit `alerting/auto-sre-rules.yaml`

## Gotchas
- `VL_PASSWORD` / `POSTGRES_PASSWORD` / `AUTH_PASSWORD` come from Ansible inventory, not hardcoded
- `VL_MODE` removed — only direct HTTP to VL (no MCP)
- **PostgreSQL required** — SQLite removed; schema auto-created via `init_db()` on startup
- **Kafka required** — outbox pattern for guaranteed delivery; consumer runs LLM analysis async
- **Basic Auth** enabled by default; `/api/health`, `/metrics`, `/static/*` excluded
- **Graceful shutdown**: waits for background tasks (`SHUTDOWN_TIMEOUT`), closes VL client, Kafka producer/consumer, DB pool
- **Outbox pattern**: finding + Kafka event written in same DB transaction; poller sends every 5s
- **Russian logging output** in code — expected, not a bug
- **Metrics cardinality**: HTTP path normalized (`/api/findings/{id}`) to avoid label explosion