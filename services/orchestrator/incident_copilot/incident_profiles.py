from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from math import isfinite
from dataclasses import dataclass
from typing import Literal

from .models import Evidence
from .signal_thresholds import METRIC_UNITS, RATIO_METRICS, THRESHOLDS

RemediationAction = Literal["none", "restart_deployment", "rollback_release"]

DIAGNOSTIC_METRICS = (
    "request_latency_p95",
    "request_error_rate",
    "db_pool_active_ratio",
    "cpu_utilization",
    "memory_working_set",
    "kafka_consumer_lag",
    "retry_rate",
    "cache_miss_rate",
)


@dataclass(frozen=True)
class IncidentProfile:
    key: str
    cause: str
    alternative_cause: str
    metrics: tuple[str, ...]
    log_query: str
    knowledge_query: str
    action: RemediationAction
    confidence: float
    expected_result: str


PROFILES = {
    "database_connection_pool_exhaustion": IncidentProfile(
        key="database_connection_pool_exhaustion",
        cause="Database connection pool exhaustion",
        alternative_cause="Database resource contention",
        metrics=("db_pool_active_ratio", "request_latency_p95"),
        log_query="acquisition timed out",
        knowledge_query="database pool saturation acquisition timeout",
        action="rollback_release",
        confidence=0.87,
        expected_result="Restore the previous query pattern and drain pool waiters.",
    ),
    "downstream_timeout": IncidentProfile(
        key="downstream_timeout",
        cause="Downstream dependency timeout",
        alternative_cause="Upstream request deadline regression",
        metrics=("request_latency_p95", "request_error_rate"),
        log_query="downstream request timed out",
        knowledge_query="downstream dependency timeout deadline",
        action="none",
        confidence=0.84,
        expected_result="Bound retries and isolate the unhealthy dependency.",
    ),
    "cpu_saturation": IncidentProfile(
        key="cpu_saturation",
        cause="CPU saturation and request throttling",
        alternative_cause="Traffic increase without CPU contention",
        metrics=("cpu_utilization", "request_latency_p95"),
        log_query="request completed",
        knowledge_query="cpu saturation throttling latency",
        action="restart_deployment",
        confidence=0.85,
        expected_result=(
            "Recycle the degraded replica while the regression is investigated."
        ),
    ),
    "memory_leak": IncidentProfile(
        key="memory_leak",
        cause="Monotonic application memory leak",
        alternative_cause="Expected cache warming",
        metrics=("memory_working_set", "request_latency_p95"),
        log_query="request completed",
        knowledge_query="memory leak monotonic growth oom",
        action="rollback_release",
        confidence=0.83,
        expected_result="Remove the introducing revision and stop memory growth.",
    ),
    "deployment_regression": IncidentProfile(
        key="deployment_regression",
        cause="Release regression after deployment",
        alternative_cause="Unrelated dependency failure during rollout",
        metrics=("request_error_rate", "request_latency_p95"),
        log_query="invalid ledger response shape",
        knowledge_query="deployment regression new revision error rollback",
        action="rollback_release",
        confidence=0.9,
        expected_result="Restore the prior healthy response contract.",
    ),
    "kafka_consumer_lag": IncidentProfile(
        key="kafka_consumer_lag",
        cause="Kafka consumer processing slowdown",
        alternative_cause="Producer traffic burst",
        metrics=("kafka_consumer_lag", "request_latency_p95"),
        log_query="request completed",
        knowledge_query="kafka consumer lag hot partition rebalance",
        action="restart_deployment",
        confidence=0.82,
        expected_result="Recover the consumer while partition skew is investigated.",
    ),
    "retry_storm": IncidentProfile(
        key="retry_storm",
        cause="Downstream retry amplification storm",
        alternative_cause="Organic request-volume increase",
        metrics=("retry_rate", "request_latency_p95"),
        log_query="downstream dependency failed",
        knowledge_query="retry amplification storm backoff circuit breaker",
        action="none",
        confidence=0.86,
        expected_result="Apply retry budgets, backoff, and circuit breaking.",
    ),
    "cache_penetration": IncidentProfile(
        key="cache_penetration",
        cause="Cache penetration from repeated missing keys",
        alternative_cause="Normal cache warm-up",
        metrics=("cache_miss_rate", "db_pool_active_ratio"),
        log_query="request completed",
        knowledge_query="cache penetration missing keys database qps",
        action="none",
        confidence=0.84,
        expected_result="Enable negative caching and request coalescing.",
    ),
}

SERVICE_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)+")


def classify_question(question: str) -> IncidentProfile:
    normalized = " ".join(question.lower().split())
    key = _matching_profile_key(normalized)
    if key is not None:
        return PROFILES[key]
    return PROFILES["database_connection_pool_exhaustion"]


def has_explicit_incident_signal(question: str) -> bool:
    return _matching_profile_key(" ".join(question.lower().split())) is not None


