# Руководство по развёртыванию — Auto SRE

## Требования

### Инфраструктура
- **Docker** 24+ и **Docker Compose** 2.20+
- **Ansible** 2.14+ (для деплоя на прод)
- **PostgreSQL** 16 (в контейнере)
- **Kafka** 7.6+ в режиме KRaft (в контейнере)
- **Victoria Logs** — внешний, доступен по HTTP
- **LiteLLM** — внешний, OpenAI-совместимый эндпоинт

### Сеть
- Порты: `8096` (sre-agent), `8097` (alert-analyzer), `5432` (PostgreSQL), `9092` (Kafka)
- Внутренняя Docker-сеть: `auto-sre-net`

---

## Прод-развёртывание (Ansible)

### Настройка inventory

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

### Секреты в Vault

```bash
ansible-vault create group_vars/all/vault.yml
```

```yaml
vault_victorialogs_password: "super-secret-vl-password"
vault_litellm_api_key: "sk-litellm-xxx"
vault_postgres_password: "super-secret-postgres-password"
vault_auth_password: "super-secret-auth-password"
```

### Деплой

```bash
# Полный деплой
ansible-playbook -i inventory/all-01-prod auto-sre.yaml --ask-vault-pass

# Пробный прогон без изменений
ansible-playbook -i inventory/all-01-prod auto-sre.yaml --check --ask-vault-pass
```

### Что делает Ansible

1. Создаёт `/opt/data/auto-sre/` и `/opt/docker/auto-sre/`
2. Копирует исходники в `/opt/docker/auto-sre/`
3. Рендерит `.env` из `templates/env.j2`
4. Рендерит `docker-compose.yml` из `templates/docker-compose.yml.j2`
4. Выполняет `docker compose down --remove-orphans`
5. Выполняет `docker compose up -d --build`

---

## Локальная разработка

### Быстрый старт

```bash
# Перейти в каталог проекта
cd auto-sre

# Подготовить .env (compose подхватит его из текущего каталога)
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

# Поднять dev-стек (с тестовыми контейнерами)
docker compose -f docker-compose.dev.yml up -d --build

# Смотреть логи
docker compose -f docker-compose.dev.yml logs -f sre-agent
```

### Возможности dev-compose

`docker-compose.dev.yml` включает:
- **Тестовые контейнеры** PostgreSQL/Kafka (эфемерные)
- **Hot reload** через volume-маунты
- **Отладочные порты**
- **Лимиты ресурсов** под локальную машину

---

## Справочник конфигурации

Вся конфигурация через переменные окружения (в `.env` или Ansible inventory):

### Обязательные
| Переменная | Описание |
|----------|-------------|
| `VL_URL` | Эндпоинт Victoria Logs |
| `VL_PASSWORD` | Пароль к VL |
| `LITELLM_URL` | Эндпоинт LiteLLM |
| `LITELLM_API_KEY` | API-ключ LiteLLM |
| `POSTGRES_PASSWORD` | Пароль PostgreSQL |
| `AUTH_PASSWORD` | Пароль Basic Auth |

### Опциональные (со значениями по умолчанию)
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

# Планирование
SCAN_INTERVAL_MINUTES=15
BLOG_HOUR=7
BLOG_MINUTE=30
TZ=Europe/Moscow

# Детекция
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

# Аутентификация
AUTH_ENABLED=true
AUTH_USERNAME=admin
AUTH_PASSWORD=changeme

# Прочее
LOG_LEVEL=INFO
SHUTDOWN_TIMEOUT=30
```

---

## Проверка после деплоя

### Проверки здоровья
```bash
# sre-agent
curl http://<хост>:8096/api/health
curl -u admin:password http://<хост>:8096/api/findings

# alert-analyzer
curl http://<хост>:8097/api/health

# Метрики
curl http://<хост>:8096/metrics
curl http://<хост>:8097/metrics
```

### Тест вебхука алерта
```bash
curl -X POST http://<хост>:8097/webhook \
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

### Просмотр логов
```bash
docker compose logs -f sre-agent
docker compose logs -f alert-analyzer
docker compose logs -f postgres
docker compose logs -f kafka
```

---

## Настройка мониторинга

### Конфигурация scrape для Prometheus

