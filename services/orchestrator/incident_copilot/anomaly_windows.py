"""Bounded fault-lab signal windows; these thresholds are not production SLOs."""
from datetime import UTC, datetime, timedelta
from math import isfinite
from typing import Any

from .incident_profiles import has_memory_growth
from .models import Evidence
from .signal_thresholds import THRESHOLDS


def detect_anomaly_windows(
    evidence: list[Evidence], *, now: datetime,
) -> list[dict[str, Any]]:
    windows = []
    for item in evidence:
        if item.kind.value != "metric" or item.score <= 0:
            continue
        raw = item.attributes.get("series")
        if not isinstance(raw, list):
            continue
        samples: dict[datetime, float] = {}
        for point in raw:
            if not isinstance(point, dict) or isinstance(point.get("value"), bool):
                continue
            try:
                timestamp = datetime.fromisoformat(str(point["timestamp"]).replace("Z", "+00:00"))
                value = float(point["value"])
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            if timestamp.tzinfo is None or not isfinite(value):
                continue
            timestamp = timestamp.astimezone(UTC)
            if now - timedelta(minutes=30) <= timestamp <= now:
                samples[timestamp] = value
        ordered = sorted(samples.items())
        if item.query == "memory_working_set":
            recent = ordered[-5:]
            if (has_memory_growth([value for _, value in recent])
                    and all(right[0] - left[0] <= timedelta(minutes=2)
                            for left, right in zip(recent, recent[1:]))):
                windows.append(_window(item, recent, "sustained_memory_growth", None,
                                       len(ordered) == len(recent)))
            continue
        threshold = THRESHOLDS.get(item.query or "")
        if threshold is None:
            continue
        run = []
        for timestamp, value in ordered:
            if value > threshold:
                # Missing scrapes must not bridge two independent anomalies.
                if run and timestamp - run[-1][0] > timedelta(minutes=2):
                    if len(run) >= 2:
                        windows.append(_window(item, run, "threshold", threshold,
                                               run[0] == ordered[0]))
                    run = []
                run.append((timestamp, value))
            else:
                if len(run) >= 2:
                    windows.append(_window(item, run, "threshold", threshold,
                                           run[0] == ordered[0]))
                run = []
        if len(run) >= 2:
            windows.append(_window(item, run, "threshold", threshold,
                                   run[0] == ordered[0]))
    return sorted(windows, key=lambda window: window["start"])


def _window(item, samples, criterion, threshold, left_censored):
    return {
        "evidence_id": item.id, "metric": item.query,
        "start": samples[0][0].isoformat(), "end": samples[-1][0].isoformat(),
        "sample_count": len(samples), "peak": max(value for _, value in samples),
        "criterion": criterion, "threshold": threshold,
        "left_censored": left_censored,
    }
