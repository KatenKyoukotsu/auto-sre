"""Async HTTP клиент для Victoria Logs с retry и circuit breaker."""

import asyncio
import base64
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx

from metrics import (
    vl_query_duration_seconds,
    vl_query_total,
    vl_circuit_breaker_state,
    vl_retries_total,
)

logger = logging.getLogger("sre.vl")

VL_URL = os.environ.get("VL_URL", "http://127.0.0.1:9428").rstrip("/")
VL_USERNAME = os.environ.get("VL_USERNAME", "")
VL_PASSWORD = os.environ.get("VL_PASSWORD", "")

VL_TIMEOUT = float(os.environ.get("VL_TIMEOUT", "60"))
VL_MAX_RETRIES = int(os.environ.get("VL_MAX_RETRIES", "3"))
VL_RETRY_BASE_DELAY = float(os.environ.get("VL_RETRY_BASE_DELAY", "1.0"))
VL_CIRCUIT_BREAKER_THRESHOLD = int(os.environ.get("VL_CIRCUIT_BREAKER_THRESHOLD", "5"))
VL_CIRCUIT_BREAKER_TIMEOUT = float(os.environ.get("VL_CIRCUIT_BREAKER_TIMEOUT", "30"))


class VlError(RuntimeError):
    pass


@dataclass
class CircuitBreakerState:
    failures: int = 0
    last_failure_time: float = 0
    open: bool = False

    def record_success(self) -> None:
        self.failures = 0
        self.open = False
        vl_circuit_breaker_state.set(0)

    def record_failure(self) -> None:
        self.failures += 1
        self.last_failure_time = time.time()
        if self.failures >= VL_CIRCUIT_BREAKER_THRESHOLD:
            self.open = True
            vl_circuit_breaker_state.set(2)
            logger.warning("Circuit breaker OPEN for Victoria Logs")

    def can_proceed(self) -> bool:
        if not self.open:
            vl_circuit_breaker_state.set(0)
            return True
        if time.time() - self.last_failure_time > VL_CIRCUIT_BREAKER_TIMEOUT:
            logger.info("Circuit breaker HALF-OPEN for Victoria Logs")
            vl_circuit_breaker_state.set(1)
            self.open = False
            return True
        vl_circuit_breaker_state.set(2)
        return False


class HttpVlClient:
    """Асинхронный HTTP-клиент для Victoria Logs (/select/logsql/*) с retry и circuit breaker."""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._circuit = CircuitBreakerState()

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(VL_TIMEOUT),
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    def _auth_header(self) -> dict[str, str]:
        if VL_USERNAME:
            token = base64.b64encode(f"{VL_USERNAME}:{VL_PASSWORD}".encode()).decode()
            return {"Authorization": f"Basic {token}"}
        return {}

    def _time_filter(self, start: str, end: str) -> str:
        return f"_time:[{start}, {end}]"

    @staticmethod
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

    def _get_operation_name(self, path: str) -> str:
        if "/select/logsql/query" in path:
            return "search_logs"
        if "/select/logsql/fields" in path:
            return "get_fields"
        return "unknown"

    async def _request_with_retry(self, path: str, params: dict) -> str:
        operation = self._get_operation_name(path)
        if not self._circuit.can_proceed():
            vl_query_total.labels(operation=operation, result="error").inc()
            raise VlError("Circuit breaker OPEN - Victoria Logs unavailable")

        client = await self._get_client()
        url = f"{VL_URL}{path}"
        headers = self._auth_header()

        last_error: Exception | None = None
        for attempt in range(VL_MAX_RETRIES):
            start_time = time.time()
            try:
                logger.debug("VL request: GET %s params=%s", url, params)
                resp = await client.get(url, params=params, headers=headers)
                resp.raise_for_status()
                body = resp.text
                logger.debug("VL response: %d bytes", len(body))
                self._circuit.record_success()
                vl_query_duration_seconds.labels(operation=operation, result="success").observe(time.time() - start_time)
                vl_query_total.labels(operation=operation, result="success").inc()
                return body
            except httpx.HTTPStatusError as exc:
                last_error = exc
                body = exc.response.text
                vl_query_duration_seconds.labels(operation=operation, result="error").observe(time.time() - start_time)
                vl_query_total.labels(operation=operation, result="error").inc()
                logger.warning("VL HTTP %s (attempt %d/%d): %s", exc.response.status_code, attempt + 1, VL_MAX_RETRIES, body[:500])
                if exc.response.status_code < 500:
                    raise VlError(f"VictoriaLogs HTTP {exc.response.status_code}: {body[:2000]}") from exc
            except (httpx.RequestError, httpx.TimeoutException) as exc:
                last_error = exc
                vl_query_duration_seconds.labels(operation=operation, result="error").observe(time.time() - start_time)
                vl_query_total.labels(operation=operation, result="error").inc()
                logger.warning("VL request error (attempt %d/%d): %s", attempt + 1, VL_MAX_RETRIES, exc)

            if attempt < VL_MAX_RETRIES - 1:
                vl_retries_total.labels(operation=operation).inc()
                delay = VL_RETRY_BASE_DELAY * (2 ** attempt)
                logger.info("Retrying in %.1fs...", delay)
                await asyncio.sleep(delay)

        self._circuit.record_failure()
        raise VlError(f"Victoria Logs unavailable after {VL_MAX_RETRIES} attempts: {last_error}") from last_error

    async def search_logs(self, query: str, start: str, end: str, limit: int = 100, offset: int = 0) -> list[dict]:
        q = f"{self._time_filter(start, end)} {query}".strip()
        raw = await self._request_with_retry("/select/logsql/query", {"query": q, "limit": limit, "offset": offset})
        rows = self._parse_jsonlines(raw)
        logger.info("search_logs: %d rows", len(rows))
        return rows

    async def count_logs(self, query: str = "*", start: str = "now-15m", end: str = "now") -> int:
        q = f"{self._time_filter(start, end)} {query} | stats count() as total".strip()
        raw = await self._request_with_retry("/select/logsql/query", {"query": q, "limit": 10})
        rows = self._parse_jsonlines(raw)
        total = int(rows[0].get("total", 0)) if rows else 0
        logger.info("count_logs: window=[%s, %s] total=%s", start, end, total)
        return total

    async def count_by_stream(self, query: str, start: str, end: str, limit: int = 200) -> list[dict]:
        q = (
            f"{self._time_filter(start, end)} {query}"
            f" | stats by (_stream) count() as total | sort by (total desc) | limit {limit}"
        ).strip()
        raw = await self._request_with_retry("/select/logsql/query", {"query": q, "limit": limit})
        result = []
        for row in self._parse_jsonlines(raw):
            stream = row.get("_stream") or row.get("stream")
            if not stream:
                continue
            result.append({"stream": stream, "count": int(row.get("total", row.get("count", 0)) or 0)})
        logger.info("count_by_stream: window=[%s, %s] %d streams", start, end, len(result))
        return result

    async def get_streams(self, start: str = "now-24h", end: str = "now", limit: int = 200) -> list[dict]:
        rows = await self.count_by_stream("*", start, end, limit=limit)
        return [{"stream": r["stream"], "samples": r["count"]} for r in rows]

    async def get_fields(self) -> list[str]:
        raw = await self._request_with_retry("/select/logsql/fields", {})
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return [name for name in raw.split() if name]


_vl_client: HttpVlClient | None = None


def build_vl_client() -> HttpVlClient:
    global _vl_client
    if _vl_client is None:
        _vl_client = HttpVlClient()
        logger.info("VL-клиент: прямой HTTP (%s)", VL_URL)
    return _vl_client


import asyncio