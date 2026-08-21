# Справочник API — Auto SRE

## Базовые URL

| Окружение | sre-agent | alert-analyzer |
|-------------|-----------|----------------|
| Продакшен | `http://<host>:8096` | `http://<host>:8097` |
| Разработка | `http://localhost:8096` | `http://localhost:8097` |

---

## Аутентификация

Все эндпоинты (кроме отмеченных) требуют **HTTP Basic Auth**:

```bash
curl -u admin:password http://host:8096/api/findings
```

**Исключённые пути** (без аутентификации):
- `/api/health`
- `/metrics`
- `/static/*`
- `/favicon.ico`

---

## sre-agent API (порт 8096)

### Проверка здоровья и метрики

#### `GET /api/health`
**Auth**: Нет  
**Ответ**: `200 OK`
```json
{
  "status": "ok",
  "time": "2025-01-21T10:30:00Z",
  "last_scan": "2025-01-21T10:15:00Z",
  "last_error": null,
  "scan_interval_minutes": 15,
  "model": "gemma-4-12B-it-qat-q4_0-gguf",
  "vl_mode": "HttpVlClient",
  "latest_finding": {...},
  "latest_blog_post": {...}
}
```

#### `GET /metrics`
**Auth**: Нет  
**Ответ**: текстовый формат Prometheus

---

### Находки

#### `GET /api/findings`
**Auth**: Basic  
**Параметры запроса**:
| Параметр | Тип | По умолчанию | Описание |
|-------|------|---------|-------------|
| `limit` | int | 50 | Максимальное число результатов |

**Ответ**: `200 OK`
```json
[
  {
    "id": 123,
    "created_at": "2025-01-21T10:15:00Z",
    "severity": "high",
    "service": "payment-api",
    "title": "Всплеск ошибок: payment-api",
    "summary": "Число ошибок в текущем окне (45) значительно выше базовой линии (12.3 в среднем за окно).",
    "possible_cause": "Database connection pool exhaustion",
    "recommended_action": "Increase pool size, check for connection leaks",
    "confidence": 0.87,
    "raw_data": {...},
    "acknowledged": false
  }
]
```

#### `GET /api/findings/{id}`
**Auth**: Basic  
**Ответ**: `200 OK` или `404 Not Found`

#### `POST /api/findings/{id}/ack`
**Auth**: Basic  
**Ответ**: `200 OK`
```json
{"ok": true, "id": 123}
```

---

### Блог

#### `GET /api/blog`
**Auth**: Basic  
**Параметры запроса**:
| Параметр | Тип | По умолчанию | Описание |
|-------|------|---------|-------------|
| `limit` | int | 30 | Максимальное число результатов |

**Ответ**: `200 OK`
```json
[
  {
    "id": 1,
    "created_at": "2025-01-21T07:30:00Z",
    "title": "SRE-дайджест",
    "content": "# SRE-дайджест\n\n## Обзор\nЗа последние 24 часа..."
  }
]
```

#### `GET /api/blog/status`
**Auth**: Basic  
**Ответ**: `200 OK`
```json
{
  "status": "idle",
  "error": null
}
```
Значения статуса: `idle`, `generating`

---

### Ручной запуск

#### `POST /api/trigger/scan`
**Auth**: Basic  
**Ответ**: `200 OK`
```json
{"ok": true, "message": "Скан аномалий запущен"}
```

#### `POST /api/trigger/full-scan`
**Auth**: Basic  
**Тело запроса** (опционально):
```json
{
  "start": "2025-01-20T00:00:00Z",
  "end": "2025-01-21T00:00:00Z"
}
```
**Ответ**: `200 OK`
```json
{"ok": true, "message": "Полное сканирование запущено"}
```

#### `POST /api/trigger/blog`
**Auth**: Basic  
**Ответ**: `200 OK`
```json
{"ok": true, "message": "Генерация блог-поста запущена"}
```

