# Auto SRE — указатель документации

## Обзор

Auto SRE — платформа наблюдаемости на базе LLM с двумя основными сервисами:

| Сервис | Порт | Назначение |
|---------|------|---------|
| **sre-agent** | 8096 | Обнаружение аномалий в логах, веб-интерфейс, генерация блог-постов |
| **alert-analyzer** | 8097 | Приём вебхуков Alertmanager, корреляция алертов |

---

## Документация

| Документ | Описание |
|----------|-------------|
| [Архитектура](architecture.md) | Устройство системы, потоки данных, схема БД, топики Kafka |
| [Справочник API](api-reference.md) | Полная документация REST API обоих сервисов |
| [Развёртывание](deployment.md) | Развёртывание: прод (Ansible) и локальная разработка |
| [Ревью кода](code-review.md) | Детальное ревью кода: 12 замечаний и план действий |

---

## Быстрые ссылки

### Разработчикам
- [Обзор архитектуры](architecture.md#диаграмма-компонентов)
- [Справочник API](api-reference.md)
- [Замечания из ревью кода](code-review.md#-критичные-проблемы)

### Эксплуатация
- [Руководство по развёртыванию](deployment.md)
- [Настройка мониторинга](deployment.md#настройка-мониторинга)
- [Устранение неполадок](deployment.md#устранение-неполадок)

### Безопасность
- [Чек-лист безопасности](deployment.md#укрепление-безопасности)
- [Конфигурация аутентификации](architecture.md#конфигурация)

---

## Статус системы

| Компонент | Статус | Примечания |
|-----------|--------|-------|
| sre-agent | ✅ Готов к продакшену | Скан логов, веб-интерфейс, генерация блог-постов |
| alert-analyzer | ✅ Готов к продакшену | Вебхук Alertmanager, LLM-анализ |
| PostgreSQL | ✅ Схема определена | Миграции в `files/*/migrations/` |
| Kafka | ✅ Настроен | Режим KRaft, 4 топика |
| Метрики | ✅ 53+ метрик | Формат Prometheus на `/metrics` |
| Алертинг | ✅ 25+ правил | В `files/sre-agent/alerting/` |
| Аутентификация | ✅ Basic Auth | Опционально, настраивается |

---

## Быстрый старт

### Локальная разработка (5 минут)
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

### Деплой на прод
```bash
ansible-playbook -i inventory/all-01-prod auto-sre.yaml --ask-vault-pass
```

---

## Ключевые метрики для наблюдения

```promql
# Здоровье сервиса
auto_sre_up

# Здоровье сканов
auto_sre_last_scan_error
auto_sre_last_scan_timestamp

# Здоровье зависимостей
auto_sre_vl_circuit_breaker_state
auto_sre_llm_circuit_breaker_state

# Здоровье Kafka
auto_sre_kafka_consumer_lag
auto_sre_kafka_outbox_pending

# Здоровье БД
auto_sre_db_pool_checked_out / auto_sre_db_pool_size

# Анализатор алертов
auto_sre_alert_webhook_received_total
auto_sre_alert_analysis_duration_seconds
```

---

## Структура кода

```
auto-sre/
├── docs/                          # Документация
├── templates/                     # Шаблоны Ansible
│   ├── env.j2                     # Переменные окружения
│   ├── docker-compose.yml.j2      # Compose для прода
│   └── docker-compose.dev.yml.j2  # Compose для разработки
├── tasks/
│   └── main.yml                   # Задачи Ansible
├── files/
│   ├── common/
│   │   └── llm_client.py          # Общий LLM-клиент
│   ├── sre-agent/                 # Основной сервис
│   │   ├── app.py                 # FastAPI + APScheduler
│   │   ├── agent.py               # Логика скана
│   │   ├── store.py               # PostgreSQL + outbox
│   │   ├── vl.py                  # Клиент Victoria Logs
│   │   ├── llm.py                 # Обёртка LLM (legacy)
│   │   ├── kafka_producer.py      # Идемпотентный продюсер
│   │   ├── kafka_consumer.py      # Фоновый воркер
│   │   ├── metrics.py             # 53+ метрик Prometheus
│   │   ├── models.py              # SQLAlchemy-модели
│   │   └── migrations/            # SQL-миграции
│   └── alert-analyzer/            # Сервис алертов
│       ├── app.py                 # FastAPI-вебхук
│       ├── analyzer.py            # Батчинг + LLM
│       ├── models.py              # Pydantic-модели
│       ├── store.py               # PostgreSQL
│       └── migrations/
└── README.md                      # Руководство по развёртыванию (ru)
```

---

## Как вносить изменения

1. **Стиль кода**: стандартный Python, async/await, аннотации типов
2. **Метрики**: сначала добавляйте в `metrics.py`, потом инструментируйте
3. **База данных**: миграция в `migrations/` + обновление моделей
4. **Тесты**: тестового набора пока нет — проверяйте вручную через API
5. **Документация**: обновляйте соответствующие `.md` в `docs/`

---

## Поддержка

- **Проблемы**: известные проблемы см. в [Ревью кода](code-review.md)
- **Логи**: `docker compose logs -f sre-agent`
- **Метрики**: `curl http://host:8096/metrics`
- **Здоровье**: `curl http://host:8096/api/health`
