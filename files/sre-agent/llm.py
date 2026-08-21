"""Клиент LLM через LiteLLM (OpenAI-совместимый API) с retry и circuit breaker."""

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from openai import AsyncOpenAI

from metrics import (
    llm_request_duration_seconds,
    llm_request_total,
    llm_tokens_used,
    llm_circuit_breaker_state,
    llm_retries_total,
    llm_confidence,
)

logger = logging.getLogger("sre.llm")

LITELLM_URL = os.environ.get("LITELLM_URL", "http://10.148.14.10:4000").rstrip("/")
LITELLM_API_KEY = os.environ.get("LITELLM_API_KEY", "sk-litellm-master-key")
LITELLM_MODEL = os.environ.get("LITELLM_MODEL", "gemma-4-12B-it-qat-q4_0-gguf")
LLM_TEMPERATURE = float(os.environ.get("LLM_TEMPERATURE", "0.2"))
LLM_MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS", "1200"))
LLM_TIMEOUT = int(os.environ.get("LLM_TIMEOUT", "180"))
LLM_MAX_RETRIES = int(os.environ.get("LLM_MAX_RETRIES", "3"))
LLM_RETRY_BASE_DELAY = float(os.environ.get("LLM_RETRY_BASE_DELAY", "2.0"))
LLM_CIRCUIT_BREAKER_THRESHOLD = int(os.environ.get("LLM_CIRCUIT_BREAKER_THRESHOLD", "5"))
LLM_CIRCUIT_BREAKER_TIMEOUT = float(os.environ.get("LLM_CIRCUIT_BREAKER_TIMEOUT", "60"))

FINDING_SCHEMA_HINT = (
    'Ответь строго одним JSON-объектом без markdown-разметки и комментариев. '
    'Ключи: "severity" ("critical"|"high"|"medium"|"low"), "service" (строка), '
    '"title" (короткий заголовок), "summary" (что произошло, 2-4 предложения), '
    '"possible_cause" (вероятная причина), "recommended_action" (что делать), '
    '"confidence" (число 0..1).'
)

ANALYZE_SYSTEM_PROMPT = (
    "Ты — Auto SRE. Анализируешь логи прод-системы и находишь аномалии. "
    "Работаешь только с теми фактами, что есть в логах, не выдумывай. "
    "Пиши на русском языке, кратко и по делу.\n" + FINDING_SCHEMA_HINT
)

BLOG_SYSTEM_PROMPT = (
    "Ты — Auto SRE, ведёшь мини-блог инженера эксплуатации. "
    "На основе перечня инцидентов и аномалий за сутки составь связный пост-дайджест "
    "на русском языке: заголовок, краткий обзор, секции по каждому значимому инциденту "
    "(сервис, суть, статус/действия) и вывод. Не выдумывай фактов, которых нет в данных.\n"
    'Ответь строго JSON-объектом с ключами "title" (строка) и "content" (markdown-текст поста).'
)


def _trunc(text, limit=300):
    text = str(text)
    if len(text) <= limit:
        return text
    return text[:limit] + f"... ({len(text)} chars total)"


class LlmError(RuntimeError):
    pass


@dataclass
class CircuitBreakerState:
    failures: int = 0
    last_failure_time: float = 0
    open: bool = False
    last_success_time: float = 0
    last_error: str | None = None

    def record_success(self) -> None:
        self.failures = 0
        self.open = False
        self.last_success_time = time.time()
        self.last_error = None
        llm_circuit_breaker_state.set(0)

    def record_failure(self, error: str | None = None) -> None:
        self.failures += 1
        self.last_failure_time = time.time()
        if error:
            self.last_error = error
        if self.failures >= LLM_CIRCUIT_BREAKER_THRESHOLD:
            self.open = True
            llm_circuit_breaker_state.set(2)
            logger.warning("Circuit breaker OPEN for LLM")

    def can_proceed(self) -> bool:
        if not self.open:
            llm_circuit_breaker_state.set(0)
            return True
        if time.time() - self.last_failure_time > LLM_CIRCUIT_BREAKER_TIMEOUT:
            logger.info("Circuit breaker HALF-OPEN for LLM")
            llm_circuit_breaker_state.set(1)
            self.open = False
            return True
        llm_circuit_breaker_state.set(2)
        return False

    def state(self) -> str:
        """ok — успехи; failing — были сбои, но breaker ещё открыт не стал; open — недоступна."""
        if self.open:
            return "open"
        if self.failures > 0:
            return "failing"
        return "ok"