def _matching_profile_key(normalized: str) -> str | None:
    rules = (
        ("kafka_consumer_lag", ("consumer lag", "kafka", "消费延迟")),
        ("cache_penetration", ("cache", "缓存", "missing key")),
        ("retry_storm", ("retry", "重试", "请求量突然放大", "amplif")),
        ("memory_leak", ("memory", "内存", "oom")),
        ("cpu_saturation", ("cpu", "throttl")),
        (
            "deployment_regression",
            ("发布后", "deploy", "rollout", "v142", "revision"),
        ),
        ("downstream_timeout", ("timeout", "超时", "downstream")),
        ("database_connection_pool_exhaustion", ("pool", "连接池", "database")),
    )
    for key, terms in rules:
        if any(term in normalized for term in terms):
            return key
    return None


def select_primary_service(question: str, profile: IncidentProfile) -> str:
    normalized = question.lower()
    services = SERVICE_PATTERN.findall(normalized)
    if services:
        # Mentioning a downstream service must not silently retarget the
        # complaint, its deployment lookup, or an existing approval boundary.
        return services[0]
    if profile.key == "kafka_consumer_lag" and "ledger consumer" in normalized:
        return "ledger-service"
    return "payment-service"


def infer_profile_from_evidence(
    evidence: list[Evidence],
    fallback: IncidentProfile | None,
) -> IncidentProfile | None:
    candidates = infer_profiles_from_evidence(evidence)
    return candidates[0] if candidates else fallback


def infer_profiles_from_evidence(evidence: list[Evidence], *, now: datetime | None = None) -> list[IncidentProfile]:
    now = now or datetime.now(UTC)
    metric_series: dict[str, list[float]] = {}
    log_messages: list[str] = []
    for item in evidence:
        if item.score <= 0:
            continue
        if item.kind.value == "metric" and item.query:
            if "unit" in item.attributes and item.attributes["unit"] != METRIC_UNITS.get(item.query):
                continue
            series = item.attributes.get("series")
            if isinstance(series, list) and 0 < len(series) <= 2048:
                values = []
                for point in series:
                    if not isinstance(point, dict) or isinstance(point.get("value"), bool):
                        continue
                    try:
                        value = float(point["value"])
                    except (KeyError, TypeError, ValueError, OverflowError):
                        continue
                    if isfinite(value) and value >= 0 and (item.query not in RATIO_METRICS or value <= 1):
                        values.append(value)
                if values:
                    metric_series[item.query] = values
        if item.kind.value == "log":
            events = item.attributes.get("events")
            if isinstance(events, list):
                # Retrieval retains 30 minutes for context; current diagnosis
                # only uses timestamped log signatures in the latest 5 minutes.
                current = []
                for event in events:
                    if not isinstance(event, dict) or not isinstance(event.get("message"), str):
                        continue
                    try:
                        timestamp = datetime.fromisoformat(str(event["timestamp"]).replace("Z", "+00:00"))
                    except (KeyError, ValueError, TypeError):
                        continue
                    if timestamp.tzinfo is not None and now - timedelta(minutes=5) <= timestamp <= now:
                        current.append((timestamp, event["message"].lower()))
                log_messages.extend(message for _, message in sorted(current)[-40:])

    combined_logs = " ".join(log_messages)
    def sustained_above(metric: str) -> bool:
        recent = metric_series.get(metric, [])[-2:]
        return len(recent) == 2 and all(value > THRESHOLDS[metric] for value in recent)

    candidates = []
    if sustained_above("cache_miss_rate"):
        candidates.append(PROFILES["cache_penetration"])
    if sustained_above("retry_rate"):
        candidates.append(PROFILES["retry_storm"])
    if sustained_above("kafka_consumer_lag"):
        candidates.append(PROFILES["kafka_consumer_lag"])
    if has_memory_growth(metric_series.get("memory_working_set", [])):
        candidates.append(PROFILES["memory_leak"])
    if sustained_above("cpu_utilization"):
        candidates.append(PROFILES["cpu_saturation"])
    if "invalid ledger response shape" in combined_logs:
        candidates.append(PROFILES["deployment_regression"])
    if "downstream request timed out" in combined_logs:
        candidates.append(PROFILES["downstream_timeout"])
    if (
        "acquisition timed out" in combined_logs
        or sustained_above("db_pool_active_ratio")
    ):
        candidates.append(PROFILES["database_connection_pool_exhaustion"])
    return candidates


def has_memory_growth(values: list[float]) -> bool:
    """Require sustained growth, not a large but healthy working set.

    Gauge samples are returned chronologically by the telemetry gateway. Use
    the most recent five finite samples, allowing plateaus between allocations
    but requiring at least two increases and material absolute/relative growth.
    This is a leak hypothesis signal, not proof of retained-object ownership.
    """
    recent = values[-5:]
    if len(recent) < 3 or any(value < 0 for value in recent):
        return False
    differences = [right - left for left, right in zip(recent, recent[1:])]
    growth = recent[-1] - recent[0]
    return (
        all(delta >= 0 for delta in differences)
        and sum(delta > 0 for delta in differences) >= 2
        and growth >= 1024 * 1024
        and growth >= recent[0] * 0.2
    )
