"""Web-интерфейс Auto SRE.

FastAPI-приложение: REST API находок/блога + страница-стена «что не так в проде»
и страница мини-блога Auto SRE. Фоновый планировщик запускает скан аномалий
по расписанию в рамках lifespan приложения.
"""

import asyncio
import base64
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

from agent import Agent, full_scan_job, scan_job
from llm import LlmClient
from store import Store, init_db, close_db
from vl import build_vl_client
from kafka_producer import init_kafka, close_kafka
from kafka_consumer import init_kafka_consumer, close_kafka_consumer
from metrics import (
    http_request_duration_seconds,
    http_requests_total,
    http_auth_failures_total,
    up,
    info,
)

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCAN_INTERVAL_MINUTES = int(os.environ.get("SCAN_INTERVAL_MINUTES", "15"))
BLOG_HOUR = int(os.environ.get("BLOG_HOUR", "7"))
BLOG_MINUTE = int(os.environ.get("BLOG_MINUTE", "30"))
TIMEZONE = os.environ.get("TZ", "Europe/Moscow")
SHUTDOWN_TIMEOUT = int(os.environ.get("SHUTDOWN_TIMEOUT", "30"))

AUTH_ENABLED = os.environ.get("AUTH_ENABLED", "true").lower() == "true"
AUTH_USERNAME = os.environ.get("AUTH_USERNAME", "admin")
AUTH_PASSWORD = os.environ.get("AUTH_PASSWORD", "")

AUTH_EXCLUDE_PATHS = {
    "/api/health",
    "/metrics",
    "/static",
    "/favicon.ico",
}

logger = logging.getLogger("sre.app")

store = Store()
agent = Agent(store=store, llm=LlmClient())
scheduler = AsyncIOScheduler(timezone=TIMEZONE)
_running_tasks: set[asyncio.Task] = set()

# Set build info
info.info({"version": "1.0.0", "build_date": "2024-01-01"})
up.set(1)


def _track_task(task: asyncio.Task) -> None:
    _running_tasks.add(task)
    task.add_done_callback(_running_tasks.discard)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await init_db()
    await init_kafka()
    await init_kafka_consumer()
    scheduler.add_job(
        scan_job,
        IntervalTrigger(minutes=SCAN_INTERVAL_MINUTES),
        args=[agent],
        id="scan",
        coalesce=True,
        max_instances=1,
    )
    scheduler.add_job(
        _blog_job,
        CronTrigger(hour=BLOG_HOUR, minute=BLOG_MINUTE),
        args=[agent],
        id="daily_blog",
        coalesce=True,
        max_instances=1,
    )
    scheduler.start()
    try:
        yield
    finally:
        logger.info("Shutting down gracefully, waiting for running tasks...")
        scheduler.shutdown(wait=True)
        if _running_tasks:
            logger.info("Waiting for %d background tasks to complete (timeout=%ds)", len(_running_tasks), SHUTDOWN_TIMEOUT)
            try:
                await asyncio.wait_for(asyncio.gather(*_running_tasks, return_exceptions=True), timeout=SHUTDOWN_TIMEOUT)
            except asyncio.TimeoutError:
                logger.warning("Shutdown timeout reached, %d tasks still running", len(_running_tasks))
        vl_client = build_vl_client()
        await vl_client.close()
        await close_kafka()
        await close_kafka_consumer()
        await close_db()
        logger.info("Shutdown complete")


async def _blog_job(a: Agent) -> None:
    try:
        post_id = await a.generate_daily_blog()
        logging.getLogger("sre.app").info("Блог-пост создан: id=%s", post_id)
    except Exception:
        logging.getLogger("sre.app").exception("Генерация блог-поста не удалась")


class BasicAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, username: str, password: str, exclude_paths: set[str]):
        super().__init__(app)
        self.expected_auth = None
        if username and password:
            credentials = f"{username}:{password}".encode()
            self.expected_auth = f"Basic {base64.b64encode(credentials).decode()}"
        self.exclude_paths = exclude_paths

    async def dispatch(self, request: Request, call_next):
        if not AUTH_ENABLED or not self.expected_auth:
            return await call_next(request)

        path = request.url.path
        if any(path.startswith(excluded) for excluded in self.exclude_paths):
            return await call_next(request)

        auth_header = request.headers.get("Authorization")
        if not auth_header or auth_header != self.expected_auth:
            http_auth_failures_total.inc()
            return Response(
                content="Unauthorized",
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="Auto SRE"'},
            )

        return await call_next(request)


