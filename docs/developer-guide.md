# Developer Guide — Auto SRE

## Development Environment

### Prerequisites
- Python 3.12+
- Docker + Docker Compose
- PostgreSQL 16 (container)
- Kafka 7.6+ KRaft (container)
- Victoria Logs (external)
- LiteLLM (external)

### Project Structure
```
files/
├── common/
│   └── llm_client.py          # Shared LLM client (reusable)
├── sre-agent/                 # Main scanning service
│   ├── app.py                 # FastAPI + APScheduler + middleware
│   ├── agent.py               # Core scanning logic
│   ├── store.py               # SQLAlchemy 2.0 async + outbox
│   ├── vl.py                  # Victoria Logs HTTP client
│   ├── kafka_producer.py      # Producer + outbox poller
│   ├── kafka_consumer.py      # Consumer worker (LLM analysis)
│   ├── metrics.py             # All Prometheus metrics
│   └── models.py              # SQLAlchemy models
└── alert-analyzer/            # Alert analysis service
    ├── app.py                 # FastAPI webhook receiver
    ├── analyzer.py            # Batching + LLM correlation
    ├── models.py              # Pydantic Alertmanager models
    └── store.py               # Alert analysis storage
```

---

## Adding New Metrics

### 1. Define in `metrics.py`
```python
from prometheus_client import Counter, Histogram, Gauge

my_new_metric = Histogram(
    "auto_sre_my_new_metric",
    "Description of what this measures",
    ["label1", "label2"],
    buckets=(0.1, 0.5, 1.0, 2.5, 5.0, 10.0)
)
```

### 2. Import and Use
```python
from metrics import my_new_metric

# In your code
my_new_metric.labels(label1="value1", label2="value2").observe(1.5)
```

### 3. Rules
- Use `auto_sre_` prefix
- Include units in name (`_seconds`, `_bytes`, `_total`)
- Add labels for cardinality control
- Document in code review

---

## Adding New API Endpoint (sre-agent)

### 1. Add Route in `app.py`
```python
@app.get("/api/new-endpoint")
async def api_new_endpoint(param: str = "default", _: bool = Depends(verify_auth)):
    # Public endpoint: don't add Depends(verify_auth)
    # Add to AUTH_EXCLUDE_PATHS if public
    return {"result": "ok"}
```

### 2. Update Auth Exclusions (if public)
```python
AUTH_EXCLUDE_PATHS = {
    "/api/health",
    "/metrics",
    "/static",
    "/favicon.ico",
    "/api/new-endpoint",  # Add here
}
```

### 3. Normalize Path for Metrics
```python
# In metrics_middleware
if path.startswith("/api/new-endpoint/"):
    path = "/api/new-endpoint/{param}"
```

---

## Database Changes

### 1. Update Model in `models.py` / `store.py`
```python
class NewTable(Base):
    __tablename__ = "new_table"
    
    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    # ... fields
    
    __table_args__ = (
        Index("idx_new_table_field", "field"),
    )
```

### 2. Create Migration
```sql
-- files/sre-agent/migrations/02_new_table.sql
CREATE TABLE IF NOT EXISTS new_table (
    id SERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- ...
);
CREATE INDEX idx_new_table_field ON new_table(field);
```

### 3. Update `init_db()` if needed
```python
async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
```

---

## Adding New Kafka Topic

### 1. Define in Compose Template
```yaml
# templates/docker-compose.yml.j2
environment:
  - KAFKA_TOPIC_NEW=auto-sre.new-topic
```

### 2. Add to Producer
```python
# kafka_producer.py
KAFKA_TOPIC_NEW = os.environ.get("KAFKA_TOPIC_NEW", "auto-sre.new-topic")

async def send_new_event(self, data: dict) -> None:
    await self.send(KAFKA_TOPIC_NEW, data, key=data.get("key"))
```

### 3. Add to Consumer
```python
# kafka_consumer.py
self._consumer = AIOKafkaConsumer(
    KAFKA_TOPIC_FINDINGS,
    KAFKA_TOPIC_NEW,  # Add here
    # ...
)

async def _process_message(self, topic: str, key: str, value: dict):
    if topic == KAFKA_TOPIC_NEW:
        self._track_task(asyncio.create_task(self._handle_new(value)))
```

---

## LLM Prompt Engineering

### Guidelines
1. **System prompt**: Define role, rules, output format
2. **User prompt**: Template with clear placeholders
3. **Output**: Strict JSON schema in prompt
4. **Validation**: Use `complete_json()` + validate keys

