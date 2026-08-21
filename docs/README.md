# Auto SRE Documentation Index

## Overview

Auto SRE is an LLM-powered observability platform with two main services:

| Service | Port | Purpose |
|---------|------|---------|
| **sre-agent** | 8096 | Log anomaly detection, web UI, blog generation |
| **alert-analyzer** | 8097 | Alertmanager webhook consumer, alert correlation |

---

## Documentation

| Document | Description |
|----------|-------------|
| [Architecture](architecture.md) | System design, data flows, database schema, Kafka topics |
| [API Reference](api-reference.md) | Complete REST API docs for both services |
| [Deployment](deployment.md) | Production (Ansible) and local development setup |
| [Code Review](code-review.md) | Detailed code review with 12 findings and action plan |

---

## Quick Links

### For Developers
- [Architecture Overview](architecture.md#component-diagram)
- [API Reference](api-reference.md)
- [Code Review Findings](code-review.md#-critical-issues)

### For Operators
- [Deployment Guide](deployment.md)
- [Monitoring Setup](deployment.md#monitoring-setup)
- [Troubleshooting](deployment.md#troubleshooting)

### For Security
- [Security Checklist](deployment.md#security-hardening)
- [Auth Configuration](architecture.md#configuration)

---

## System Status

| Component | Status | Notes |
|-----------|--------|-------|
| sre-agent | ✅ Production ready | Log scanning, web UI, blog generation |
| alert-analyzer | ✅ Production ready | Alertmanager webhook, LLM analysis |
| PostgreSQL | ✅ Schema defined | Migrations in `files/*/migrations/` |
| Kafka | ✅ Configured | KRaft mode, 4 topics |
| Metrics | ✅ 53+ metrics | Prometheus format at `/metrics` |
| Alerting | ✅ 25+ rules | In `files/sre-agent/alerting/` |
| Auth | ✅ Basic Auth | Optional, configurable |

---

## Getting Started

### Local Development (5 minutes)
```bash
cd auto-sre
cat > .env <<'EOF'
VL_URL=http://host.docker.internal:9428
VL_USERNAME=admin
VL_PASSWORD=test
LITELLM_URL=http://host.docker.internal:4000
LITELLM_API_KEY=sk-test
LITELLM_MODEL=gemma-4-12B-it-qat-q4_0-gguf
POSTGRES_DB=auto_sre
POSTGRES_USER=auto_sre
POSTGRES_PASSWORD=test
KAFKA_BOOTSTRAP_SERVERS=kafka:9092
AUTH_ENABLED=false
LOG_LEVEL=DEBUG
EOF

docker compose -f docker-compose.dev.yml up -d --build
```

### Production Deploy
```bash
ansible-playbook -i inventory/all-01-prod auto-sre.yaml --ask-vault-pass
```

---

## Key Metrics to Watch

```promql
# Service health
auto_sre_up

# Scan health
auto_sre_last_scan_error
auto_sre_last_scan_timestamp

# Dependency health
auto_sre_vl_circuit_breaker_state
auto_sre_llm_circuit_breaker_state

# Kafka health
auto_sre_kafka_consumer_lag
auto_sre_kafka_outbox_pending

# Database health
auto_sre_db_pool_checked_out / auto_sre_db_pool_size

# Alert analyzer
auto_sre_alert_webhook_received_total
auto_sre_alert_analysis_duration_seconds
```

---

## Code Structure

```
auto-sre/
├── docs/                          # Documentation
├── templates/                     # Ansible templates
│   ├── env.j2                     # Environment variables
│   ├── docker-compose.yml.j2      # Production compose
│   └── docker-compose.dev.yml.j2  # Development compose
├── tasks/
│   └── main.yml                   # Ansible tasks
├── files/
│   ├── common/
│   │   └── llm_client.py          # Shared LLM client
│   ├── sre-agent/                 # Main service
│   │   ├── app.py                 # FastAPI + APScheduler
│   │   ├── agent.py               # Scanning logic
│   │   ├── store.py               # PostgreSQL + outbox
│   │   ├── vl.py                  # Victoria Logs client
│   │   ├── llm.py                 # LLM wrapper (legacy)
│   │   ├── kafka_producer.py      # Idempotent producer
│   │   ├── kafka_consumer.py      # Background worker
│   │   ├── metrics.py             # 53+ Prometheus metrics
│   │   ├── models.py              # SQLAlchemy models
│   │   └── migrations/            # SQL migrations
│   └── alert-analyzer/            # Alert service
│       ├── app.py                 # FastAPI webhook
│       ├── analyzer.py            # Batching + LLM
│       ├── models.py              # Pydantic models
│       ├── store.py               # PostgreSQL
│       └── migrations/
└── README.md                      # Deployment guide (ru)
```

---

## Contributing

1. **Code style**: Standard Python, async/await, type hints
2. **Metrics**: Add to `metrics.py` before instrumenting
3. **Database**: Add migration in `migrations/` + update models
4. **Tests**: No test suite yet - verify manually via API
5. **Docs**: Update relevant `.md` in `docs/`

---

## Support

- **Issues**: Check [Code Review](code-review.md) for known issues
- **Logs**: `docker compose logs -f sre-agent`
- **Metrics**: `curl http://host:8096/metrics`
- **Health**: `curl http://host:8096/api/health`