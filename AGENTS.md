# Auto SRE — Ansible Role for LLM-Powered Log Anomaly Detection

## Project Type
Ansible role that deploys four Docker services (defined in `templates/docker-compose.yml.j2`):
- **postgres** (5432): PostgreSQL 16 — findings, blog_posts, outbox_events, alert_analysis
- **kafka** (9092): KRaft single-node — topics `auto-sre.findings`, `auto-sre.blog`, `auto-sre.scan-events`
- **sre-agent** (8096): FastAPI + APScheduler — VL log scanning, anomaly detection, LLM analysis, web UI
- **alert-analyzer** (8097): FastAPI — Alertmanager webhook ingestion, batching + LLM correlation

> README.md and docs/ are current. Code comments/logs are in Russian — expected, not a bug.

## Key Commands

### Deploy (production)
```bash
ansible-playbook -i inventory/all-01-prod auto-sre.yaml --ask-vault-pass
```
Ansible copies `files/` → `/opt/docker/auto-sre/{common,sre-agent,alert-analyzer,frontend}/`, renders `.env` + compose, runs `docker compose down && up -d --build`. Note: deployed layout flattens `files/` — that's why prod and dev compose both build sre-agent/alert-analyzer from the `files/` root (`dockerfile: sre-agent/Dockerfile`).

### Local development (repo root, macOS-friendly)
```bash
cp templates/docker-compose.dev.yml.j2 docker-compose.dev.yml   # template has NO Jinja vars — plain copy works
cat > .env   # needed: VL_*, LITELLM_*, POSTGRES_*, AUTH_*, ALERT_* (see templates/env.j2 for names/defaults)
docker compose -f docker-compose.dev.yml up -d --build
docker compose -f docker-compose.dev.yml logs -f sre-agent
```
Dev compose mounts `files/sre-agent` and `files/alert-analyzer` at `/app` with `uvicorn --reload`, plus `files/frontend` at `/app/static` — Python edits apply without rebuild; requirements.txt changes need `--build`.

### Health checks
```bash
curl http://localhost:8096/api/health   # sre-agent (no auth)
curl http://localhost:8097/api/health   # alert-analyzer (no auth)
curl http://localhost:8096/metrics      # Prometheus (no auth)
```

### Manual scan trigger after deploy
```bash
curl -u user:pass -X POST http://<host>:8096/api/trigger/scan
```

### Browser UI debugging
`opencode.json` wires `.claude/skills/playwright-cli` — use `npx playwright-cli open/goto/snapshot/click/find` against `http://localhost:8096/` (wall) and `/blog`. Install chromium once: `npx playwright install chromium`.

## Architecture Notes

| Component | Path | Purpose |
|-----------|------|---------|
| Ansible tasks | `tasks/main.yml` | Copy sources, render `.env` + compose, `docker compose up` |
| Shared LLM client | `files/common/llm_client.py` | OpenAI SDK → LiteLLM server, retry + circuit breaker |
| sre-agent API | `files/sre-agent/app.py` | FastAPI + APScheduler + Basic Auth middleware + Prometheus |
| SRE logic | `files/sre-agent/agent.py` | Rolling-baseline detection + dedup + outbox writes |
| LLM client | `files/sre-agent/llm.py` | Prompts `ANALYZE_SYSTEM_PROMPT` / `BLOG_SYSTEM_PROMPT`, JSON extraction |
| VL client | `files/sre-agent/vl.py` | Direct HTTP to Victoria Logs LogsQL (`/select/logsql/*`) |
| Kafka producer | `files/sre-agent/kafka_producer.py` | Idempotent producer + outbox poller (`process_outbox`) |
| Kafka consumer | `files/sre-agent/kafka_consumer.py` | Background worker: LLM analysis of findings, lag metrics |
| Storage | `files/sre-agent/store.py` | SQLAlchemy 2.0 async models + `init_db()` (create_all) |
| Frontend | `files/frontend/` | Static UI (no build step): `index.html`/`blog.html` shells, `css/{tokens,base,animations}.css`, ES-modules in `js/`, vendored marked+DOMPurify in `vendor/`. Served from `/app/static`; `/` and `/blog` are FileResponse with `Cache-Control: no-cache`, `/static` is NoCacheStaticFiles (ETag revalidation) |
| Alert batching | `files/alert-analyzer/analyzer.py` | Time/size windowing, fingerprint dedup, `ALERT_ANALYSIS_SYSTEM_PROMPT` |
| PrometheusRule | `files/sre-agent/alerting/auto-sre-rules.yaml` | 25+ alerting rules |

