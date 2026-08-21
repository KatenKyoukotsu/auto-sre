# Deployment Guide — Auto SRE

## Prerequisites

### Infrastructure
- **Docker** 24+ and **Docker Compose** 2.20+
- **Ansible** 2.14+ (for production deploy)
- **PostgreSQL** 16 (via container)
- **Kafka** 7.6+ KRaft mode (via container)
- **Victoria Logs** — external, accessible via HTTP
- **LiteLLM** — external, OpenAI-compatible endpoint

### Network
- Ports: `8096` (sre-agent), `8097` (alert-analyzer), `5432` (PostgreSQL), `9092` (Kafka)
- Internal Docker network: `auto-sre-net`

---

## Production Deployment (Ansible)

### Inventory Setup

```ini
# inventory/all-01-prod/hosts.yml
all:
  children:
    log:
      hosts:
        sre-prod-01:
          ansible_host: 10.0.1.100
      vars:
        victorialogs_url: "http://10.148.14.12:9428"
        victorialogs_username: "admin"
        victorialogs_password: "{{ vault_victorialogs_password }}"
        
        auto_sre_litellm_url: "http://10.148.14.10:4000"
        auto_sre_litellm_api_key: "{{ vault_litellm_api_key }}"
        auto_sre_llm_model: "gemma-4-12B-it-qat-q4_0-gguf"
        
        auto_sre_postgres_password: "{{ vault_postgres_password }}"
        auto_sre_auth_password: "{{ vault_auth_password }}"
        
        auto_sre_scan_interval_minutes: 15
        auto_sre_blog_hour: 7
        auto_sre_blog_minute: 30
        auto_sre_tz: "Europe/Moscow"
```

### Vault Secrets

```bash
ansible-vault create group_vars/all/vault.yml
```

```yaml
vault_victorialogs_password: "super-secret-vl-password"
vault_litellm_api_key: "sk-litellm-xxx"
vault_postgres_password: "super-secret-postgres-password"
vault_auth_password: "super-secret-auth-password"
```

### Deploy

```bash
# Full deploy
ansible-playbook -i inventory/all-01-prod auto-sre.yaml --ask-vault-pass

# Dry run
ansible-playbook -i inventory/all-01-prod auto-sre.yaml --check --ask-vault-pass
```

### What Ansible Does

1. Creates `/opt/data/auto-sre/` and `/opt/docker/auto-sre/`
2. Copies source files to `/opt/docker/auto-sre/`
3. Renders `.env` from `templates/env.j2`
4. Renders `docker-compose.yml` from `templates/docker-compose.yml.j2`
4. Runs `docker compose down --remove-orphans`
5. Runs `docker compose up -d --build`

---

## Local Development

### Quick Start

```bash
# Clone and enter
cd auto-sre

# Render compose (uses .env in current dir)
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

# Start dev stack (with test containers)
docker compose -f docker-compose.dev.yml up -d --build

# View logs
docker compose -f docker-compose.dev.yml logs -f sre-agent
```

### Dev Compose Features

`docker-compose.dev.yml` includes:
- **Testcontainers** for PostgreSQL/Kafka (ephemeral)
- **Hot reload** via volume mounts
- **Debug ports** exposed
- **Resource limits** for local machine

---

## Configuration Reference

All config via environment variables (in `.env` or Ansible inventory):

### Required
| Variable | Description |
|----------|-------------|
| `VL_URL` | Victoria Logs endpoint |
| `VL_PASSWORD` | VL auth password |
| `LITELLM_URL` | LiteLLM endpoint |
| `LITELLM_API_KEY` | LiteLLM API key |
| `POSTGRES_PASSWORD` | PostgreSQL password |
| `AUTH_PASSWORD` | Basic Auth password |

### Optional (with defaults)
```bash
# Victoria Logs
VL_URL=http://10.148.14.12:9428
VL_USERNAME=admin
VL_TIMEOUT=60
VL_MAX_RETRIES=3
VL_RETRY_BASE_DELAY=1.0
VL_CIRCUIT_BREAKER_THRESHOLD=5
VL_CIRCUIT_BREAKER_TIMEOUT=30

# LLM
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

# Kafka
KAFKA_BOOTSTRAP_SERVERS=kafka:9092
KAFKA_TOPIC_FINDINGS=auto-sre.findings
KAFKA_TOPIC_BLOG=auto-sre.blog
KAFKA_TOPIC_SCAN_EVENTS=auto-sre.scan-events
KAFKA_CONSUMER_GROUP=auto-sre-worker
OUTBOX_POLL_INTERVAL=5

# PostgreSQL
POSTGRES_DB=auto_sre
POSTGRES_USER=auto_sre
POSTGRES_PASSWORD=auto_sre

# Scheduling
SCAN_INTERVAL_MINUTES=15
BLOG_HOUR=7
BLOG_MINUTE=30
TZ=Europe/Moscow

# Detection
ERROR_PATTERN="i(error*) OR i(exception*) OR i(panic*) OR i(fatal*) OR i(traceback*)"
HISTORY_HOURS=6
WINDOW_MINUTES=15
MIN_ABS_SPIKE=20
SPIKE_STD_MULTIPLIER=3.0
SPIKE_MEAN_MULTIPLIER=2.0
SAMPLE_LIMIT=40
MAX_STREAMS=8
DEDUP_MINUTES=60

# Alert Analyzer
ALERT_BATCH_WINDOW_SEC=300
ALERT_BATCH_MAX=20
ALERT_DEDUP_WINDOW=3600
FLUSH_INTERVAL=60

# Auth
AUTH_ENABLED=true
AUTH_USERNAME=admin
AUTH_PASSWORD=changeme

# Other
LOG_LEVEL=INFO
SHUTDOWN_TIMEOUT=30
```