---

### Веб-интерфейс

| Путь | Auth | Описание |
|------|------|-------------|
| `GET /` | Basic | Стена аномалий |
| `GET /blog` | Basic | Блог-дайджест |
| `GET /static/*` | Нет | Статические файлы |

---

## alert-analyzer API (порт 8097)

### Проверка здоровья и метрики

#### `GET /api/health`
**Auth**: Нет  
**Ответ**: `200 OK`
```json
{
  "status": "ok",
  "time": "2025-01-21T10:30:00Z",
  "llm_model": "gemma-4-12B-it-qat-q4_0-gguf",
  "buffer_stats": {
    "groups": 3,
    "total_alerts": 15
  }
}
```

#### `GET /metrics`
**Auth**: Нет  
**Ответ**: текстовый формат Prometheus

---

### Вебхук

#### `POST /webhook`
**Auth**: Basic (опционально через `AUTH_ENABLED=false`)  
**Content-Type**: `application/json`  
**Тело запроса**: полезная нагрузка вебхука Alertmanager

```json
{
  "receiver": "auto-sre",
  "status": "firing",
  "alerts": [
    {
      "labels": {
        "alertname": "HighErrorRate",
        "severity": "critical",
        "instance": "payment-api-1",
        "namespace": "production",
        "cluster": "k8s-prod",
        "service": "payment-api"
      },
      "annotations": {
        "summary": "Error rate > 5%",
        "description": "Payment API error rate exceeded threshold"
      },
      "startsAt": "2025-01-21T10:15:00Z",
      "endsAt": "0001-01-01T00:00:00Z",
      "generatorURL": "http://prometheus:9090/graph?...",
      "fingerprint": "a1b2c3d4e5f6",
      "status": "firing"
    }
  ],
  "groupLabels": {"alertname": "HighErrorRate"},
  "commonLabels": {"severity": "critical"},
  "commonAnnotations": {},
  "externalURL": "http://alertmanager:9093",
  "version": "4",
  "groupKey": "{}/{severity=\"critical\"}:{alertname=\"HighErrorRate\"}",
  "truncatedAlerts": 0
}
```

**Ответ**: `200 OK`
```json
{
  "status": "ok",
  "received": 1,
  "batched": 1,
  "analyzed": 1,
  "deduped": 0
}
```

#### `POST /webhook/test`
**Auth**: Basic  
**Ответ**: `200 OK` — обрабатывает, но не сохраняет

---

### Анализы алертов

#### `GET /api/analyses`
**Auth**: Basic  
**Параметры запроса**:
| Параметр | Тип | По умолчанию | Описание |
|-------|------|---------|-------------|
| `limit` | int | 50 | Максимальное число результатов |
| `alertname` | string | — | Фильтр по имени алерта |
| `severity` | string | — | Фильтр по severity |
| `status` | string | — | Фильтр по статусу (firing/resolved) |

**Ответ**: `200 OK`
```json
[
  {
    "id": 1,
    "created_at": "2025-01-21T10:15:00Z",
    "alert_fingerprint": "a1b2c3d4e5f6",
    "alertname": "HighErrorRate",
    "severity": "critical",
    "cluster": "k8s-prod",
    "namespace": "production",
    "service": "payment-api",
    "status": "firing",
    "correlated_group": "[\"a1b2c3d4e5f6\", \"f6e5d4c3b2a1\"]",
    "root_cause": "Database connection pool exhaustion due to connection leak in payment service",
    "suggested_actions": [
      "Increase PostgreSQL max_connections",
      "Fix connection leak in payment service",
      "Enable connection pooling (PgBouncer)"
    ],
    "confidence": {"score": 0.92},
    "raw_alerts": [...],
    "llm_model": "gemma-4-12B-it-qat-q4_0-gguf"
  }
]
```

#### `GET /api/analyses/{id}`
**Auth**: Basic  
**Ответ**: `200 OK` или `404 Not Found`

