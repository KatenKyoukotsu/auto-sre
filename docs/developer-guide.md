# Руководство разработчика — Auto SRE

## Среда разработки

### Требования
- Python 3.12+
- Docker + Docker Compose
- PostgreSQL 16 (контейнер)
- Kafka 7.6+ KRaft (контейнер)
- Victoria Logs (внешний)
- LiteLLM (внешний)

### Структура проекта
```
files/
├── common/
│   └── llm_client.py          # Общий LLM-клиент (переиспользуемый)
├── sre-agent/                 # Основной сервис сканирования
│   ├── app.py                 # FastAPI + APScheduler + middleware
│   ├── agent.py               # Основная логика сканирования
│   ├── store.py               # SQLAlchemy 2.0 async + outbox
│   ├── vl.py                  # HTTP-клиент Victoria Logs
│   ├── kafka_producer.py      # Продюсер + поллер outbox
│   ├── kafka_consumer.py      # Воркер-консюмер (LLM-анализ)
│   ├── metrics.py             # Все метрики Prometheus
│   └── models.py              # Модели SQLAlchemy
└── alert-analyzer/            # Сервис анализа алертов
    ├── app.py                 # Приёмник вебхуков на FastAPI
    ├── analyzer.py            # Батчинг + LLM-корреляция
    ├── models.py              # Pydantic-модели Alertmanager
    └── store.py               # Хранилище анализов алертов
```

---

## Добавление новой метрики

### 1. Определение в `metrics.py`
```python
from prometheus_client import Counter, Histogram, Gauge

my_new_metric = Histogram(
    "auto_sre_my_new_metric",
    "Description of what this measures",
    ["label1", "label2"],
    buckets=(0.1, 0.5, 1.0, 2.5, 5.0, 10.0)
)
```

### 2. Импорт и использование
```python
from metrics import my_new_metric

# В вашем коде
my_new_metric.labels(label1="value1", label2="value2").observe(1.5)
```

### 3. Правила
- Используйте префикс `auto_sre_`
- Включайте единицы измерения в имя (`_seconds`, `_bytes`, `_total`)
- Добавляйте метки для контроля кардинальности
- Документируйте в code review

---

## Добавление нового API-эндпоинта (sre-agent)

### 1. Добавление маршрута в `app.py`
```python
@app.get("/api/new-endpoint")
async def api_new_endpoint(param: str = "default", _: bool = Depends(verify_auth)):
    # Публичный эндпоинт: не добавляйте Depends(verify_auth)
    # Если публичный — добавьте в AUTH_EXCLUDE_PATHS
    return {"result": "ok"}
```

### 2. Обновление исключений аутентификации (если эндпоинт публичный)
```python
AUTH_EXCLUDE_PATHS = {
    "/api/health",
    "/metrics",
    "/static",
    "/favicon.ico",
    "/api/new-endpoint",  # Добавьте сюда
}
```

### 3. Нормализация пути для метрик
```python
# В metrics_middleware
if path.startswith("/api/new-endpoint/"):
    path = "/api/new-endpoint/{param}"
```

---

## Изменения базы данных

### 1. Обновление модели в `models.py` / `store.py`
```python
class NewTable(Base):
    __tablename__ = "new_table"
    
    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    # ... поля
    
    __table_args__ = (
        Index("idx_new_table_field", "field"),
    )
```

### 2. Создание миграции
```sql
-- files/sre-agent/migrations/02_new_table.sql
CREATE TABLE IF NOT EXISTS new_table (
    id SERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- ...
);
CREATE INDEX idx_new_table_field ON new_table(field);
```

### 3. Обновление `init_db()` при необходимости
```python
async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
```

---

## Добавление нового топика Kafka

### 1. Определение в шаблоне Compose
```yaml
# templates/docker-compose.yml.j2
environment:
  - KAFKA_TOPIC_NEW=auto-sre.new-topic
```

### 2. Добавление в продюсер
```python
# kafka_producer.py
KAFKA_TOPIC_NEW = os.environ.get("KAFKA_TOPIC_NEW", "auto-sre.new-topic")

async def send_new_event(self, data: dict) -> None:
    await self.send(KAFKA_TOPIC_NEW, data, key=data.get("key"))
```

### 3. Добавление в консюмер
```python
# kafka_consumer.py
self._consumer = AIOKafkaConsumer(
    KAFKA_TOPIC_FINDINGS,
    KAFKA_TOPIC_NEW,  # Добавьте сюда
    # ...
)

async def _process_message(self, topic: str, key: str, value: dict):
    if topic == KAFKA_TOPIC_NEW:
        self._track_task(asyncio.create_task(self._handle_new(value)))
```

---

## Разработка LLM-промптов

