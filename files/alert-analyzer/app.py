"""Alert Analyzer - FastAPI service for Alertmanager webhook processing and LLM analysis."""

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response, Depends
from fastapi.responses import PlainTextResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

from common.llm_client import LlmClient
from metrics import (
    http_request_duration_seconds,
    http_requests_total,
    http_auth_failures_total,
    up,
    info,
    alert_batch_size,
    alert_batch_duration_seconds,
    alert_analysis_duration_seconds,
    alert_analysis_total,
    alert_deduped_total,
    alert_webhook_received_total,
    alert_webhook_errors_total,
)
from analyzer import AlertAnalyzer
from models import AlertmanagerPayload
from store import AlertStore, init_db, close_db

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

logger = logging.getLogger("alert.app")

AUTH_ENABLED = os.environ.get("AUTH_ENABLED", "true").lower() == "true"
AUTH_USERNAME = os.environ.get("AUTH_USERNAME", "admin")
AUTH_PASSWORD = os.environ.get("AUTH_PASSWORD", "")

ALERT_BATCH_WINDOW_SEC = int(os.environ.get("ALERT_BATCH_WINDOW_SEC", "300"))
ALERT_BATCH_MAX = int(os.environ.get("ALERT_BATCH_MAX", "20"))
FLUSH_INTERVAL = int(os.environ.get("FLUSH_INTERVAL", "60"))

store = AlertStore()
llm = LlmClient()
analyzer = AlertAnalyzer(store, llm)
security = HTTPBasic(auto_error=False)


def verify_auth(credentials: HTTPBasicCredentials = Depends(security)):
    if not AUTH_ENABLED:
        return True
    if not credentials:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated",
            headers={"WWW-Authenticate": 'Basic realm="Alert Analyzer"'},
        )
    if credentials.username != AUTH_USERNAME or credentials.password != AUTH_PASSWORD:
        http_auth_failures_total.inc()
        raise HTTPException(
            status_code=401,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": 'Basic realm="Alert Analyzer"'},
        )
    return True


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await init_db()
    up.set(1)
    info.info({"version": "1.0.0", "build_date": "2024-01-01"})

    flush_task = asyncio.create_task(analyzer.periodic_flush(FLUSH_INTERVAL))
    logger.info("Alert Analyzer started, flush interval: %ds", FLUSH_INTERVAL)

    try:
        yield
    finally:
        flush_task.cancel()
        try:
            await flush_task
        except asyncio.CancelledError:
            pass
        await analyzer.flush_pending()
        await close_db()
        logger.info("Alert Analyzer stopped")


app = FastAPI(title="Alert Analyzer", lifespan=lifespan)


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    if request.url.path == "/metrics":
        return await call_next(request)

    start_time = time.time()
    try:
        response = await call_next(request)
        status = response.status_code
    except Exception:
        status = 500
        raise
    finally:
        duration = time.time() - start_time
        path = request.url.path
        if path.startswith("/api/") and path.count("/") == 3:
            path = "/api/{endpoint}"
        http_request_duration_seconds.labels(method=request.method, path=path, status=status).observe(duration)
        http_requests_total.labels(method=request.method, path=path, status=status).inc()
    return response


@app.post("/webhook", dependencies=[Depends(verify_auth)])
async def webhook(payload: AlertmanagerPayload):
    """Alertmanager webhook endpoint."""
    logger.info("Received webhook: %d alerts, status=%s", len(payload.alerts), payload.status)
    result = await analyzer.process_webhook(payload)
    return {"status": "ok", **result}


@app.post("/webhook/test", dependencies=[Depends(verify_auth)])
async def webhook_test(payload: AlertmanagerPayload):
    """Test webhook endpoint - processes but doesn't store."""
    logger.info("Test webhook: %d alerts", len(payload.alerts))
    return {"status": "ok", "message": "Test received", "alert_count": len(payload.alerts)}


@app.get("/api/analyses")
async def list_analyses(
    limit: int = 50,
    alertname: str | None = None,
    severity: str | None = None,
    status: str | None = None,
    _: bool = Depends(verify_auth),
):
    analyses = await store.list_analyses(limit=limit, alertname=alertname, severity=severity, status=status)
    return analyses


@app.get("/api/analyses/{analysis_id}")
async def get_analysis(analysis_id: int, _: bool = Depends(verify_auth)):
    analysis = await store.get_analysis(analysis_id)
    if not analysis:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return analysis


@app.get("/api/stats")
async def get_stats(_: bool = Depends(verify_auth)):
    unresolved_critical = await store.count_unresolved_critical()
    buffer_stats = await analyzer.batcher.get_buffer_stats()
    return {
        "unresolved_critical": unresolved_critical,
        "buffer": buffer_stats,
        "config": {
            "batch_window_sec": ALERT_BATCH_WINDOW_SEC,
            "batch_max": ALERT_BATCH_MAX,
            "flush_interval": FLUSH_INTERVAL,
        },
    }


@app.post("/api/flush", dependencies=[Depends(verify_auth)])
async def manual_flush():
    count = await analyzer.flush_pending()
    return {"flushed": count}


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "time": datetime.now(timezone.utc).isoformat(),
        "llm_model": llm.model,
        "buffer_stats": await analyzer.batcher.get_buffer_stats(),
    }


@app.get("/metrics", response_class=PlainTextResponse, include_in_schema=False)
async def metrics():
    return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)


from datetime import datetime, timezone