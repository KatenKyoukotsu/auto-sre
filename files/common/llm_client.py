"""Shared LLM client for LiteLLM (OpenAI-compatible API) with retry and circuit breaker."""

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Optional

from openai import AsyncOpenAI

from metrics import (
    llm_request_duration_seconds,
    llm_request_total,
    llm_tokens_used,
    llm_circuit_breaker_state,
    llm_retries_total,
    llm_confidence,
)

logger = logging.getLogger("common.llm")

LITELLM_URL = os.environ.get("LITELLM_URL", "http://10.148.14.10:4000").rstrip("/")
LITELLM_API_KEY = os.environ.get("LITELLM_API_KEY", "sk-litellm-master-key")
LITELLM_MODEL = os.environ.get("LITELLM_MODEL", "gemma-4-12B-it-qat-q4_0-gguf")
LLM_TEMPERATURE = float(os.environ.get("LLM_TEMPERATURE", "0.2"))
LLM_MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS", "2000"))
LLM_TIMEOUT = int(os.environ.get("LLM_TIMEOUT", "180"))
LLM_MAX_RETRIES = int(os.environ.get("LLM_MAX_RETRIES", "3"))
LLM_RETRY_BASE_DELAY = float(os.environ.get("LLM_RETRY_BASE_DELAY", "2.0"))
LLM_CIRCUIT_BREAKER_THRESHOLD = int(os.environ.get("LLM_CIRCUIT_BREAKER_THRESHOLD", "5"))
LLM_CIRCUIT_BREAKER_TIMEOUT = float(os.environ.get("LLM_CIRCUIT_BREAKER_TIMEOUT", "60"))


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

    def record_success(self) -> None:
        self.failures = 0
        self.open = False
        llm_circuit_breaker_state.set(0)

    def record_failure(self) -> None:
        self.failures += 1
        self.last_failure_time = time.time()
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


class LlmClient:
    def __init__(
        self,
        url: str = LITELLM_URL,
        api_key: str = LITELLM_API_KEY,
        model: str = LITELLM_MODEL,
        temperature: float = LLM_TEMPERATURE,
        max_tokens: int = LLM_MAX_TOKENS,
        timeout: int = LLM_TIMEOUT,
    ):
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.client = AsyncOpenAI(base_url=f"{url}/v1", api_key=api_key, timeout=timeout)
        self._circuit = CircuitBreakerState()

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
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
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

        self._circuit.record_failure()
        raise LlmError(f"LLM unavailable after {LLM_MAX_RETRIES} attempts: {last_error}") from last_error

    async def complete(self, system: str, user: str) -> str:
        logger.info("LLM request: model=%s system=%s", self.model, _trunc(system, 250))
        logger.info("LLM request: user=%s", _trunc(user, 1200))
        return await self._complete_with_retry(system, user)

    async def complete_json(self, system: str, user: str) -> dict:
        text = await self.complete(system, user)
        return extract_json(text)


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