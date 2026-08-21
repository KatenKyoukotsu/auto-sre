"""Юнит-тесты детекции: _is_spike и _error_query (agent.py)."""
import pytest

import agent


@pytest.fixture
def thresholds(monkeypatch):
    monkeypatch.setattr(agent, "SPIKE_STD_MULTIPLIER", 3.0)
    monkeypatch.setattr(agent, "SPIKE_MEAN_MULTIPLIER", 2.0)
    monkeypatch.setattr(agent, "MIN_ABS_SPIKE", 20)


class TestIsSpike:
    def test_empty_series_no_spike(self, thresholds):
        assert agent.Agent._is_spike([], 100) == (False, 0.0, 0.0)

    def test_flat_baseline_below_min_abs(self, thresholds):
        series = [5] * 24
        is_spike, mean, latest = agent.Agent._is_spike(series, 6)
        assert is_spike is False
        assert mean == 5.0
        assert latest == 6

    def test_zero_variance_spike(self, thresholds):
        # std=0: порог = max(5 + 0, 2*5) = 10; 100 > 10 и >= MIN_ABS_SPIKE
        is_spike, _, _ = agent.Agent._is_spike([5] * 24, 100)
        assert is_spike is True

    def test_std_based_threshold(self, thresholds):
        # mean=3, std=sqrt(2)~1.41: порог = max(3+4.24, 6) = 7.24
        series = [1, 2, 3, 4, 5] * 4
        is_spike, _, _ = agent.Agent._is_spike(series, 25)
        assert is_spike is True  # 25 > 7.24 и >= 20

    def test_abs_gate_blocks_below_min_abs(self, thresholds):
        # 15 > порога 7.24, но < MIN_ABS_SPIKE=20 — не всплеск
        series = [1, 2, 3, 4, 5] * 4
        is_spike, _, _ = agent.Agent._is_spike(series, 15)
        assert is_spike is False

    def test_mean_multiplier_dominates(self, thresholds):
        # большой mean при почти нулевом std: работает SPIKE_MEAN_MULTIPLIER
        series = [50] * 23 + [52]
        is_spike, mean, _ = agent.Agent._is_spike(series, 60)
        assert is_spike is False  # порог ~100
        assert abs(mean - 50.08) < 0.01
        is_spike, _, _ = agent.Agent._is_spike(series, 120)
        assert is_spike is True


class TestErrorQuery:
    def test_no_stream_returns_bare_pattern(self):
        q = agent.Agent._error_query(None)
        assert q.startswith("(") and q.endswith(")")
        assert "_stream" not in q

    def test_empty_stream_same_as_none(self):
        assert agent.Agent._error_query("{}") == agent.Agent._error_query(None)

    def test_named_stream_appended_with_and(self):
        base = agent.Agent._error_query(None)
        stream = '{app="billing"}'
        q = agent.Agent._error_query(stream)
        # AND снаружи скобок: без обёртки OR-цепочка склеилась бы неверно
        assert q == base + " AND _stream:" + stream
