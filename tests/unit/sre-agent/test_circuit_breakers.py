"""Юнит-тесты circuit breaker'ов: vl.py и llm.py (sre-agent).

Оба класса имеют одинаковую семантику:
closed -> (N сбоев) -> open -> (таймаут) -> half-open (can_proceed=True) -> ...
"""
import pytest

import llm as sre_llm
import vl as sre_vl


def _exercise_breaker(monkeypatch, module, threshold_attr, timeout_attr, cls, record_failure):
    monkeypatch.setattr(module, threshold_attr, 2)
    monkeypatch.setattr(module, timeout_attr, 60.0)
    cb = cls()

    assert cb.can_proceed() is True, "свежий breaker пропускает"

    record_failure(cb)
    assert cb.can_proceed() is True, "один сбой ещё не открывает"

    record_failure(cb)
    assert cb.open is True
    assert cb.can_proceed() is False, "открытый breaker блокирует"

    # таймаут истёк — half-open: пропуск и сброс open
    cb.last_failure_time -= 61.0
    assert cb.can_proceed() is True
    assert cb.open is False

    # успех закрывает полностью
    cb.record_success()
    assert cb.failures == 0


class TestVlBreaker:
    def test_lifecycle(self, monkeypatch):
        _exercise_breaker(
            monkeypatch,
            sre_vl,
            "VL_CIRCUIT_BREAKER_THRESHOLD",
            "VL_CIRCUIT_BREAKER_TIMEOUT",
            sre_vl.CircuitBreakerState,
            lambda cb: cb.record_failure(),
        )


class TestLlmBreaker:
    def test_lifecycle_with_error_text(self, monkeypatch):
        _exercise_breaker(
            monkeypatch,
            sre_llm,
            "LLM_CIRCUIT_BREAKER_THRESHOLD",
            "LLM_CIRCUIT_BREAKER_TIMEOUT",
            sre_llm.CircuitBreakerState,
            lambda cb: cb.record_failure(error="boom"),
        )

    def test_state_labels(self, monkeypatch):
        monkeypatch.setattr(sre_llm, "LLM_CIRCUIT_BREAKER_THRESHOLD", 2)
        cb = sre_llm.CircuitBreakerState()
        assert cb.state() == "ok"
        cb.record_failure(error="x")
        assert cb.state() == "failing"
        cb.record_failure(error="x")
        assert cb.state() == "open"
