import asyncio
import copy
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from incident_copilot.action_applicability import assess_action_applicability
from incident_copilot.action_signal_time import current_action_signals
from incident_copilot.approval import ApprovalReceiptSigner
from incident_copilot.guardrails import GuardrailViolation
from incident_copilot.langgraph_workflow import LangGraphIncidentWorkflow
from incident_copilot.models import ApprovalDecision, Evidence, Hypothesis, IncidentQuestion, IncidentState
from incident_copilot.tools import DemoTelemetryTools


NOW = datetime(2026, 10, 3, 2, tzinfo=UTC)


def metric(points):
    return Evidence(kind="metric", service="payment-service", source="offline-test",
        title="pool", observation="Retained samples", observed_at=NOW, score=0.9,
        query="db_pool_active_ratio", attributes={"series": points})


def point(seconds, value=1):
    return {"timestamp": (NOW - timedelta(seconds=seconds)).isoformat(), "value": value}


@pytest.mark.parametrize("points,count", [
    ([point(30), point(15)], 2),
    ([point(15), point(30)], 2),
    ([point(30), point(30)], 1),
    ([point(30, 0.2), point(30), point(30), point(15)], 1),
    ([point(250), point(15)], 1),
    ([point(400), point(350)], 0),
    ([point(-30), point(-15)], 0),
    ([{"value": 1}, {"timestamp": "broken", "value": 1}], 0),
    ([{"timestamp": NOW.replace(tzinfo=None).isoformat(), "value": 1}], 0),
    ([point(30, True), point(15, "NaN"), point(0, "inf")], 0),
    ([point(30, "0.99"), point(15, "1.0")], 2),
])
def test_current_samples_are_sorted_unique_finite_aware_and_contiguous_without_mutating_source(points, count):
    item = metric(points)
    before = copy.deepcopy(item)
    retained, check = current_action_signals([item], now=NOW)
    assert item == before
    assert check["samples"][0]["eligible_samples"] == count
    assert check["evaluated_at"] == NOW.isoformat()
    if count:
        samples = retained[0].attributes["series"]
        assert len(samples) == count
        assert samples == sorted(samples, key=lambda value: value["timestamp"])
        assert len({value["timestamp"] for value in samples}) == count
    else:
        assert retained == []


@pytest.mark.parametrize("points", [
    [point(400), point(350)], [point(-30), point(-15)],
    [point(250), point(15)], [point(30), point(30)],
    [point(30), point(15, 0.1)],
    [{"value": 1}, {"value": 1}],
])
def test_old_or_unusable_metric_values_cannot_support_destructive_action(points):
    item = metric(points)
    log = Evidence(kind="log", service="payment-service", source="offline-test",
        title="log", observation="Normal request", observed_at=NOW, score=0.9,
        attributes={"events": [{"timestamp": NOW.isoformat(), "message": "request completed"}]})
    incident = IncidentState(question="test", environment="offline-test", services=["payment-service"],
        incident_type="database_connection_pool_exhaustion", evidence=[item, log],
        hypotheses=[Hypothesis(rank=1, cause="Pool exhaustion", confidence=1,
            evidence_ids=[item.id, log.id])])
    with patch("incident_copilot.action_applicability.datetime") as clock:
        clock.now.return_value = NOW
        result = assess_action_applicability(incident, "rollback_release")
    assert result["allowed"] is False
    assert result["matched_profiles"] == []
    assert result["signal_time_check"]["window_seconds"] == 300
    assert incident.evidence[0].attributes["series"] == points


def test_log_observations_use_event_time_not_retrieval_time():
    item = Evidence(kind="log", service="payment-service", source="offline-test",
        title="log", observation="Retrieved now", observed_at=NOW, score=0.9,
        attributes={"events": [
            {"timestamp": (NOW - timedelta(hours=1)).isoformat(), "message": "acquisition timed out"},
            {"timestamp": NOW.isoformat(), "message": "request completed"},
            {"timestamp": (NOW + timedelta(seconds=1)).isoformat(), "message": "acquisition timed out"}]})
    retained, check = current_action_signals([item], now=NOW)
    assert retained[0].attributes["events"] == [item.attributes["events"][1]]
    assert check["samples"][0]["eligible_samples"] == 1


def test_naive_evaluation_clock_is_rejected():
    with pytest.raises(ValueError, match="aware clock"):
        current_action_signals([], now=NOW.replace(tzinfo=None))


def test_expired_approval_samples_fail_before_intent_and_existing_intent_dispatch():
    async def run():
        tools = DemoTelemetryTools()
        tools.allow_simulated_action_summaries = False
        now = datetime.now(UTC)
        item = metric([{"timestamp": (now - timedelta(seconds=offset)).isoformat(), "value": 1}
                       for offset in (30, 15)])
        log = Evidence(kind="log", service="payment-service", source="offline-test",
            title="log", observation="Normal request", observed_at=now, score=0.9,
            attributes={"events": [{"timestamp": now.isoformat(), "message": "request completed"}]})
        # A service-scoped provider must not replay the complaint's evidence
        # when the workflow asks for dependency SLIs.
        tools.query_metrics = AsyncMock(side_effect=lambda service, metrics: [item]
            if service == item.service and item.query in metrics else [])
        tools.search_logs = AsyncMock(side_effect=lambda service, query: [log]
            if service == log.service else [])
        tools.rollback_release = AsyncMock(wraps=tools.rollback_release)
        workflow = LangGraphIncidentWorkflow(tools=tools, checkpointer=InMemorySaver(),
            approval_signer=ApprovalReceiptSigner("expiry-test-only-secret"))
        result = await workflow.start(IncidentQuestion(question="Why is payment-service slow?"))
        assert result.status.value == "waiting_approval"
        result.approval_decision, result.approval_actor = ApprovalDecision.APPROVE, "operator"
        arguments = {"incident": {**result.model_dump(mode="json"),
                                  "_approval_idempotency_key": "sample-expiry-approval-0001"}}
        prepared = await workflow._prepare_execution(arguments)
        with patch("incident_copilot.action_applicability.datetime") as clock:
            clock.now.return_value = now + timedelta(minutes=6)
            with pytest.raises(GuardrailViolation, match="Current cited"):
                await workflow._prepare_execution(arguments)
            with pytest.raises(GuardrailViolation, match="Current cited"):
                await workflow._execute(prepared)
        tools.rollback_release.assert_awaited_once()
        assert tools.rollback_release.call_args.kwargs["dry_run"] is True
        retained = await workflow.get(result.incident_id)
        assessment = next(event.payload for event in retained.trace if event.event == "action_signal_applicability_checked")
        assert assessment["signal_time_check"]["samples"]
    asyncio.run(run())