app = FastAPI(title="Auto SRE", lifespan=lifespan)

app.add_middleware(BasicAuthMiddleware, username=AUTH_USERNAME, password=AUTH_PASSWORD, exclude_paths=AUTH_EXCLUDE_PATHS)

@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    """Prometheus metrics middleware."""
    if request.url.path == "/metrics":
        return await call_next(request)

    start_time = time.time()
    try:
        response = await call_next(request)
        status = response.status_code
    except Exception as exc:
        status = 500
        raise
    finally:
        duration = time.time() - start_time
        path = request.url.path
        # Normalize path for metrics (avoid high cardinality)
        if path.startswith("/api/findings/") and path.count("/") == 3:
            path = "/api/findings/{id}"
        http_request_duration_seconds.labels(method=request.method, path=path, status=status).observe(duration)
        http_requests_total.labels(method=request.method, path=path, status=status).inc()
    return response


@app.get("/metrics", response_class=PlainTextResponse, include_in_schema=False)
async def metrics_endpoint():
    """Prometheus metrics endpoint."""
    return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    body = ""
    if request.method in ("POST", "PUT", "PATCH"):
        raw = await request.body()
        body = raw.decode("utf-8", errors="replace")[:1000] if raw else ""
    qs = str(request.url.query)
    started = time.monotonic()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("HTTP %s %s?%s body=%s", request.method, request.url.path, qs, body)
        raise
    duration_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "HTTP %s %s?%s -> %s body=%s (%dms)",
        request.method, request.url.path, qs, response.status_code, body or "-", duration_ms,
    )
    return response



class NoCacheStaticFiles(StaticFiles):
    """Статика с обязательной ревалидацией: правки фронтенда видны без ручного сброса кэша."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


app.mount("/static", NoCacheStaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

FRONTEND_DIR = os.path.join(BASE_DIR, "static")


@app.get("/", include_in_schema=False)
async def wall():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"), headers={"Cache-Control": "no-cache"})


@app.get("/blog", include_in_schema=False)
async def blog_page():
    return FileResponse(os.path.join(FRONTEND_DIR, "blog.html"), headers={"Cache-Control": "no-cache"})


@app.get("/api/findings")
async def api_findings(limit: int = 50):
    return await store.list_findings(limit=limit)


@app.get("/api/findings/{finding_id}")
async def api_finding(finding_id: int):
    finding = await store.get_finding(finding_id)
    if not finding:
        raise HTTPException(status_code=404, detail="Finding not found")
    return finding


@app.post("/api/findings/{finding_id}/ack")
async def api_ack(finding_id: int):
    if not await store.acknowledge_finding(finding_id):
        raise HTTPException(status_code=404, detail="Finding not found")
    return {"ok": True, "id": finding_id}


@app.get("/api/blog")
async def api_blog(limit: int = 30):
    return await store.list_blog_posts(limit=limit)


@app.get("/api/blog/status")
async def api_blog_status():
    return {"status": agent.blog_status, "error": agent.blog_error}


@app.post("/api/trigger/scan")
async def api_trigger_scan():
    _track_task(asyncio.create_task(scan_job(agent)))
    return {"ok": True, "message": "Скан аномалий запущен"}


@app.post("/api/trigger/full-scan")
async def api_trigger_full_scan(payload: dict | None = None):
    body = payload or {}
    start, end = body.get("start"), body.get("end")
    _track_task(asyncio.create_task(full_scan_job(agent, start, end)))
    return {"ok": True, "message": "Полное сканирование запущено"}


@app.post("/api/trigger/blog")
async def api_trigger_blog():
    _track_task(asyncio.create_task(_blog_job(agent)))
    return {"ok": True, "message": "Генерация блог-поста запущена"}


@app.get("/api/health")
async def api_health():
    findings = await store.list_findings(limit=1)
    posts = await store.list_blog_posts(limit=1)
    return {
        "status": "ok",
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "last_scan": agent.last_scan,
        "last_error": agent.last_error,
        "scan_interval_minutes": SCAN_INTERVAL_MINUTES,
        "model": agent.llm.model,
        "vl_mode": type(agent.vl).__name__,
        "latest_finding": findings[0] if findings else None,
        "latest_blog_post": posts[0] if posts else None,
    }

