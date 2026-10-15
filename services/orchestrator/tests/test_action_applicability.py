import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from incident_copilot.action_applicability import assess_action_applicability, validate_action_applicability
from incident_copilot.approval import ApprovalReceiptSigner
from incident_copilot.guardrails import GuardrailViolation
from incident_copilot.langgraph_workflow import LangGraphIncidentWorkflow
from incident_copilot.models import ApprovalDecision, Evidence, Hypothesis, IncidentQuestion, IncidentState
from incident_copilot.tools import DemoTelemetryTools


def signal(metric="db_pool_active_ratio", values=(0.98, 1.0), service="payment-service"):
    now = datetime.now(UTC)
    return Evidence(kind="metric", service=service, source="contract-fixture", title=metric,
        observation="Retained bounded samples", query=metric, observed_at=now, score=0.9,
        attributes={"series": [{"timestamp": (now - timedelta(seconds=30-i)).isoformat(), "value": value}
                               for i, value in enumerate(values)]})


def state():
    metric = signal()
    log = Evidence(kind="log", service="payment-service", source="contract-fixture", title="logs",
        observation="Current request records", observed_at=datetime.now(UTC), score=0.9,
        attributes={"events": [{"timestamp": datetime.now(UTC).isoformat(), "message": "request completed"}]})
    return IncidentState(question="test", environment="prod-sg", services=["payment-service"],
        incident_type="database_connection_pool_exhaustion", evidence=[metric, log],
        hypotheses=[Hypothesis(rank=1, cause="Provisional pool exhaustion", confidence=0.99,
                               evidence_ids=[metric.id, log.id])])


def test_matching_cited_signal_is_applicable_but_not_semantically_or_causally_verified():
    incident = state()
    result = assess_action_applicability(incident, "rollback_release")
    assert result["allowed"] and result["matched_profiles"] == ["database_connection_pool_exhaustion"]
    assert result["semantic_entailment_verified"] is result["causation_verified"] is False
    assert result["leading_hypothesis_id"] == incident.hypotheses[0].id
    assert result["leading_cause"] == incident.hypotheses[0].cause
    assert result["leading_evidence_ids"] == incident.hypotheses[0].evidence_ids
    assert result["primary_service"] == incident.services[0]


@pytest.mark.parametrize("change", ["wrong_metric", "wrong_unit", "impossible_ratio", "healthy", "foreign_service", "uncited_signal", "conflict", "rank", "wrong_tool", "wrong_cause_family"])
def test_citation_integrity_and_confidence_do_not_substitute_for_action_signals(change):
    incident = state()
    tool = "rollback_release"
    if change == "wrong_metric":
        incident.evidence[0].query = "request_latency_p95"
    elif change == "wrong_unit":
        incident.evidence[0].attributes["unit"] = "seconds"
    elif change == "impossible_ratio":
        for point in incident.evidence[0].attributes["series"]:
            point["value"] = 2
    elif change == "healthy":
        incident.evidence[0].attributes["series"] = [{"value": 0.2}, {"value": 0.3}]
    elif change == "foreign_service":
        incident.evidence[0].service = "other-service"
    elif change == "uncited_signal":
        unrelated = signal("request_latency_p95", (2800, 3000))
        incident.evidence.append(unrelated)
        incident.hypotheses[0].evidence_ids[0] = unrelated.id
    elif change == "conflict":
        incident.hypotheses[0].conflicting_evidence_ids = [incident.evidence[1].id]
    elif change == "rank":
        incident.hypotheses[0].rank = 2
    elif change == "wrong_cause_family":
        incident.hypotheses[0].cause = "CPU saturation"
    else:
        tool = "restart_deployment"
    assert not assess_action_applicability(incident, tool)["allowed"]


def test_offline_summary_capability_cannot_override_structured_healthy_signals():
    incident = state()
    for point in incident.evidence[0].attributes["series"]:
        point["value"] = 0.1
    assert not assess_action_applicability(incident, "rollback_release", allow_simulated_summaries=True)["allowed"]


@pytest.mark.parametrize("cause,status", [
    ("Processing threads monopolize compute cycles", "unrecognized"),
    ("No pool exhaustion is observed", "unresolved_negation"),
    ("Pool exhaustion plus CPU saturation", "multiple_families_unresolved"),
])
@pytest.mark.parametrize("simulation", [False, True])
def test_unresolved_cause_cannot_authorize_action_even_with_matching_signals(cause, status, simulation):
    incident = state()
    incident.hypotheses[0].cause = cause
    if simulation:
        for item in incident.evidence:
            item.attributes = {}
    result = assess_action_applicability(incident, "rollback_release", allow_simulated_summaries=simulation)
    assert result["allowed"] is False
    assert result["cause_family_check"]["status"] == status
    assert result["semantic_entailment_verified"] is False
    assert "unresolved" in result["reason"]


