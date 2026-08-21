"""Интеграционные тесты alert-analyzer: вебхук, дедуп, флаш, числовой confidence."""
import time
import uuid

import httpx


def _payload(fingerprint: str, alertname: str) -> dict:
    return {
        "receiver": "tests",
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": alertname,
                    "severity": "warning",
                    "service": "test-svc",
                    "namespace": "ns",
                    "cluster": "c1",
                },
                "annotations": {"description": "Интеграционный тест Auto SRE"},
                "startsAt": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - 60)),
                "fingerprint": fingerprint,
            }
        ],
    }


def test_webhook_validation_requires_envelope(analyzer_url):
    r = httpx.post(f"{analyzer_url}/webhook", json={"alerts": []}, timeout=10)
    assert r.status_code == 422  # без receiver/status конверт невалиден


def test_webhook_accepts_and_dedups(analyzer_url):
    """Дедуп сверяется с БД анализов: повтор детектится только после флаша."""
    fp = uuid.uuid4().hex[:12]
    name = f"IntTest{fp[:6]}"

    first = httpx.post(f"{analyzer_url}/webhook", json=_payload(fp, name), timeout=10)
    assert first.status_code == 200
    body = first.json()
    assert body["received"] == 1
    assert body["deduped"] == 0

    # до флаша алерт живёт только в буфере — дедуп его ещё не видит
    flushed = httpx.post(f"{analyzer_url}/api/flush", timeout=30).json()["flushed"]
    assert flushed >= 1
    _wait_for_analysis(analyzer_url, name)

    # теперь анализ в БД, повтор того же fingerprint отбрасывается
    second = httpx.post(f"{analyzer_url}/webhook", json=_payload(fp, name), timeout=10)
    assert second.status_code == 200
    assert second.json()["deduped"] == 1


def _wait_for_analysis(analyzer_url: str, alertname: str, timeout_sec: float = 120) -> dict:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        rows = httpx.get(
            f"{analyzer_url}/api/analyses",
            params={"alertname": alertname, "limit": 1},
            timeout=10,
        ).json()
        if rows:
            return rows[0]
        time.sleep(5)
    raise AssertionError(f"анализ для {alertname} не появился за {timeout_sec:.0f}с")


def test_flush_produces_analysis_with_numeric_confidence(analyzer_url):
    """Вечный регресс унификации confidence: в БД и API должно быть число."""
    fp = uuid.uuid4().hex[:12]
    name = f"FlushTest{fp[:6]}"
    r = httpx.post(f"{analyzer_url}/webhook", json=_payload(fp, name), timeout=10)
    assert r.status_code == 200

    flushed = httpx.post(f"{analyzer_url}/api/flush", timeout=30).json()["flushed"]
    assert flushed >= 1

    analysis = _wait_for_analysis(analyzer_url, name)
    assert analysis["confidence"] is None or isinstance(analysis["confidence"], (int, float)), (
        f"confidence должен быть числом, пришло: {analysis['confidence']!r}"
    )
    assert isinstance(analysis["correlated_group"], list)


def test_stats_shape(analyzer_url):
    r = httpx.get(f"{analyzer_url}/api/stats", timeout=10)
    assert r.status_code == 200
    body = r.json()
    assert set(body) >= {"unresolved_critical", "buffer", "config"}
    assert set(body["buffer"]) >= {"groups", "total_alerts"}
