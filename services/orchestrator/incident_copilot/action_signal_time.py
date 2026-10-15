"""Current retained action samples, not a live refresh or causal validation."""
from datetime import UTC, datetime, timedelta
from math import isfinite

from .models import Evidence
from .signal_thresholds import METRIC_UNITS, RATIO_METRICS

WINDOW = timedelta(minutes=5)
MAX_GAP = timedelta(minutes=2)


def timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return result.astimezone(UTC) if result.tzinfo is not None else None


def current_action_signals(signals: list[Evidence], *, now: datetime):
    if now.tzinfo is None:
        raise ValueError("Action sample evaluation requires an aware clock")
    now = now.astimezone(UTC)
    retained, counts = [], []
    for item in signals:
        attributes = dict(item.attributes)
        if item.kind.value == "metric":
            raw = attributes.get("series")
            samples, conflicts = {}, set()
            unit_valid = "unit" not in attributes or attributes["unit"] == METRIC_UNITS.get(item.query)
            bounded_series = raw if unit_valid and isinstance(raw, list) and len(raw) <= 2048 else []
            for point in bounded_series:
                if not isinstance(point, dict) or isinstance(point.get("value"), bool):
                    continue
                observed = timestamp(point.get("timestamp"))
                if observed is None or not now - WINDOW <= observed <= now:
                    continue
                try:
                    value = float(point["value"])
                except (KeyError, ValueError, TypeError, OverflowError):
                    continue
                if not isfinite(value) or value < 0 or item.query in RATIO_METRICS and value > 1:
                    continue
                if observed in samples and samples[observed] != value:
                    conflicts.add(observed)
                samples[observed] = value
            ordered = sorted((time, value) for time, value in samples.items() if time not in conflicts)
            # Only the latest contiguous segment may support a sustained signal.
            start = 0
            for index in range(1, len(ordered)):
                if ordered[index][0] - ordered[index - 1][0] > MAX_GAP:
                    start = index
            ordered = ordered[start:]
            attributes["series"] = [{"timestamp": time.isoformat(), "value": value} for time, value in ordered]
            count = len(ordered)
        else:
            raw = attributes.get("events")
            events = []
            for event in raw if isinstance(raw, list) else []:
                if not isinstance(event, dict) or not isinstance(event.get("message"), str):
                    continue
                observed = timestamp(event.get("timestamp"))
                if observed is not None and now - WINDOW <= observed <= now:
                    events.append((observed, event))
            attributes["events"] = [event for _, event in sorted(events, key=lambda pair: pair[0])]
            count = len(events)
        counts.append({"evidence_id": item.id, "kind": item.kind.value, "eligible_samples": count})
        if count:
            retained.append(item.model_copy(update={"attributes": attributes}))
    return retained, {"evaluated_at": now.isoformat(), "window_seconds": int(WINDOW.total_seconds()),
        "max_metric_gap_seconds": int(MAX_GAP.total_seconds()), "samples": counts,
        "limitation": "Retained observation freshness only; no telemetry refresh, semantic or causal validation."}