---

## Post-Deploy Verification

### Health Checks
```bash
# sre-agent
curl http://<host>:8096/api/health
curl -u admin:password http://<host>:8096/api/findings

# alert-analyzer
curl http://<host>:8097/api/health

# Metrics
curl http://<host>:8096/metrics
curl http://<host>:8097/metrics
```

### Test Alert Webhook
```bash
curl -X POST http://<host>:8097/webhook \
  -u admin:password \
  -H "Content-Type: application/json" \
  -d '{
    "receiver": "test",
    "status": "firing",
    "alerts": [{
      "labels": {
        "alertname": "TestAlert",
        "severity": "critical",
        "service": "test-service"
      },
      "annotations": {
        "summary": "Test alert"
      },
      "startsAt": "2025-01-21T10:00:00Z",
      "fingerprint": "test123",
      "status": "firing"
    }]
  }'
```

### Check Logs
```bash
docker compose logs -f sre-agent
docker compose logs -f alert-analyzer
docker compose logs -f postgres
docker compose logs -f kafka
```

---

## Monitoring Setup

### Prometheus Scrape Config

```yaml
scrape_configs:
  - job_name: 'auto-sre'
    static_configs:
      - targets: ['sre-host:8096', 'sre-host:8097']
```

### Grafana Dashboards

Import from `docs/dashboards/` (to be created):
- `auto-sre-overview.json` — Service health, scan rate, findings
- `auto-sre-kafka.json` — Consumer lag, outbox queue
- `auto-sre-llm.json` — LLM latency, tokens, errors
- `auto-sre-alerts.json` — Alert throughput, analysis latency

### Alert Rules

Already defined in `files/sre-agent/alerting/auto-sre-rules.yaml`. Apply to Prometheus:

```yaml
rule_files:
  - "auto-sre-rules.yaml"
```

---

## Backup & Restore

### PostgreSQL Backup
```bash
# Backup
docker exec auto-sre-postgres pg_dump -U auto_sre auto_sre > backup_$(date +%F).sql

# Restore
cat backup_2025-01-21.sql | docker exec -i auto-sre-postgres psql -U auto_sre auto_sre
```

### Kafka Backup
```bash
# Topics are replicated; backup not typically needed
# For disaster recovery: mirror to another cluster
```

### Volume Backup
```bash
# PostgreSQL data
tar -czf pg_backup_$(date +%F).tar.gz /opt/data/auto-sre/postgres

# Kafka data
tar -czf kafka_backup_$(date +%F).tar.gz /opt/data/auto-sre/kafka
```

---

## Troubleshooting

### Common Issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| `auto_sre_up = 0` | Container crashed | `docker compose logs sre-agent` |
| Scan not running | APScheduler not started | Check logs for scheduler errors |
| High Kafka lag | Consumer slow | Scale consumer, check LLM latency |
| VL circuit breaker open | VL unreachable | Check VL URL, network, credentials |
| LLM circuit breaker open | LLM timeout | Increase `LLM_TIMEOUT`, check LiteLLM |
| DB pool exhausted | Too many connections | Increase `pool_size`, check leaks |

### Debug Commands
```bash
# Enter container
docker exec -it auto-sre-agent bash

# Check DB
docker exec -it auto-sre-postgres psql -U auto_sre -d auto_sre

# Check Kafka
docker exec -it auto-sre-kafka kafka-topics --bootstrap-server localhost:9092 --list
docker exec -it auto-sre-kafka kafka-consumer-groups --bootstrap-server localhost:9092 --describe --group auto-sre-worker

# Check metrics
curl -s http://localhost:8096/metrics | grep auto_sre
```

---

## Upgrading

### Rolling Update
```bash
# Pull latest images
docker compose pull

# Rebuild and restart
docker compose up -d --build

# Verify
curl http://localhost:8096/api/health
```

### Database Migrations
- Schema changes via `Base.metadata.create_all()` on startup
- For production: use Alembic migrations (see `files/sre-agent/migrations/`)

### Rollback
```bash
# Previous image tag
docker compose down
docker compose up -d --build  # with previous image tag
```

---

## Security Hardening

### Production Checklist
- [ ] Change all default passwords
- [ ] Enable `AUTH_ENABLED=true`
- [ ] Configure TLS for PostgreSQL (`sslmode=require`)
- [ ] Configure TLS for Kafka (`SSL` listener)
- [ ] Configure TLS for Victoria Logs (HTTPS)
- [ ] Restrict network access (firewall/security groups)
- [ ] Enable audit logging
- [ ] Rotate secrets regularly (Ansible Vault)
- [ ] Set up log aggregation (Loki/ELK)
- [ ] Configure alerting on-call rotation

### Secrets Management
```bash
# Rotate passwords
ansible-vault edit group_vars/all/vault.yml

# Re-deploy
ansible-playbook -i inventory/all-01-prod auto-sre.yaml --ask-vault-pass
```