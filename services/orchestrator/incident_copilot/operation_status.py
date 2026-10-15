"""Conservative public projection of read-only operation observations."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class OperationObservation(BaseModel):
    status: Literal["unknown", "marker_matches", "not_observed", "unverified_marker",
                    "conflicting_marker", "recorded_response", "conflicting_record"]
    source: Literal["kubernetes_deployment_marker", "process_cache", "fixture"]
    read_only: Literal[True]
    rollout_health_verified: Literal[False]
    observed_at: datetime | None = None


def public_observation(result: dict) -> dict:
    # Provider notes, responses, fingerprints and credentials are never echoed.
    if not isinstance(result, dict) or result.get("read_only") is not True or result.get("rollout_health_verified") is not False:
        raise ValueError("Invalid inspection safety flags.")
    observation = OperationObservation.model_validate(result)
    allowed = {
        "kubernetes_deployment_marker": {"marker_matches", "not_observed", "unverified_marker", "conflicting_marker"},
        "process_cache": {"unknown", "recorded_response", "conflicting_record"},
        "fixture": {"unknown"},
    }
    if observation.status not in allowed[observation.source]:
        raise ValueError("Inspection status does not match its source.")
    return {**observation.model_dump(mode="json"),
            "note": "Latest marker/cache only: absence does not prove non-execution; a match does not prove rollout health."}