### Рекомендации
1. **Системный промпт**: определите роль, правила и формат вывода
2. **Пользовательский промпт**: шаблон с понятными плейсхолдерами
3. **Вывод**: строгая JSON-схема прямо в промпте
4. **Валидация**: используйте `complete_json()` + проверку ключей

### Пример паттерна
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
    
    # Проверка обязательных ключей
    required = ["field1", "field2"]
    for key in required:
        if key not in result:
            result[key] = DEFAULT_VALUE
    
    return result
```

---

## Локальное тестирование изменений

### 1. Сборка образа
```bash
docker build -f files/sre-agent/Dockerfile -t auto-sre-agent-test files/sre-agent
```

### 2. Запуск с тестовой БД
```bash
# Запуск тестового PostgreSQL
docker run -d --name test-pg \
  -e POSTGRES_DB=auto_sre \
  -e POSTGRES_USER=auto_sre \
  -e POSTGRES_PASSWORD=test \
  -p 5432:5432 \
  postgres:16-alpine

# Ожидание готовности
sleep 5

# Запуск теста
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
    # ... ваш тест
    await close_db()
asyncio.run(test())
"
```

### 3. Просмотр логов
```bash
docker compose -f docker-compose.dev.yml logs -f sre-agent
```

---

## Типовые паттерны

### Асинхронная операция с БД с записью метрик
```python
async def my_db_operation(self, data: dict) -> int:
    start_time = time.time()
    update_pool_metrics()
    try:
        async with self._session_maker() as session:
            # ... операция
            await session.commit()
            _record_db_query("my_operation", start_time, True)
            return obj.id
    except Exception:
        _record_db_query("my_operation", start_time, False)
        raise
```

### Использование circuit breaker
```python
# Проверка перед вызовом
if not self._circuit.can_proceed():
    raise MyError("Circuit breaker OPEN")

# После успеха
self._circuit.record_success()

# После сбоя
self._circuit.record_failure()
```

### Отслеживание задач при корректной остановке
```python
# В lifespan из app.py
_running_tasks: set[asyncio.Task] = set()

def _track_task(task: asyncio.Task):
    _running_tasks.add(task)
    task.add_done_callback(_running_tasks.discard)

# При запуске фоновой задачи
_track_task(asyncio.create_task(my_background_job()))

# При остановке
if _running_tasks:
    await asyncio.wait_for(
        asyncio.gather(*_running_tasks, return_exceptions=True),
        timeout=SHUTDOWN_TIMEOUT
    )
```

---

## Советы по отладке

### Включение debug-логирования
```bash
LOG_LEVEL=DEBUG docker compose up
```

### Инспекция базы данных
```bash
docker exec -it auto-sre-postgres psql -U auto_sre -d auto_sre -c "SELECT * FROM findings ORDER BY created_at DESC LIMIT 10;"
```

### Проверка Kafka
```bash
# Список топиков
docker exec auto-sre-kafka kafka-topics --bootstrap-server localhost:9092 --list

# Отставание консюмера
docker exec auto-sre-kafka kafka-consumer-groups --bootstrap-server localhost:9092 --describe --group auto-sre-worker

# Чтение сообщений
docker exec auto-sre-kafka kafka-console-consumer --bootstrap-server localhost:9092 --topic auto-sre.findings --from-beginning --max-messages 5
```

### Отладка метрик
```bash
# Все метрики
curl -s http://localhost:8096/metrics | grep auto_sre

# Конкретная метрика
curl -s http://localhost:8096/metrics | grep auto_sre_scan
```

---

## Частые проблемы

| Проблема | Решение |
|-------|----------|
| Ошибки подключения `asyncpg` | Проверьте формат `DATABASE_URL`, убедитесь, что PostgreSQL готов |
| Консюмер Kafka не получает сообщения | Проверьте существование топика и совпадение группы консюмеров |
| LLM возвращает невалидный JSON | Улучшите промпт, добавьте валидацию, проверьте `max_tokens` |
| Высокое потребление памяти | Проверьте незакрытые сессии, добавьте `expire_on_commit=False` |
| Метрики не появляются | Убедитесь, что `prometheus_client` импортирован, проверьте порядок middleware |
| Аутентификация не работает | Проверьте `AUTH_ENABLED`, формат заголовка `Basic base64(user:pass)` |

---

## Советы по производительности

1. **Батчинг операций с БД** — используйте `session.add_all()` для массовых вставок
2. **Ограничивайте результаты запросов** — всегда применяйте `.limit()`
3. **Переиспользуйте HTTP-клиенты** — один `httpx.AsyncClient` с пулом соединений
4. **Асинхронность повсюду** — никогда не используйте блокирующие вызовы в async-функциях
5. **Кардинальность метрик** — нормализуйте пути, избегайте высококардинальных меток
