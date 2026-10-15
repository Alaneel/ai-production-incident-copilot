"""Bounded post-execution rollout/SLI observation, never causal efficacy proof.

The fixed trailing window prevents cherry-picking a healthy subsegment. Older
observations remain incident history but cannot prove present recovery. Backend
origin labels are trust-boundary assertions, not independent attestation.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from math import isfinite
from typing import Any

from .models import Evidence, normalize_target_context
from .signal_thresholds import THRESHOLDS

MAX_EVIDENCE = 32
MAX_SERIES = 2048
WINDOW = timedelta(seconds=60)
FRESHNESS = timedelta(seconds=30)
MIN_SPAN = timedelta(seconds=30)
UNITS = {"request_latency_p95": "milliseconds", "request_error_rate": "ratio"}


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not 1 <= len(value) <= 64:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(UTC) if parsed.tzinfo is not None else None
    except (ValueError, OverflowError):
        return None


def _integer(value: Any, *, minimum: int = 0) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def _hash(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def assess_recovery(incident, deployment: dict, metrics: list[Evidence], *, now: datetime) -> dict:
    """Verify only exact applied-target rollout and sustained observed SLI health.

    Missing provenance, scope, acknowledgement or required observations always
    leaves recovery pending. The supplied clock is an internal aware clock; an
    invalid clock is a programming error, never a successful verification.
    """
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("Recovery verification requires an aware clock.")
    now = now.astimezone(UTC)
    reasons: list[str] = []
    evidence_ids: list[str] = []

    def reason(code: str) -> None:
        if code not in reasons:
            reasons.append(code)

    def pending() -> dict:
        return {"status": "pending", "checked_at": now.isoformat(),
                "reason_codes": reasons, "metric_evidence_ids": evidence_ids,
                "observation_window": None}

    recovery = getattr(incident, "recovery", None)
    if not isinstance(recovery, dict):
        reason("recovery_state_missing")
        return pending()
    if recovery.get("simulation") is not False:
        reason("simulation_or_unknown_backend")
        return pending()
    acknowledgement = _time(recovery.get("execution_acknowledged_at"))
    if acknowledgement is None or acknowledgement > now:
        reason("execution_acknowledgement_invalid")
        return pending()
    applied_generation = recovery.get("applied_generation")
    applied_hash = recovery.get("applied_template_sha256")
    if not _integer(applied_generation, minimum=1) or not _hash(applied_hash):
        reason("applied_target_identity_missing")
        return pending()
    remediation = getattr(incident, "remediation", None)
    arguments = getattr(remediation, "arguments", None)
    if not isinstance(arguments, dict):
        reason("approved_target_context_invalid")
        return pending()
    try:
        context = normalize_target_context(arguments.get("target_context"))
    except (ValueError, TypeError):
        reason("approved_target_context_invalid")
        return pending()
    if (context["tool"] != getattr(remediation, "tool", None)
            or context["service"] != arguments.get("service")
            or context["namespace"] != arguments.get("namespace")
            or context["namespace"] != getattr(incident, "environment", None)
            or context["revision"] != (arguments.get("revision") if context["tool"] == "rollback_release" else None)):
        reason("approved_target_scope_mismatch")
        return pending()

    if (not isinstance(deployment, dict) or deployment.get("read_only") is not True
            or deployment.get("source") != "kubernetes_deployment_health"
            or deployment.get("marker_status") != "marker_matches"):
        reason("deployment_observation_untrusted")
        return pending()
    if any(deployment.get(field) != context[field]
           for field in ("cluster_context", "deployment_uid", "service", "namespace")):
        reason("deployment_target_mismatch")
    generation = deployment.get("generation")
    observed_generation = deployment.get("observed_generation")
    if (not _integer(generation, minimum=1) or generation != applied_generation
            or not _hash(deployment.get("template_sha256"))
            or deployment.get("template_sha256") != applied_hash):
        reason("deployment_applied_target_mismatch")
    desired = deployment.get("desired_replicas")
    if (not _integer(observed_generation, minimum=1) or not _integer(generation, minimum=1)
            or observed_generation < generation or not _integer(desired, minimum=1)
            or any(not _integer(deployment.get(field), minimum=1) or deployment[field] != desired
                   for field in ("total_replicas", "updated_replicas", "ready_replicas", "available_replicas"))
            or not _integer(deployment.get("unavailable_replicas"))
            or deployment.get("unavailable_replicas") != 0):
        reason("deployment_rollout_incomplete")
    observed_at = _time(deployment.get("observed_at"))
    if observed_at is None or not acknowledgement < observed_at <= now or now - observed_at > FRESHNESS:
        reason("deployment_observation_stale_or_invalid")
    if reasons:
        return pending()

    if not isinstance(metrics, list) or len(metrics) > MAX_EVIDENCE:
        reason("metric_evidence_budget_exceeded")
        return pending()
    window_start = max(acknowledgement, now - WINDOW)
    samples: dict[str, dict[datetime, float]] = {metric: {} for metric in UNITS}
    seen_ids: set[str] = set()
    for item in metrics:
        if not isinstance(item, Evidence):
            reason("metric_observation_invalid")
            continue
        metric = item.query
        if metric not in UNITS:
            continue
        if (item.kind.value != "metric" or item.source != "prometheus-via-telemetry-gateway"
                or item.service != context["service"] or item.attributes.get("data_origin") != "prometheus_api"):
            reason("metric_observation_untrusted")
            continue
        if (not isinstance(item.id, str) or not 1 <= len(item.id) <= 200 or item.id in seen_ids):
            reason("metric_evidence_identity_invalid")
            continue
        seen_ids.add(item.id)
        if item.attributes.get("unit") != UNITS[metric]:
            reason("metric_units_mismatch")
            continue
        envelope_time = item.observed_at
        if (envelope_time.tzinfo is None or not acknowledgement < envelope_time <= now
                or now - envelope_time > FRESHNESS):
            reason("metric_observation_stale_or_invalid")
        discarded = item.attributes.get("discarded_samples", 0)
        if not _integer(discarded) or discarded != 0:
            # The client must not erase bad samples before this boundary and
            # present its cleaned subset as an entirely healthy window.
            reason("metric_discarded_samples")
        series = item.attributes.get("series")
        if not isinstance(series, list):
            reason("metric_series_missing_or_invalid")
            continue
        if len(series) > MAX_SERIES:
            reason("metric_series_budget_exceeded")
            continue
        contributes = False
        for point in series:
            if not isinstance(point, dict):
                reason("metric_sample_invalid")
                continue
            time = _time(point.get("timestamp"))
            if time is None:
                reason("metric_sample_invalid")
                continue
            if time > now:
                reason("metric_sample_future")
                continue
            if time <= acknowledgement or time < window_start:
                continue
            value = point.get("value")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                reason("metric_sample_invalid")
                continue
            try:
                value = float(value)
            except (ValueError, OverflowError):
                reason("metric_sample_invalid")
                continue
            if not isfinite(value) or value < 0 or (metric == "request_error_rate" and value > 1):
                reason("metric_sample_invalid")
                continue
            if time in samples[metric] and samples[metric][time] != value:
                reason("metric_sample_conflict")
            if value > THRESHOLDS[metric]:
                reason("metric_threshold_failed")
            samples[metric][time] = value
            contributes = True
        if contributes:
            evidence_ids.append(item.id)

    timelines = {metric: sorted(points) for metric, points in samples.items()}
    for times in timelines.values():
        if len(times) < 3:
            reason("metric_samples_missing")
            continue
        if times[-1] - times[0] < MIN_SPAN or any(right - left > FRESHNESS for left, right in zip(times, times[1:])):
            reason("metric_window_insufficient")
        if now - times[-1] > FRESHNESS:
            reason("metric_window_stale")
    if reasons:
        return pending()
    overlap_start = max(times[0] for times in timelines.values())
    overlap_end = min(times[-1] for times in timelines.values())
    if (overlap_end - overlap_start < MIN_SPAN
            or any(sum(overlap_start <= time <= overlap_end for time in times) < 3
                   for times in timelines.values())):
        reason("metric_shared_window_insufficient")
        return pending()
    return {"status": "verified", "checked_at": now.isoformat(),
            "reason_codes": ["rollout_and_sli_recovery_observed_not_causal_proof"],
            "metric_evidence_ids": evidence_ids,
            "observation_window": [overlap_start.isoformat(), overlap_end.isoformat()]}