```yaml
scrape_configs:
  - job_name: 'auto-sre'
    static_configs:
      - targets: ['sre-host:8096', 'sre-host:8097']
```

### Дашборды Grafana

Импортировать из `docs/dashboards/` (пока не созданы):
- `auto-sre-overview.json` — здоровье сервисов, частота сканов, находки
- `auto-sre-kafka.json` — лаг консюмера, очередь outbox
- `auto-sre-llm.json` — задержка LLM, токены, ошибки
- `auto-sre-alerts.json` — поток алертов, задержка анализа

### Правила алертинга

Уже определены в `files/sre-agent/alerting/auto-sre-rules.yaml`. Подключить к Prometheus:

```yaml
rule_files:
  - "auto-sre-rules.yaml"
```

---

## Резервное копирование и восстановление

### Бэкап PostgreSQL
```bash
# Бэкап
docker exec auto-sre-postgres pg_dump -U auto_sre auto_sre > backup_$(date +%F).sql

# Восстановление
cat backup_2025-01-21.sql | docker exec -i auto-sre-postgres psql -U auto_sre auto_sre
```

### Бэкап Kafka
```bash
# Топики реплицированы; бэкап обычно не требуется
# Для disaster recovery: зеркало на другой кластер
```

### Бэкап томов
```bash
# Данные PostgreSQL
tar -czf pg_backup_$(date +%F).tar.gz /opt/data/auto-sre/postgres

# Данные Kafka
tar -czf kafka_backup_$(date +%F).tar.gz /opt/data/auto-sre/kafka
```

---

## Устранение неполадок

### Частые проблемы

| Симптом | Причина | Решение |
|---------|-------|-----|
| `auto_sre_up = 0` | Контейнер упал | `docker compose logs sre-agent` |
| Скан не запускается | APScheduler не стартовал | Проверить логи на ошибки планировщика |
| Высокий лаг Kafka | Медленный консюмер | Масштабировать консюмера, проверить задержку LLM |
| VL circuit breaker открыт | VL недоступен | Проверить URL VL, сеть, креды |
| LLM circuit breaker открыт | Таймаут LLM | Увеличить `LLM_TIMEOUT`, проверить LiteLLM |
| Пул БД исчерпан | Слишком много соединений | Увеличить `pool_size`, проверить утечки |

### Отладочные команды
```bash
# Зайти в контейнер
docker exec -it auto-sre-agent bash

# Проверить БД
docker exec -it auto-sre-postgres psql -U auto_sre -d auto_sre

# Проверить Kafka
docker exec -it auto-sre-kafka kafka-topics --bootstrap-server localhost:9092 --list
docker exec -it auto-sre-kafka kafka-consumer-groups --bootstrap-server localhost:9092 --describe --group auto-sre-worker

# Проверить метрики
curl -s http://localhost:8096/metrics | grep auto_sre
```

---

## Обновление

### Rolling update
```bash
# Забрать свежие образы
docker compose pull

# Пересобрать и перезапустить
docker compose up -d --build

# Проверить
curl http://localhost:8096/api/health
```

### Миграции базы данных
- Изменения схемы через `Base.metadata.create_all()` при старте
- Для прода: миграции Alembic (см. `files/sre-agent/migrations/`)

### Откат
```bash
# Предыдущий тег образа
docker compose down
docker compose up -d --build  # с предыдущим тегом образа
```

---

## Укрепление безопасности

### Чек-лист для прода
- [ ] Сменить все пароли по умолчанию
- [ ] Включить `AUTH_ENABLED=true`
- [ ] Настроить TLS для PostgreSQL (`sslmode=require`)
- [ ] Настроить TLS для Kafka (SSL-листенер)
- [ ] Настроить TLS для Victoria Logs (HTTPS)
- [ ] Ограничить сетевой доступ (фаервол/security groups)
- [ ] Включить аудит-логирование
- [ ] Регулярно ротировать секреты (Ansible Vault)
- [ ] Настроить агрегацию логов (Loki/ELK)
- [ ] Настроить алертинг с графиком дежурств

### Управление секретами
```bash
# Ротация паролей
ansible-vault edit group_vars/all/vault.yml

# Повторный деплой
ansible-playbook -i inventory/all-01-prod auto-sre.yaml --ask-vault-pass
```
