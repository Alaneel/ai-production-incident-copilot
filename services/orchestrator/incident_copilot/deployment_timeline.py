"""Compare observation intervals to sampled metrics, never assert causation."""
from datetime import datetime


def compare_deployment_events(incident):
    if not incident.services:
        return None
    contexts = [item for item in incident.evidence if item.kind.value == "deployment"
                and item.service == incident.services[0] and "event_timeline" in item.attributes]
    if not contexts:
        return None
    observations = []
    for item in incident.evidence:
        if item.kind.value != "deployment_event" or item.service != incident.services[0]:
            continue
        attributes = item.attributes
        relation = "anomaly_window_unavailable"
        try:
            if incident.anomalous_window is not None:
                start, end = incident.anomalous_window
                first = attributes.get("first_observed_at")
                last = attributes.get("last_observed_at")
                if first is None or last is None:
                    relation = "event_interval_incomplete"
                else:
                    first, last = datetime.fromisoformat(first), datetime.fromisoformat(last)
                    if first > last or start > end or any(value.tzinfo is None for value in (first, last, start, end)):
                        relation = "event_interval_unusable"
                    elif last < start:
                        relation = "before_sampled_anomaly_window"
                    elif first > end:
                        relation = "after_sampled_anomaly_window"
                    else:
                        relation = "overlaps_sampled_anomaly_window"
        except (TypeError, ValueError):
            relation = "event_interval_unusable"
        observations.append({"evidence_id": item.id, "event_uid": attributes.get("event_uid"),
            "first_observed_at": attributes.get("first_observed_at"),
            "last_observed_at": attributes.get("last_observed_at"), "relation": relation,
            "causation_verified": False, "revision_at_event_verified": False})
        observations[-1]["individual_occurrence_in_window_verified"] = False
    return {"timeline_statuses": [item.attributes["event_timeline"].get("status", "unknown")
            if isinstance(item.attributes["event_timeline"], dict) else "unavailable" for item in contexts],
            "anomaly_window": incident.anomalous_window, "event_observations": observations,
            "causation_verified": False,
            "limitation": "Metric window is sampled/censored; event observations do not prove deployment timing, release revision or incident causation."}