### Example Pattern
```python
MY_SYSTEM_PROMPT = """
You are an SRE. Analyze X and return JSON:
{
  "field1": "type",
  "field2": "type"
}
Rules: ...
"""

async def my_analysis(self, data: dict) -> dict:
    user_prompt = TEMPLATE.format(data=json.dumps(data))
    result = await self.llm.complete_json(MY_SYSTEM_PROMPT, user_prompt)
    
    # Validate required keys
    required = ["field1", "field2"]
    for key in required:
        if key not in result:
            result[key] = DEFAULT_VALUE
    
    return result
```

---

## Testing Changes Locally

### 1. Build Image
```bash
docker build -f files/sre-agent/Dockerfile -t auto-sre-agent-test files/sre-agent
```

### 2. Run with Test DB
```bash
# Start test PostgreSQL
docker run -d --name test-pg \
  -e POSTGRES_DB=auto_sre \
  -e POSTGRES_USER=auto_sre \
  -e POSTGRES_PASSWORD=test \
  -p 5432:5432 \
  postgres:16-alpine

# Wait for ready
sleep 5

# Run tests
docker run --rm --network host \
  -e DATABASE_URL=postgresql+asyncpg://auto_sre:test@localhost:5432/auto_sre \
  -e VL_URL=http://test \
  -e LITELLM_URL=http://test \
  auto-sre-agent-test python -c "
import asyncio
from store import init_db, Store
async def test():
    await init_db()
    store = Store()
    # ... your test
    await close_db()
asyncio.run(test())
"
```

### 3. Check Logs
```bash
docker compose -f docker-compose.dev.yml logs -f sre-agent
```

---

## Common Patterns

### Async Database Operation with Metrics
```python
async def my_db_operation(self, data: dict) -> int:
    start_time = time.time()
    update_pool_metrics()
    try:
        async with self._session_maker() as session:
            # ... operation
            await session.commit()
            _record_db_query("my_operation", start_time, True)
            return obj.id
    except Exception:
        _record_db_query("my_operation", start_time, False)
        raise
```

### Circuit Breaker Usage
```python
# Check before call
if not self._circuit.can_proceed():
    raise MyError("Circuit breaker OPEN")

# After success
self._circuit.record_success()

# After failure
self._circuit.record_failure()
```

### Graceful Shutdown Tracking
```python
# In app.py lifespan
_running_tasks: set[asyncio.Task] = set()

def _track_task(task: asyncio.Task):
    _running_tasks.add(task)
    task.add_done_callback(_running_tasks.discard)

# When starting background work
_track_task(asyncio.create_task(my_background_job()))

# On shutdown
if _running_tasks:
    await asyncio.wait_for(
        asyncio.gather(*_running_tasks, return_exceptions=True),
        timeout=SHUTDOWN_TIMEOUT
    )
```

---

## Debugging Tips

### Enable Debug Logging
```bash
LOG_LEVEL=DEBUG docker compose up
```

### Inspect Database
```bash
docker exec -it auto-sre-postgres psql -U auto_sre -d auto_sre -c "SELECT * FROM findings ORDER BY created_at DESC LIMIT 10;"
```

### Check Kafka
```bash
# List topics
docker exec auto-sre-kafka kafka-topics --bootstrap-server localhost:9092 --list

# Consumer lag
docker exec auto-sre-kafka kafka-consumer-groups --bootstrap-server localhost:9092 --describe --group auto-sre-worker

# Consume messages
docker exec auto-sre-kafka kafka-console-consumer --bootstrap-server localhost:9092 --topic auto-sre.findings --from-beginning --max-messages 5
```

### Metrics Debugging
```bash
# All metrics
curl -s http://localhost:8096/metrics | grep auto_sre

# Specific metric
curl -s http://localhost:8096/metrics | grep auto_sre_scan
```

---

## Common Pitfalls

| Issue | Solution |
|-------|----------|
| `asyncpg` connection errors | Check `DATABASE_URL` format, ensure PostgreSQL ready |
| Kafka consumer not receiving | Verify topic exists, consumer group matches |
| LLM returns invalid JSON | Improve prompt, add validation, check `max_tokens` |
| High memory usage | Check for unclosed sessions, add `expire_on_commit=False` |
| Metrics not appearing | Verify `prometheus_client` imported, middleware order |
| Auth not working | Check `AUTH_ENABLED`, header format `Basic base64(user:pass)` |

---

## Performance Tips

1. **Batch DB operations** - Use `session.add_all()` for bulk inserts
2. **Limit query results** - Always use `.limit()` on queries
3. **Reuse HTTP clients** - Single `httpx.AsyncClient` with connection pool
4. **Async throughout** - Never use blocking calls in async functions
5. **Metrics cardinality** - Normalize paths, avoid high-cardinality labels