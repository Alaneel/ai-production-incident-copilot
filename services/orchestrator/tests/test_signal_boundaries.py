from datetime import UTC, datetime, timedelta

import pytest

from incident_copilot.incident_profiles import infer_profile_from_evidence
from incident_copilot.action_signal_time import current_action_signals
from incident_copilot.models import Evidence
from incident_copilot.signal_thresholds import METRIC_UNITS, THRESHOLDS


def metric(name, values, score=0.9):
    return Evidence(kind="metric", source="snapshot", title=name, observation="retained samples",
                    observed_at=datetime.now(UTC), service="payment-service", score=score, query=name,
                    attributes={"series": [{"value": value} for value in values]})


@pytest.mark.parametrize("name", ["cpu_utilization", "kafka_consumer_lag", "retry_rate", "cache_miss_rate", "db_pool_active_ratio"])
@pytest.mark.parametrize("pattern", ["below", "equal", "one_spike", "recovered"])
def test_non_sustained_or_below_threshold_signal_does_not_classify(name, pattern):
    threshold = THRESHOLDS[name]
    samples = {
        "below": [threshold * 0.75] * 5,
        "equal": [threshold] * 5,
        "one_spike": [0, 0, 0, 0, threshold * 2],
        "recovered": [0, threshold * 2, threshold * 2, 0, 0],
    }
    assert infer_profile_from_evidence([metric(name, samples[pattern])], None) is None


def test_boolean_and_unavailable_series_cannot_become_fault_evidence():
    assert infer_profile_from_evidence([metric("cpu_utilization", [True, True])], None) is None
    assert infer_profile_from_evidence([metric("cpu_utilization", [0.95, 0.95], score=0)], None) is None


@pytest.mark.parametrize("name", list(METRIC_UNITS))
@pytest.mark.parametrize("unit_mode", ["missing", "correct", "wrong", "null"])
def test_primary_classifier_and_action_samples_share_the_explicit_unit_contract(name, unit_mode):
    now = datetime.now(UTC)
    values = [20*1024*1024, 25*1024*1024, 30*1024*1024] if name == "memory_working_set" else [
        {"request_latency_p95": 2500, "request_error_rate": .1, "db_pool_active_ratio": .96,
         "cpu_utilization": .96, "kafka_consumer_lag": 200, "retry_rate": 4, "cache_miss_rate": 4}[name]
    ]*3
    item = metric(name, values)
    item.attributes["series"] = [{"timestamp": (now - timedelta(seconds=30-index*10)).isoformat(), "value": value}
                                  for index, value in enumerate(values)]
    if unit_mode != "missing":
        item.attributes["unit"] = METRIC_UNITS[name] if unit_mode == "correct" else None if unit_mode == "null" else "wrong-unit"
    retained, check = current_action_signals([item], now=now)
    assert check["samples"][0]["eligible_samples"] == (3 if unit_mode in {"missing", "correct"} else 0)
    if unit_mode in {"wrong", "null"}:
        assert retained == []
        assert infer_profile_from_evidence([item], None) is None


@pytest.mark.parametrize("value", [-1, 1.01, float("inf"), float("nan"), True])
def test_impossible_ratio_values_never_classify_or_support_an_action(value):
    now = datetime.now(UTC)
    item = metric("cpu_utilization", [value, value])
    item.attributes["series"] = [{"timestamp": (now - timedelta(seconds=30-index*15)).isoformat(), "value": value}
                                  for index in range(2)]
    assert current_action_signals([item], now=now)[0] == []
    assert infer_profile_from_evidence([item], None) is None


def test_oversized_metric_series_is_unavailable_at_both_primary_boundaries():
    now = datetime.now(UTC)
    item = metric("cpu_utilization", [.96]*2049)
    item.attributes["series"] = [{"timestamp": now.isoformat(), "value": .96}]*2049
    assert current_action_signals([item], now=now)[0] == []
    assert infer_profile_from_evidence([item], None) is None
