"""Юнит-тесты AlertBatcher: окно по времени/размеру, группировка, статистика."""
import uuid
from datetime import datetime, timedelta, timezone

from models import Alert, AlertAnnotations, AlertLabels
import analyzer as az


def make_alert(alertname="HighErrorRate", service="billing", severity="critical") -> Alert:
    return Alert(
        labels=AlertLabels(alertname=alertname, severity=severity, service=service),
        annotations=AlertAnnotations(description="тест"),
        startsAt=datetime.now(timezone.utc),
        fingerprint=uuid.uuid4().hex[:12],
        status="firing",
    )


class _ShiftedDatetime(datetime):
    """datetime с управляемым сдвигом now() для проверки временного окна."""
    shift_sec = 0.0

    @classmethod
    def now(cls, tz=None):
        base = datetime.now(tz) if tz else datetime.now()
        return base + timedelta(seconds=cls.shift_sec)


async def test_add_below_limits_keeps_buffering():
    batcher = az.AlertBatcher(window_sec=300, max_size=5)
    result = await batcher.add(make_alert())
    assert result is None
    stats = await batcher.get_buffer_stats()
    assert stats == {"groups": 1, "total_alerts": 1}


async def test_max_size_flushes_whole_group():
    batcher = az.AlertBatcher(window_sec=300, max_size=2)
    first = make_alert()
    second = make_alert()
    assert await batcher.add(first) is None
    batch = await batcher.add(second)
    assert batch is not None and [a.fingerprint for a in batch] == [first.fingerprint, second.fingerprint]
    assert (await batcher.get_buffer_stats())["total_alerts"] == 0


async def test_different_services_form_separate_groups():
    batcher = az.AlertBatcher(window_sec=300, max_size=10)
    await batcher.add(make_alert(service="billing"))
    await batcher.add(make_alert(service="gateway"))
    stats = await batcher.get_buffer_stats()
    assert stats == {"groups": 2, "total_alerts": 2}


async def test_time_window_flushes_stale_group(monkeypatch):
    monkeypatch.setattr(az, "datetime", _ShiftedDatetime)
    batcher = az.AlertBatcher(window_sec=10, max_size=100)
    stale = make_alert()
    assert await batcher.add(stale) is None

    _ShiftedDatetime.shift_sec = 11.0  # окно истекло
    try:
        fresh = make_alert()
        batch = await batcher.add(fresh)
        assert batch is not None
        assert [a.fingerprint for a in batch] == [stale.fingerprint, fresh.fingerprint]
    finally:
        _ShiftedDatetime.shift_sec = 0.0


async def test_flush_all_returns_everything_and_empties():
    batcher = az.AlertBatcher(window_sec=300, max_size=10)
    alerts = [make_alert(service=s) for s in ("billing", "gateway", "auth")]
    for a in alerts:
        await batcher.add(a)
    flushed = await batcher.flush_all()
    assert {a.fingerprint for a in flushed} == {a.fingerprint for a in alerts}
    assert (await batcher.get_buffer_stats())["total_alerts"] == 0