- **Build contexts**: both sre-agent and alert-analyzer build from the `files/` root (`dockerfile: sre-agent/Dockerfile`, `dockerfile: alert-analyzer/Dockerfile`) so they can `COPY common/` and, for sre-agent, `COPY frontend/ ./static/`. `files/common/llm_client.py` does `from metrics import ...` — it depends on alert-analyzer's `metrics.py` being importable; don't "fix" this import, it's why the context is the parent dir.
- **Frontend is Jinja-free**: `/` and `/blog` serve static shells; all data comes from the JSON API and is rendered client-side (`js/wall.js`, `js/blog.js`). Live wall polls `/api/findings?limit=100` every 15s with client-side diff by id (new cards get `.card-enter` + stagger `--i`). Blog typewriter plays only for posts that appeared while the page is open (sessionStorage seen-set); click skips it.
- **Data flow**: finding + outbox event written in one DB transaction → poller sends to Kafka every 5s → consumer runs LLM analysis and updates the finding row.
- **Alembic is configured but unused**: `alembic.ini` + `migrations/env.py` exist (target = `store.Base`), but there are no `versions/`; schema comes from SQL mounts + `init_db()` create_all. `alembic.ini` hardcodes the DB URL.

## Environment Variables (templates/env.j2)
Rendered by Ansible from inventory (`auto_sre_*` vars with fallbacks). Compose passes them via `${VAR}` interpolation.

- **VL**: `VL_URL`, `VL_USERNAME`, `VL_PASSWORD`
- **LLM**: `LITELLM_URL`, `LITELLM_API_KEY`, `LITELLM_MODEL` (default model is a local Gemma gguf)
- **Postgres**: `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` → injected as `DATABASE_URL` by compose
- **Kafka**: `KAFKA_BOOTSTRAP_SERVERS`, `KAFKA_TOPIC_FINDINGS/BLOG/SCAN_EVENTS`, `KAFKA_CONSUMER_GROUP`
- **Scheduling**: `SCAN_INTERVAL_MINUTES` (15), `BLOG_HOUR`/`BLOG_MINUTE` (7:30), `TZ` (Europe/Moscow)
- **Auth**: `AUTH_ENABLED` (true), `AUTH_USERNAME`, `AUTH_PASSWORD`
- **Alert-analyzer**: `ALERT_BATCH_WINDOW_SEC` (300), `ALERT_BATCH_MAX` (20), `ALERT_DEDUP_WINDOW` (3600), `FLUSH_INTERVAL` (60)
- **Other**: `LOG_LEVEL` (INFO)

> Tuning vars exist only as code defaults, NOT wired through env.j2/compose: `ERROR_PATTERN`, `HISTORY_HOURS`, `WINDOW_MINUTES`, `SPIKE_STD_MULTIPLIER`, `SPIKE_MEAN_MULTIPLIER`, `MIN_ABS_SPIKE`, `SAMPLE_LIMIT`, `MAX_STREAMS`, `DEDUP_MINUTES`, `FULL_SCAN_*` (agent.py:40-53), `LLM_TIMEOUT`/`LLM_MAX_RETRIES`/circuit-breaker vars (llm.py, common/llm_client.py), `VL_TIMEOUT`/retries (vl.py), `SHUTDOWN_TIMEOUT` (app.py). Changing them means editing defaults or adding pass-through in compose.
> Dead config: `KAFKA_TOPIC_ALERTS` / `KAFKA_CONSUMER_GROUP_ALERTS` in env.j2 — nothing consumes them; alert-analyzer doesn't use Kafka despite having aiokafka in requirements.

