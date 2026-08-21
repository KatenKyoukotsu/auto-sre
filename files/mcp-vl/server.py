"""MCP-сервер для Victoria Logs.

Предоставляет LLM-агентам (через протокол MCP, транспорт streamable-http)
инструменты для поиска и анализа логов: search_logs, count_logs,
count_by_stream, get_streams, get_fields.

Переменные окружения:
    VL_URL          - базовый URL Victoria Logs (по умолчанию http://127.0.0.1:9428)
    VL_USERNAME     - пользователь http-аутентификации VL
    VL_PASSWORD     - пароль http-аутентификации VL
    MCP_HOST        - адрес прослушивания MCP (по умолчанию 0.0.0.0)
    MCP_PORT        - порт MCP (по умолчанию 8095)
    VL_DEFAULT_LIMIT- лимит строк по умолчанию (по умолчанию 100)
"""

import base64
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request

from mcp.server.fastmcp import FastMCP

VL_URL = os.environ.get("VL_URL", "http://127.0.0.1:9428").rstrip("/")
VL_USERNAME = os.environ.get("VL_USERNAME", "")
VL_PASSWORD = os.environ.get("VL_PASSWORD", "")
MCP_HOST = os.environ.get("MCP_HOST", "0.0.0.0")
MCP_PORT = int(os.environ.get("MCP_PORT", "8095"))
DEFAULT_LIMIT = int(os.environ.get("VL_DEFAULT_LIMIT", "100"))

logger = logging.getLogger("sre.mcp")

mcp = FastMCP("victorialogs")


def _trunc(text, limit=300):
    text = str(text)
    if len(text) <= limit:
        return text
    return text[:limit] + f"... ({len(text)} chars total)"


def _auth_header() -> dict:
    if VL_USERNAME:
        token = base64.b64encode(f"{VL_USERNAME}:{VL_PASSWORD}".encode("utf-8")).decode("ascii")
        return {"Authorization": f"Basic {token}"}
    return {}


def _http_get(path: str, params: dict) -> str:
    url = f"{VL_URL}{path}?{urllib.parse.urlencode(params)}"
    logger.info("VL request: GET %s", url)
    request = urllib.request.Request(url, headers=_auth_header())
    try:
        with urllib.request.urlopen(request, timeout=60) as resp:
            body = resp.read().decode("utf-8")
        logger.info("VL response: %d bytes body=%s", len(body), _trunc(body, 600))
        return body
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        logger.error("VL HTTP %s: %s", exc.code, url)
        logger.error("VL error body: %s", _trunc(body, 1000))
        raise RuntimeError(f"VictoriaLogs HTTP {exc.code}: {body[:2000]}") from exc


def _parse_jsonlines(raw: str) -> list[dict]:
    rows = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            rows.append({"_msg": line})
    return rows


def _time_filter(start: str, end: str) -> str:
    return f"_time:[{start}, {end}]"


@mcp.tool()
def search_logs(query: str, start: str = "now-15m", end: str = "now", limit: int = DEFAULT_LIMIT, offset: int = 0) -> list[dict]:
    """Поиск логов в Victoria Logs.

    Args:
        query: LogsQL-фильтр, например `error` или `level:error AND db:postgres`.
        start: начало временного окна (`now-1h`, `2025-01-01T00:00:00Z`).
        end: конец временного окна (`now`).
        limit: максимальное число возвращаемых записей.
        offset: смещение для пагинации.

    Returns:
        Список лог-записей (каждая запись - словарь полей, `_msg` - сообщение).
    """
    logger.info("tool search_logs: query=%r window=[%s, %s] limit=%s offset=%s", query, start, end, limit, offset)
    q = f"{_time_filter(start, end)} {query}".strip()
    raw = _http_get("/select/logsql/query", {
        "query": q,
        "limit": limit,
        "offset": offset,
    })
    rows = _parse_jsonlines(raw)
    logger.info("search_logs: %d rows, sample=%s", len(rows), _trunc(rows[:3], 400))
    return rows


