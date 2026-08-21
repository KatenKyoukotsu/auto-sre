"""Юнит-тесты common/llm_client.py: extract_json и circuit breaker."""
import pytest

from common import llm_client


class TestExtractJsonCommon:
    def test_plain_object(self):
        assert llm_client.extract_json('{"a": 1}') == {"a": 1}

    def test_wrapped_in_prose(self):
        text = 'Анализ:\n```json\n{"root_cause": "падение БД", "confidence": {"score": 0.9}}\n```'
        data = llm_client.extract_json(text)
        assert data["root_cause"] == "падение БД"

    def test_trailing_commas_tolerated(self):
        assert llm_client.extract_json('{"groups": [{"a": 1},],}') == {"groups": [{"a": 1}]}

    def test_garbage_returns_empty_dict(self):
        assert llm_client.extract_json("никакого JSON тут нет") == {}


class TestCommonBreaker:
    def test_lifecycle(self, monkeypatch):
        monkeypatch.setattr(llm_client, "LLM_CIRCUIT_BREAKER_THRESHOLD", 2)
        monkeypatch.setattr(llm_client, "LLM_CIRCUIT_BREAKER_TIMEOUT", 60.0)
        cb = llm_client.CircuitBreakerState()

        assert cb.can_proceed() is True
        cb.record_failure()
        assert cb.can_proceed() is True
        cb.record_failure()
        assert cb.open is True
        assert cb.can_proceed() is False

        cb.last_failure_time -= 61.0  # half-open по таймауту
        assert cb.can_proceed() is True
        assert cb.open is False

        cb.record_success()
        assert cb.failures == 0
