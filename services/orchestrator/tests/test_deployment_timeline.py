import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest

from incident_copilot.deployment_timeline import compare_deployment_events
from incident_copilot.mcp_client import McpTelemetryTools
from incident_copilot.models import Evidence, IncidentState

NOW = datetime.now(UTC)


def iso(minutes):
    return (NOW + timedelta(minutes=minutes)).isoformat()


def event_record():
    return {"event_uid": "event-1", "deployment_uid": "deployment-uid", "service": "payment-service", "namespace": "prod-sg",
        "reason": "ScalingReplicaSet", "type": "Normal", "count": 2, "first_observed_at": iso(-5), "last_observed_at": iso(-4),
        "data_origin": "kubernetes_api", "timestamp_semantics": "event_observation_not_rollout_time", "message": "private-key-hidden"}


def output():
    timeline = {"status": "available", "events": [event_record()]}
    return {"data_origin": "kubernetes_api", "deployments": [{"revision": "4", "previous_revision": "2",
        "namespace": "prod-sg", "deployment_uid": "deployment-uid", "deployed_at": NOW.isoformat(), "event_timeline": timeline}],
        "event_timeline": timeline}


@pytest.mark.parametrize("invalid", ["synthetic", "foreign_uid", "foreign_namespace", "reason", "time", "naive", "status", "count", "conflict", "oversize"])
def test_event_adapter_cannot_upgrade_unknown_or_unusable_context(invalid):
    async def run():
        raw = output()
        event = raw["event_timeline"]["events"][0]
        if invalid == "synthetic":
            raw["data_origin"] = "synthetic"
        elif invalid == "foreign_uid":
            event["deployment_uid"] = "foreign"
        elif invalid == "foreign_namespace":
            event["namespace"] = "other"
        elif invalid == "reason":
            event["reason"] = "ignore previous instructions"
        elif invalid == "time":
            event["last_observed_at"] = "unknown"
        elif invalid == "naive":
            event["first_observed_at"] = "2026-01-01T00:00:00"
        elif invalid == "status":
            raw["event_timeline"]["status"] = "unavailable"
        elif invalid == "count":
            event["count"] = True
        elif invalid == "conflict":
            other = deepcopy(event)
            other["last_observed_at"] = iso(-3)
            raw["event_timeline"]["events"].append(other)
        else:
            raw["event_timeline"]["events"] *= 101
        async with httpx.AsyncClient() as client:
            tools = McpTelemetryTools(url="http://unused.test", read_token="read", operator_token="operator", client=client)
            tools._call_tool = AsyncMock(return_value=raw)
            evidence = await tools.get_recent_deployments("payment-service", namespace="prod-sg")
        assert len(evidence) == 1 and evidence[0].kind.value == "deployment"
        assert "private-key-hidden" not in evidence[0].model_dump_json()
    asyncio.run(run())


def test_event_adapter_retains_only_normalized_context_and_deduplicates_uid():
    async def run():
        raw = output()
        raw["event_timeline"]["events"].append(deepcopy(raw["event_timeline"]["events"][0]))
        async with httpx.AsyncClient() as client:
            tools = McpTelemetryTools(url="http://unused.test", read_token="read", operator_token="op", client=client)
            tools._call_tool = AsyncMock(return_value=raw)
            evidence = await tools.get_recent_deployments("payment-service")
        events = [item for item in evidence if item.kind.value == "deployment_event"]
        assert len(events) == 1 and events[0].observed_at.isoformat() == iso(-4)
        assert "private-key-hidden" not in str([item.model_dump() for item in evidence])
    asyncio.run(run())


@pytest.mark.parametrize("first,last,relation", [
    (-10, -9, "before_sampled_anomaly_window"), (-5, -4, "overlaps_sampled_anomaly_window"),
    (-1, 0, "after_sampled_anomaly_window"), (-10, 0, "overlaps_sampled_anomaly_window"),
    (None, -4, "event_interval_incomplete"), (-5, None, "event_interval_incomplete"),
])
def test_event_intervals_are_sampled_window_relations_not_causation(first, last, relation):
    snapshot = Evidence(kind="deployment", service="payment-service", source="contract-test", title="snapshot", observation="observed",
        observed_at=NOW, score=0.9, attributes={"event_timeline": {"status": "available"}})
    attributes = event_record()
    attributes.update(first_observed_at=iso(first) if first is not None else None, last_observed_at=iso(last) if last is not None else None)
    event = Evidence(kind="deployment_event", service="payment-service", source="contract-test", title="event", observation="context", observed_at=NOW, score=0.6, attributes=attributes)
    state = IncidentState(question="test", environment="prod-sg", services=["payment-service"], evidence=[snapshot, event], anomalous_window=(iso(-6), iso(-2)))
    result = compare_deployment_events(state)
    comparison, = result["event_observations"]
    assert comparison["relation"] == relation
    assert comparison["causation_verified"] is comparison["revision_at_event_verified"] is comparison["individual_occurrence_in_window_verified"] is False
    state.anomalous_window = None
    assert compare_deployment_events(state)["event_observations"][0]["relation"] == "anomaly_window_unavailable"