## Detection Logic (agent.py)
1. Rolling windows: `HISTORY_HOURS` back, `WINDOW_MINUTES` each; per-stream error counts via LogsQL
2. Spike if `current > max(mean + SPIKE_STD_MULTIPLIER*std, SPIKE_MEAN_MULTIPLIER*mean)` AND `current >= MIN_ABS_SPIKE (20)`
3. Finding + outbox event in one PostgreSQL transaction; poller → Kafka → consumer → LLM → row update
4. Dedup by stream within `DEDUP_MINUTES` (60); full-scan variant enumerates all streams over a range

## REST API
sre-agent (:8096): `/api/findings[/{id}[/ack]]`, `/api/blog[/status]`, `/api/trigger/{scan,full-scan,blog}`, `/api/health`*, `/metrics`*, `/` + `/blog` (web UI), `/static/*`*
alert-analyzer (:8097): `/webhook`, `/webhook/test`, `/api/analyses[/{id}]`, `/api/stats`, `/api/flush`, `/api/health`*, `/metrics`*

\* no auth. Everything else behind Basic Auth when `AUTH_ENABLED=true` (alert-analyzer: per-route `Depends(verify_auth)`; sre-agent: middleware with `AUTH_EXCLUDE_PATHS`).

## Data Persistence
- PostgreSQL: `/opt/data/auto-sre/postgres`, Kafka: `/opt/data/auto-sre/kafka` (prod bind mounts — won't work on macOS Docker Desktop; dev compose uses named volumes)
- Sources on server: `/opt/docker/auto-sre/`

## Common Tasks for Agents
- **Detection thresholds** → constants atop `agent.py` (see env-var caveat above)
- **LLM prompts** → `llm.py` (sre-agent), `ALERT_ANALYSIS_*PROMPT` in `analyzer.py` (alert-analyzer)
- **New endpoint** → `app.py`; public path must be added to `AUTH_EXCLUDE_PATHS` (sre-agent); fetch wrapper goes to `files/frontend/js/api.js`
- **UI changes** → static shells + ES-modules in `files/frontend/`; animations/timings in `css/animations.css` (tokens in `tokens.css`); no build step — edit and refresh
- **New metric** → define in `metrics.py` first (both services have their own)
- **Schema change** → update `store.py` models AND `migrations/01_init.sql` (fresh DBs are built from SQL, existing DBs from create_all — keep both in sync)
- **Alert rules** → `files/sre-agent/alerting/auto-sre-rules.yaml`

## Gotchas
- **Prod compose passes `AUTH_*` only to alert-analyzer, not sre-agent** → in prod sre-agent gets empty password → `expected_auth=None` → Basic Auth silently disabled (app.py middleware skips when no password). Wire the vars in if auth is required.
- **Kafka KRaft requires `CLUSTER_ID`** (added to both templates). If an existing kafka data dir was formatted with a different ID, container dies with InconsistentClusterId.
- **aiokafka constraints**: no `max_in_flight_requests_per_connection` kwarg (crashes producer init); snappy compression requires `aiokafka[snappy]` extra (already in requirements.txt — don't downgrade to plain `aiokafka`).
- **Postgres init mounts**: each migration mounted individually (`01_sre_agent.sql`, `02_alert_analyzer.sql`) — entrypoint glob is non-recursive; later mounts override earlier ones at the same path.
- **Secrets come from Ansible inventory** (`victorialogs_password`, `auto_sre_postgres_password`, `auto_sre_auth_password`) — never hardcode.
- **Labeled metrics need `.labels(...)` before inc/observe** — unlabeled call raises at runtime.
- **HTTP path normalization in metrics middleware** (`/api/findings/{id}`, `/api/{endpoint}`) — keep it, prevents label explosion.
- **VL unreachable locally** → scans hang through retry/backoff/circuit-breaker (minutes), UI and health stay fine; check `sre.vl` log lines.
- **`npx playwright-cli eval` takes an expression, not statements** — `a; b` throws SyntaxError. Use `(() => { a; return b; })()`. Discarding eval output with `>/dev/null 2>&1` hides these failures — don't.
- **No test suite, no lint/typecheck config** — verify via manual API calls, `/metrics`, and Playwright UI checks.