class LlmClient:
    PROBE_INTERVAL_SEC = 60

    def __init__(self, url: str = LITELLM_URL, api_key: str = LITELLM_API_KEY, model: str = LITELLM_MODEL):
        self.model = model
        self.base_url = url.rstrip("/")
        self.client = AsyncOpenAI(base_url=f"{url}/v1", api_key=api_key, timeout=LLM_TIMEOUT)
        self._circuit = CircuitBreakerState()
        self._probe_at = 0.0
        self._reachable: bool | None = None

    async def _probe(self) -> None:
        """Дешёвая проверка живости LiteLLM, кэшируется на PROBE_INTERVAL_SEC."""
        if time.time() - self._probe_at < self.PROBE_INTERVAL_SEC:
            return
        self._probe_at = time.time()
        try:
            resp = await self.client.get(f"{self.base_url}/v1/models")
            self._reachable = resp.status_code < 500
        except Exception as exc:
            logger.debug("LLM probe failed: %s", exc)
            self._reachable = False

    async def _complete_with_retry(self, system: str, user: str) -> str:
        if not self._circuit.can_proceed():
            raise LlmError("Circuit breaker OPEN - LLM unavailable")

        last_error: Exception | None = None
        for attempt in range(LLM_MAX_RETRIES):
            start_time = time.time()
            try:
                logger.debug("LLM request: model=%s attempt=%d", self.model, attempt + 1)
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    temperature=LLM_TEMPERATURE,
                    max_tokens=LLM_MAX_TOKENS,
                )
                content = response.choices[0].message.content or ""
                self._circuit.record_success()
                llm_request_duration_seconds.labels(operation="chat_completion", result="success").observe(time.time() - start_time)
                llm_request_total.labels(operation="chat_completion", result="success").inc()
                if response.usage:
                    llm_tokens_used.labels(operation="chat_completion", type="prompt").inc(response.usage.prompt_tokens)
                    llm_tokens_used.labels(operation="chat_completion", type="completion").inc(response.usage.completion_tokens)
                logger.info("LLM response: %d chars", len(content))
                return content
            except Exception as exc:
                last_error = exc
                llm_request_duration_seconds.labels(operation="chat_completion", result="error").observe(time.time() - start_time)
                llm_request_total.labels(operation="chat_completion", result="error").inc()
                logger.warning("LLM request failed (attempt %d/%d): %s", attempt + 1, LLM_MAX_RETRIES, exc)
                if attempt < LLM_MAX_RETRIES - 1:
                    llm_retries_total.labels(operation="chat_completion").inc()
                    delay = LLM_RETRY_BASE_DELAY * (2 ** attempt)
                    logger.info("Retrying LLM in %.1fs...", delay)
                    await asyncio.sleep(delay)

        self._circuit.record_failure(error=str(last_error) if last_error else None)
        raise LlmError(f"LLM unavailable after {LLM_MAX_RETRIES} attempts: {last_error}") from last_error

    async def status(self) -> dict:
        """Состояние LLM для статус-бара: не просто имя модели, а живость."""
        await self._probe()
        last_ok = (
            datetime.fromtimestamp(self._circuit.last_success_time, timezone.utc).isoformat(timespec="seconds")
            if self._circuit.last_success_time
            else None
        )
        return {
            "model": self.model,
            "state": self._circuit.state(),
            "reachable": self._reachable,
            "last_ok": last_ok,
            "last_error": self._circuit.last_error,
        }

    async def _complete(self, system: str, user: str) -> str:
        logger.info("LLM request: model=%s system=%s", self.model, _trunc(system, 250))
        logger.info("LLM request: user=%s", _trunc(user, 1200))
        return await self._complete_with_retry(system, user)

    async def analyze_logs(self, context: str) -> dict:
        start_time = time.time()
        try:
            text = await self._complete(ANALYZE_SYSTEM_PROMPT, context)
            data = extract_json(text)
            confidence = data.get("confidence")
            if confidence is not None:
                try:
                    llm_confidence.labels(severity=data.get("severity", "unknown")).observe(float(confidence))
                except (ValueError, TypeError):
                    pass
            return data
        finally:
            pass

    async def write_blog_post(self, digest_context: str) -> dict:
        start_time = time.time()
        try:
            text = await self._complete(BLOG_SYSTEM_PROMPT, digest_context)
            data = extract_json(text)
            if not data.get("title") or not data.get("content"):
                data = {"title": "SRE-дайджест", "content": text}
            return data
        finally:
            pass


def extract_json(text: str) -> dict:
    """Извлекает первый JSON-объект из ответа модели, устойчиво к обёртке."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


import asyncio