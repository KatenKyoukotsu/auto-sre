"""Интеграционные тесты sre-agent API (только чтение — без триггеров сканов)."""
import httpx


def test_health(sre_agent_url):
    r = httpx.get(f"{sre_agent_url}/api/health", timeout=10)
    assert r.status_code == 200
    body = r.json()
    assert "last_scan" in body
    assert "last_error" in body
    # честная семантика: при ошибке VL last_error заполнен и это не «успех»
    if body["status"] == "error":
        assert body["last_error"], "статус error без last_error ломает диагностику"


def test_metrics_expose_core_families(sre_agent_url):
    r = httpx.get(f"{sre_agent_url}/metrics", timeout=10)
    assert r.status_code == 200
    for family in (
        "auto_sre_scan_total",
        "auto_sre_last_scan_error",
        "auto_sre_findings_total",
        "auto_sre_vl_circuit_breaker_state",
        "auto_sre_llm_confidence",
    ):
        assert family in r.text, f"нет метрики {family}"


def test_findings_list_shape(sre_agent_url):
    r = httpx.get(f"{sre_agent_url}/api/findings", params={"limit": 5}, timeout=10)
    assert r.status_code == 200
    items = r.json()
    assert isinstance(items, list)
    for item in items:
        assert isinstance(item["id"], int)
        # LLM не всегда держит фиксированную шкалу — принимаем любую непустую строку
        assert isinstance(item["severity"], str) and item["severity"]
        # регресс унификации: confidence — число или null, не JSON-объект/строка
        assert item["confidence"] is None or isinstance(item["confidence"], (int, float))


def test_unknown_finding_404(sre_agent_url):
    r = httpx.get(f"{sre_agent_url}/api/findings/99999999", timeout=10)
    assert r.status_code == 404
