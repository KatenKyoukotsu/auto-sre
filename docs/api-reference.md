# API Reference — Auto SRE

## Base URLs

| Environment | sre-agent | alert-analyzer |
|-------------|-----------|----------------|
| Production | `http://<host>:8096` | `http://<host>:8097` |
| Development | `http://localhost:8096` | `http://localhost:8097` |

---

## Authentication

All endpoints (except noted) require **HTTP Basic Auth**:

```bash
curl -u admin:password http://host:8096/api/findings
```

**Excluded paths** (no auth required):
- `/api/health`
- `/metrics`
- `/static/*`
- `/favicon.ico`

---

## sre-agent API (port 8096)

### Health & Metrics

#### `GET /api/health`
**Auth**: None  
**Response**: `200 OK`
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
**Auth**: None  
**Response**: Prometheus text format

---

### Findings

#### `GET /api/findings`
**Auth**: Basic  
**Query Parameters**:
| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `limit` | int | 50 | Max results |

**Response**: `200 OK`
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
**Response**: `200 OK` or `404 Not Found`

#### `POST /api/findings/{id}/ack`
**Auth**: Basic  
**Response**: `200 OK`
```json
{"ok": true, "id": 123}
```

---

### Blog

#### `GET /api/blog`
**Auth**: Basic  
**Query Parameters**:
| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `limit` | int | 30 | Max results |

**Response**: `200 OK`
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
**Response**: `200 OK`
```json
{
  "status": "idle",
  "error": null
}
```
Status values: `idle`, `generating`

---

### Triggers

#### `POST /api/trigger/scan`
**Auth**: Basic  
**Response**: `200 OK`
```json
{"ok": true, "message": "Скан аномалий запущен"}
```

#### `POST /api/trigger/full-scan`
**Auth**: Basic  
**Body** (optional):
```json
{
  "start": "2025-01-20T00:00:00Z",
  "end": "2025-01-21T00:00:00Z"
}
```
**Response**: `200 OK`
```json
{"ok": true, "message": "Полное сканирование запущено"}
```

#### `POST /api/trigger/blog`
**Auth**: Basic  
**Response**: `200 OK`
```json
{"ok": true, "message": "Генерация блог-поста запущена"}
```

---

### Web UI

| Path | Auth | Description |
|------|------|-------------|
| `GET /` | Basic | Anomaly wall |
| `GET /blog` | Basic | Blog digest |
| `GET /static/*` | None | Static assets |

---

## alert-analyzer API (port 8097)

### Health & Metrics

#### `GET /api/health`
**Auth**: None  
**Response**: `200 OK`
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
**Auth**: None  
**Response**: Prometheus text format

---

### Webhook

#### `POST /webhook`
**Auth**: Basic (optional via `AUTH_ENABLED=false`)  
**Content-Type**: `application/json`  
**Body**: Alertmanager webhook payload

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

**Response**: `200 OK`
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
**Response**: `200 OK` — Processes but doesn't store

---

### Alert Analyses

#### `GET /api/analyses`
**Auth**: Basic  
**Query Parameters**:
| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `limit` | int | 50 | Max results |
| `alertname` | string | — | Filter by alert name |
| `severity` | string | — | Filter by severity |
| `status` | string | — | Filter by status (firing/resolved) |

**Response**: `200 OK`
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
**Response**: `200 OK` or `404 Not Found`

---

### Stats & Control

#### `GET /api/stats`
**Auth**: Basic  
**Response**: `200 OK`
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
**Response**: `200 OK`
```json
{"flushed": 5}
```

---

## Data Models

### Finding
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

### AlertmanagerPayload (webhook input)
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

## Error Responses

### 400 Bad Request
```json
{"detail": "Invalid request body"}
```

### 401 Unauthorized
```json
{"detail": "Not authenticated"}
```
Headers: `WWW-Authenticate: Basic realm="Auto SRE"`

### 404 Not Found
```json
{"detail": "Finding not found"}
```

### 500 Internal Server Error
```json
{"detail": "Internal server error"}
```

---

## Rate Limits

No explicit rate limiting implemented. Recommended to add via reverse proxy (nginx/traefik).

---

## Pagination

List endpoints support `?limit=N` (default 50 for findings, 30 for blog/analyses).  
No cursor/offset pagination — results ordered by `created_at DESC`.

---

## Webhooks

### Alertmanager Configuration

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