---

### Статистика и управление

#### `GET /api/stats`
**Auth**: Basic  
**Ответ**: `200 OK`
```json
{
  "unresolved_critical": 3,
  "buffer": {
    "groups": 3,
    "total_alerts": 15
  },
  "config": {
    "batch_window_sec": 300,
    "batch_max": 20,
    "flush_interval": 60
  }
}
```

#### `POST /api/flush`
**Auth**: Basic  
**Ответ**: `200 OK`
```json
{"flushed": 5}
```

---

## Модели данных

### Finding (находка)
```json
{
  "id": 123,
  "created_at": "2025-01-21T10:15:00Z",
  "severity": "critical|high|medium|low",
  "service": "string|null",
  "title": "string",
  "summary": "string|null",
  "possible_cause": "string|null",
  "recommended_action": "string|null",
  "confidence": "float|null",
  "raw_data": "object|null",
  "acknowledged": "boolean"
}
```

### BlogPost
```json
{
  "id": 1,
  "created_at": "2025-01-21T07:30:00Z",
  "title": "string",
  "content": "string (markdown)"
}
```

### AlertAnalysis
```json
{
  "id": 1,
  "created_at": "2025-01-21T10:15:00Z",
  "alert_fingerprint": "string",
  "alertname": "string",
  "severity": "critical|high|medium|low",
  "cluster": "string|null",
  "namespace": "string|null",
  "service": "string|null",
  "status": "firing|resolved",
  "correlated_group": "string (JSON array)",
  "root_cause": "string|null",
  "suggested_actions": "string|null",
  "confidence": "string (JSON)",
  "raw_alerts": "string (JSON array)",
  "llm_model": "string|null"
}
```

### AlertmanagerPayload (входные данные вебхука)
```json
{
  "receiver": "string",
  "status": "firing|resolved",
  "alerts": [
    {
      "labels": {
        "alertname": "string",
        "severity": "string",
        "instance": "string|null",
        "namespace": "string|null",
        "cluster": "string|null",
        "service": "string|null"
      },
      "annotations": {
        "summary": "string|null",
        "description": "string|null",
        "runbook_url": "string|null"
      },
      "startsAt": "ISO8601",
      "endsAt": "ISO8601|null",
      "generatorURL": "string|null",
      "fingerprint": "string",
      "status": "firing|resolved"
    }
  ],
  "groupLabels": "object",
  "commonLabels": "object",
  "commonAnnotations": "object",
  "externalURL": "string|null",
  "version": "string",
  "groupKey": "string|null",
  "truncatedAlerts": "int"
}
```

---

## Ошибки

### 400 Bad Request
```json
{"detail": "Invalid request body"}
```

### 401 Unauthorized
```json
{"detail": "Not authenticated"}
```
Заголовки: `WWW-Authenticate: Basic realm="Auto SRE"`

### 404 Not Found
```json
{"detail": "Finding not found"}
```

### 500 Internal Server Error
```json
{"detail": "Internal server error"}
```

---

## Ограничение частоты запросов

Явное ограничение частоты запросов не реализовано. Рекомендуется добавить на уровне обратного прокси (nginx/traefik).

---

## Пагинация

Эндпоинты списков поддерживают `?limit=N` (по умолчанию 50 для находок, 30 для блога и анализов).  
Курсорная и offset-пагинация отсутствуют — результаты упорядочены по `created_at DESC`.

---

## Вебхуки

### Конфигурация Alertmanager

```yaml
receivers:
  - name: 'auto-sre-analyzer'
    webhook_configs:
      - url: 'http://alert-analyzer:8097/webhook'
        send_resolved: true
        http_config:
          basic_auth:
            username: 'admin'
            password: '${ALERT_ANALYZER_PASSWORD}'
route:
  receiver: 'auto-sre-analyzer'
  group_by: ['alertname', 'cluster', 'namespace']
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h
```