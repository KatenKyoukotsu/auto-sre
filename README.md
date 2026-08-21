# Auto SRE Ansible Role

Роль разворачивает сервис **Auto SRE** — агента, который автоматически анализирует логи продуктовой системы (Victoria Logs), находит аномалии с помощью LLM (LiteLLM → локальная Gemma), публикует находки на «стену» в веб-интерфейсе и ведёт ежедневный мини-блог.

## Обзор

### Компоненты
- **mcp-vl** — MCP-сервер Victoria Logs (streamable HTTP, порт 8095). Проксирует инструменты `search_logs`, `count_logs`, `get_streams`, `get_fields` в API Victoria Logs (`/select/logsql/query`).
- **sre-agent** — FastAPI-приложение (порт 8096):
  - периодический скан прод-логов (каждые `SCAN_INTERVAL_MINUTES` мин.);
  - поиск всплесков ошибок по каждому стриму относительно базовой линии (скользящее окно);
  - вызов LLM для каждого подозрительного стрима: серьёзность, возможная причина, рекомендуемое действие, уверенность;
  - REST API и веб-страницы: `/` (стена аномалий), `/blog` (дайджест);
  - ежедневный блог-пост со сводкой (по умолчанию 07:30 Europe/Moscow).

## Структура

```
roles/auto-sre/
├── files/
│   ├── mcp-vl/
│   │   ├── server.py            # MCP-сервер Victoria Logs
│   │   ├── requirements.txt     # fastmcp, mcp>=1.2,<2.0
│   │   └── Dockerfile
│   └── sre-agent/
│       ├── agent.py             # логика сканирования и детекции аномалий
│       ├── app.py               # FastAPI + планировщик APScheduler
│       ├── vl.py                # клиент VL (MCP или прямой HTTP)
│       ├── llm.py               # клиент LiteLLM (OpenAI-совместимый)
│       ├── store.py             # SQLite (находки, блог)
│       ├── requirements.txt
│       ├── Dockerfile
│       ├── templates/
│       │   ├── wall.html
│       │   └── blog.html
│       └── static/
│           └── style.css
├── tasks/
│   └── main.yaml                # копирование исходников, рендер, docker compose up
├── templates/
│   ├── env.j2                   # переменные окружения
│   └── docker-compose.yml.j2    # compose для mcp-vl + sre-agent
└── README.md
```

## Требования

- Хост: группа `log` (плейбук `auto-sre.yaml`, hosts: `log`).
- Докер на хосте, доступ к репозиторию Victoria Logs (`VL_URL`) и LiteLLM (`LITELLM_URL`).
- Пароль Victoria Logs задаётся через `victorialogs_password` в инвентаре (инвентарь `all-01-prod`).

## Переменные роли

| Переменная | По умолчанию | Описание |
| :--- | :--- | :--- |
| `auto_sre_vl_url` / `victorialogs_url` | `http://10.148.14.12:9428` | URL Victoria Logs |
| `auto_sre_vl_username` / `victorialogs_username` | `admin` | Пользователь VL |
| `auto_sre_vl_password` / `victorialogs_password` | `''` | Пароль VL |
| `auto_sre_vl_mode` | `mcp` | Режим доступа агента к VL: `mcp` или `http` |
| `auto_sre_litellm_url` | `http://10.148.14.10:4000` | URL LiteLLM |
| `auto_sre_litellm_api_key` | `sk-litellm-master-key` | API-ключ LiteLLM |
| `auto_sre_llm_model` | `gemma-4-12B-it-qat-q4_0-gguf` | Модель LLM |
| `auto_sre_scan_interval_minutes` | `15` | Интервал сканирования, мин |
| `auto_sre_blog_hour` | `7` | Час ежедневного блога |
| `auto_sre_blog_minute` | `30` | Минута ежедневного блога |
| `auto_sre_tz` | `Europe/Moscow` | Таймзона планировщика |

## Порты

| Сервис | Порт | Назначение |
| :--- | :---: | :--- |
| mcp-vl | 8095 | MCP streamable HTTP (эндпоинт `/mcp`) |
| sre-agent | 8096 | Web UI и REST API |

## REST API sre-agent

| Метод | Путь | Описание |
| :--- | :--- | :--- |
| GET | `/api/findings` | Список находок |
| GET | `/api/findings/{id}` | Находка по id |
| POST | `/api/findings/{id}/ack` | Подтвердить (ack) находку |
| GET | `/api/blog` | Список блог-постов |
| POST | `/api/trigger/scan` | Ручной запуск скана |
| POST | `/api/trigger/blog` | Ручная генерация блога |
| GET | `/api/health` | Статус сервиса |

Веб-страницы: `/` — стена аномалий, `/blog` — блог.

## Запуск

```bash
ansible-playbook -i inventory/all-01-prod auto-sre.yaml
```

Данные сохраняются в `/opt/data/auto-sre/sre.db` (SQLite), исходники — в `/opt/docker/auto-sre/`.

Ручной триггер скана после установки:

```bash
curl -X POST http://<host>:8096/api/trigger/scan
```

