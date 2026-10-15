"""Sanitized Deployment Event observations, not release/causation assertions."""
from datetime import UTC, datetime, timedelta
import re

REASONS = {"ScalingReplicaSet", "NewReplicaSetCreated", "ProgressDeadlineExceeded",
           "FailedCreate", "ReplicaSetUpdated", "DeploymentRollback"}


def timestamp(value):
    if value in (None, "", "0001-01-01T00:00:00Z"):
        return None
    if not isinstance(value, str):
        raise ValueError("Invalid event time")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.year < 1970:
        raise ValueError("Invalid event time")
    return parsed.astimezone(UTC)


def normalize_events(items, *, uid, service, namespace, now):
    retained, rejected, duplicates = {}, 0, 0
    for item in items:
        try:
            metadata = item["metadata"]
            subject = item["involvedObject"]
            event_uid = metadata.get("uid")
            if (item.get("apiVersion") != "v1" or item.get("kind") != "Event"
                    or metadata.get("namespace") != namespace or not isinstance(event_uid, str)
                    or re.fullmatch(r"[A-Za-z0-9-]{1,128}", event_uid) is None
                    or subject.get("uid") != uid or subject.get("name") != service
                    or subject.get("namespace") != namespace or subject.get("kind") != "Deployment"
                    or subject.get("apiVersion") != "apps/v1" or item.get("reason") not in REASONS):
                raise ValueError("Event identity/reason is not eligible")
            series = item.get("series") or {}
            first = timestamp(item.get("eventTime")) or timestamp(item.get("firstTimestamp"))
            last = timestamp(series.get("lastObservedTime")) or timestamp(item.get("lastTimestamp"))
            if (first is None and last is None or first and last and first > last
                    or any(value and value > now + timedelta(seconds=30) for value in (first, last))):
                raise ValueError("Event time is unavailable/inconsistent")
            count = series.get("count", item.get("count"))
            if count is not None and (not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 10**9):
                raise ValueError("Invalid event count")
            row = {"event_uid": event_uid, "deployment_uid": uid, "service": service,
                   "namespace": namespace, "reason": item["reason"],
                   "type": item.get("type") if item.get("type") in {"Normal", "Warning"} else "unknown",
                   "first_observed_at": first.isoformat() if first else None,
                   "last_observed_at": last.isoformat() if last else None, "count": count,
                   "data_origin": "kubernetes_api", "timestamp_semantics": "event_observation_not_rollout_time"}
            if event_uid in retained:
                if retained[event_uid] != row:
                    # Never pick one of two conflicting versions of an Event.
                    return {"status": "unavailable", "reason": "conflicting_event_identity", "events": []}
                duplicates += 1
            else:
                retained[event_uid] = row
        except (TypeError, ValueError, KeyError, AttributeError):
            rejected += 1
    rows = sorted(retained.values(), key=lambda row: row["last_observed_at"] or row["first_observed_at"])
    return {"status": "available" if rows else "empty", "events": rows,
            "returned_valid_events_only": True, "rejected_records": rejected,
            "deduplicated_records": duplicates, "causation_verified": False,
            "retention_warning": "Events may expire; absence does not prove that no deployment occurred."}