@mcp.tool()
def count_logs(query: str = "*", start: str = "now-15m", end: str = "now") -> dict:
    """Подсчёт числа лог-записей, попадающих под фильтр в окне времени.

    Используется для построения базовой линии (baseline) и детекции всплесков.

    Args:
        query: LogsQL-фильтр (`*` - все записи).
        start: начало окна.
        end: конец окна.

    Returns:
        {"count": int, "query": str}.
    """
    logger.info("tool count_logs: query=%r window=[%s, %s]", query, start, end)
    q = f"{_time_filter(start, end)} {query} | stats count() as total".strip()
    raw = _http_get("/select/logsql/query", {"query": q, "limit": 10})
    rows = _parse_jsonlines(raw)
    count = 0
    if rows:
        count = int(rows[0].get("total", 0))
    logger.info("count_logs: total=%s", count)
    return {"count": count, "query": q}


@mcp.tool()
def count_by_stream(query: str = "*", start: str = "now-24h", end: str = "now", limit: int = 200) -> list[dict]:
    """Число лог-записей, подходящих под `query`, сгруппированных по `_stream`.

    В отличие от get_streams, перечисляет ВСЕ потоки за окно (не первые N строк),
    поэтому подходит для полного сканирования и детекции всплесков.

    Args:
        query: LogsQL-фильтр (`*` - все записи).
        start: начало окна.
        end: конец окна.
        limit: максимум возвращаемых потоков.

    Returns:
        Список {"_stream": str, "total": int}, отсортирован по убыванию total.
    """
    logger.info("tool count_by_stream: query=%r window=[%s, %s] limit=%s", query, start, end, limit)
    q = (
        f"{_time_filter(start, end)} {query}"
        f" | stats by (_stream) count() as total | sort by (total desc) | limit {limit}"
    ).strip()
    raw = _http_get("/select/logsql/query", {"query": q, "limit": limit})
    rows = _parse_jsonlines(raw)
    result = [
        {"_stream": row.get("_stream", ""), "total": int(row.get("total", 0) or 0)}
        for row in rows if row.get("_stream")
    ]
    logger.info("count_by_stream: %d streams: %s", len(result), _trunc(result, 500))
    return result


@mcp.tool()
def get_streams(start: str = "now-24h", end: str = "now", limit: int = 200) -> list[dict]:
    """Список потоков (streams) логов за окно времени.

    Перечисляет ВСЕ потоки за окно через группировку `stats by (_stream)`,
    а не выборку первых строк, поэтому видит и малоактивные потоки.

    Args:
        start: начало окна.
        end: конец окна.
        limit: максимум возвращаемых потоков.

    Returns:
        Список уникальных потоков с количеством записей за окно.
    """
    logger.info("tool get_streams: window=[%s, %s] limit=%s", start, end, limit)
    rows = count_by_stream("*", start, end, limit=limit)
    return [{"stream": row["_stream"], "samples": row["total"]} for row in rows]


@mcp.tool()
def get_fields() -> list[str]:
    """Список имён полей, доступных в Victoria Logs."""
    logger.info("tool get_fields")
    raw = _http_get("/select/logsql/fields", {})
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return [name for name in raw.split() if name]


if __name__ == "__main__":
    # mcp 1.x по умолчанию включает защиту от DNS-rebinding: Host-хедер запроса
    # обязан совпадать с localhost, иначе сервер отвечает 421 "Invalid Host header".
    # Сервис живёт во внутренней docker-сети инфраструктуры, поэтому защиту
    # отключаем явно.
    try:
        from mcp.server.transport_security import TransportSecuritySettings

        mcp.settings.transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=False,
            allowed_hosts=["*"],
        )
    except Exception:
        pass
    mcp.settings.host = MCP_HOST
    mcp.settings.port = MCP_PORT

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    import uvicorn
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    async def health(request):
        return JSONResponse({"status": "ok"})

    # MCP-эндпоинт (streamable HTTP) требует специфичных Accept/Content-Type
    # заголовков и отвечает 406 на простые GET, поэтому healthcheck идёт
    # на отдельный /health вместо /mcp.
    app = mcp.streamable_http_app()
    app.router.routes.append(Route("/health", health, methods=["GET"]))

    uvicorn.run(app, host=MCP_HOST, port=MCP_PORT)