class Generator:
    def __init__(self, mismatch):
        self.mismatch = mismatch

    async def generate(self, *, evidence, **kwargs):
        metric = next(item for item in evidence if item.service == "payment-service" and item.query ==
                      ("request_latency_p95" if self.mismatch else "db_pool_active_ratio"))
        log = next(item for item in evidence if item.kind.value == "log" and item.service == "payment-service")
        return [Hypothesis(rank=1, cause="Database connection pool exhaustion", confidence=1.0,
                           evidence_ids=[metric.id, log.id])], {"source": "offline-protocol-fixture"}


@pytest.mark.parametrize("changed_cause,reason", [
    ("CPU saturation", "different known failure family"),
    ("Processing threads monopolize compute cycles", "cause family is unresolved"),
    ("No pool exhaustion", "cause family is unresolved"),
    ("Pool exhaustion plus CPU saturation", "cause family is unresolved"),
])
def test_changed_cause_family_is_rechecked_before_intent_and_existing_intent_dispatch(changed_cause, reason):
    async def run():
        tools = DemoTelemetryTools()
        tools.rollback_release = AsyncMock(wraps=tools.rollback_release)
        workflow = LangGraphIncidentWorkflow(tools=tools, checkpointer=InMemorySaver(),
            approval_signer=ApprovalReceiptSigner("cause-change-test-secret"))
        result = await workflow.start(IncidentQuestion(question="Why is payment-service slow?"))
        result.approval_decision = ApprovalDecision.APPROVE
        result.approval_actor = "operator"
        prepared = await workflow._prepare_execution({"incident": {**result.model_dump(mode="json"),
            "_approval_idempotency_key": "cause-change-approval-0001"}})
        result.hypotheses[0].cause = changed_cause
        with pytest.raises(GuardrailViolation, match=reason):
            await workflow._prepare_execution({"incident": {**result.model_dump(mode="json"),
                "_approval_idempotency_key": "cause-change-approval-0001"}})
        prepared["incident"]["hypotheses"][0]["cause"] = changed_cause
        with pytest.raises(GuardrailViolation, match=reason):
            await workflow._execute(prepared)
        tools.rollback_release.assert_awaited_once()
        assert tools.rollback_release.call_args.kwargs["dry_run"] is True
    asyncio.run(run())


@pytest.mark.parametrize("mismatch", [False, True])
def test_compiled_graph_checks_signal_applicability_before_dry_run_and_approval(mismatch):
    async def run():
        tools = DemoTelemetryTools()
        tools.allow_simulated_action_summaries = False
        async def metrics(service, metrics):
            return [signal(metric, (0.98, 1.0) if metric == "db_pool_active_ratio" else (0.1, 0.1), service)
                    for metric in metrics]
        tools.query_metrics = metrics
        tools.search_logs = AsyncMock(return_value=[state().evidence[1]])
        tools.rollback_release = AsyncMock(wraps=tools.rollback_release)
        workflow = LangGraphIncidentWorkflow(tools=tools, checkpointer=InMemorySaver(),
            approval_signer=ApprovalReceiptSigner("applicability-test-secret"), hypothesis_generator=Generator(mismatch))
        result = await workflow.start(IncidentQuestion(question="Why is payment-service slow?"))
        check, = [item.payload for item in result.trace if item.event == "action_signal_applicability_checked"]
        assert check["allowed"] is not mismatch
        assert await workflow.get(result.incident_id) == result
        if mismatch:
            assert result.status.value == "diagnosed" and result.remediation.tool == "none"
            assert result.hypotheses[0].confidence == 1.0
            tools.rollback_release.assert_not_awaited()
            assert not any(item.event == "workflow_interrupted" for item in result.trace)
        else:
            assert result.status.value == "waiting_approval"
            tools.rollback_release.assert_awaited_once()
            assert tools.rollback_release.call_args.kwargs["dry_run"] is True
            # A checkpoint modified after the proposal must be checked again
            # before any durable execution intent or signed receipt is created.
            result.approval_decision = ApprovalDecision.APPROVE
            result.approval_actor = "operator"
            prepared = await workflow._prepare_execution({"incident": {**result.model_dump(mode="json"),
                "_approval_idempotency_key": "applicability-approval-0001"}})
            next(item for item in result.evidence if item.service == "payment-service"
                 and item.query == "db_pool_active_ratio").query = "unrelated_metric"
            with pytest.raises(GuardrailViolation, match="Action applicability"):
                await workflow._prepare_execution({"incident": {**result.model_dump(mode="json"),
                    "_approval_idempotency_key": "applicability-approval-0001"}})
            with pytest.raises(GuardrailViolation):
                validate_action_applicability(result)
            next(item for item in prepared["incident"]["evidence"] if item["service"] == "payment-service"
                 and item["query"] == "db_pool_active_ratio")["query"] = "unrelated_metric"
            with pytest.raises(GuardrailViolation, match="Action applicability"):
                await workflow._execute(prepared)
            tools.rollback_release.assert_awaited_once()  # Only the original dry run.
    asyncio.run(run())
