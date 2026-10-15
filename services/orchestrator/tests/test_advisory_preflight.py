"""Compiled-graph regressions: advice is not a successful tool preflight."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from incident_copilot.approval import ApprovalReceiptSigner
from incident_copilot.incident_profiles import PROFILES
from incident_copilot.langgraph_workflow import LangGraphIncidentWorkflow
from incident_copilot.models import Evidence, IncidentQuestion, IncidentStatus, WorkflowStep
from incident_copilot.tools import DemoTelemetryTools


CASES = [
    ("downstream_timeout", "ledger-service", "request_latency_p95", 3400.0, "downstream request timed out"),
    ("retry_storm", "checkout-api", "retry_rate", 5.0, "request completed"),
    ("cache_penetration", "payment-service", "cache_miss_rate", 9.5, "request completed"),
]


@pytest.mark.parametrize("family,service,metric,value,message", CASES, ids=[case[0] for case in CASES])
def test_observed_advisory_families_retain_diagnosis_without_inventing_preflight(family, service, metric, value, message):
    async def run():
        # Reuse the offline adapter's knowledge/dependency/trace fixtures, but
        # supply timestamped observations so a generic question is classified
        # from signals rather than a cause hint or legacy summary fallback.
        tools = DemoTelemetryTools()
        tools.allow_simulated_signal_summaries = False
        tools.allow_simulated_action_summaries = False
        tools.allow_simulated_deployment_targets = False
        now = datetime.now(UTC)
        metric_evidence = Evidence(
            kind="metric", source="prometheus-contract-fixture", title=metric,
            observation="Current bounded advisory-family signal fixture.",
            observed_at=now, service=service, score=0.95, query=metric,
            attributes={"series": [
                {"timestamp": (now - timedelta(seconds=offset)).isoformat(), "value": value}
                for offset in (10, 5)
            ]},
        )
        log_evidence = Evidence(
            kind="log", source="loki-contract-fixture", title="Current service logs",
            observation=message, observed_at=now, service=service, score=0.94,
            attributes={"events": [{"timestamp": (now - timedelta(seconds=4)).isoformat(), "message": message}]},
        )
        tools.query_metrics = AsyncMock(return_value=[metric_evidence])
        tools.search_logs = AsyncMock(return_value=[log_evidence])
        tools.restart_deployment = AsyncMock(side_effect=AssertionError("Advice must not invoke destructive preflight or execution."))
        tools.rollback_release = AsyncMock(side_effect=AssertionError("Advice must not invoke destructive preflight or execution."))
        workflow = LangGraphIncidentWorkflow(
            tools=tools, checkpointer=InMemorySaver(),
            approval_signer=ApprovalReceiptSigner("advisory-test-only-signing-secret"),
        )
        state = await workflow.start(IncidentQuestion(question=f"Why is {service} suddenly slow?"))

        assert PROFILES[family].action == "none"
        assert state.status == IncidentStatus.DIAGNOSED
        assert state.current_step == WorkflowStep.COMPLETE
        assert state.incident_type == family and state.candidate_incident_types == [family]
        assert state.services[0] == service
        assert state.hypotheses and state.hypotheses[0].cause == PROFILES[family].cause
        assert metric_evidence in state.evidence and log_evidence in state.evidence
        assert state.hypotheses[0].evidence_ids
        assert set(state.hypotheses[0].evidence_ids) <= {item.id for item in state.evidence}
        assert any(item.kind.value == "runbook" for item in state.evidence)
        remediation = state.remediation
        assert remediation is not None and remediation.tool == "none"
        assert remediation.requires_approval is False
        assert remediation.title and remediation.command_preview == "No production mutation; apply runbook guidance."
        assert remediation.expected_result == PROFILES[family].expected_result
        assert remediation.preflight_mode is None
        assert "target_context" not in remediation.arguments
        assert state.approval_decision is None and state.approval_actor is None
        tools.restart_deployment.assert_not_awaited()
        tools.rollback_release.assert_not_awaited()
        assert tools._executions == {}
        assert not any(event.event == "workflow_interrupted" for event in state.trace)
        assert not any(event.step in {WorkflowStep.HUMAN_APPROVAL, WorkflowStep.PREPARE_EXECUTION, WorkflowStep.EXECUTE}
                       for event in state.trace)
        assert not any(event.event in {"tool_call", "tool_result"}
                       and event.payload.get("tool") in {"restart_deployment", "rollback_release", "none"}
                       for event in state.trace)
        assert not any(event.payload.get("dry_run") is True for event in state.trace)
        assert await workflow.get(state.incident_id) == state
        snapshot = await workflow.graph.aget_state({"configurable": {"thread_id": state.incident_id}})
        assert not snapshot.next and not any(task.interrupts for task in snapshot.tasks)
        assert snapshot.values["incident"]["remediation"] == remediation.model_dump(mode="json")

        # This assertion failed before the fix: a made-up {accepted: True}
        # response was being reported as a passed preflight despite zero calls.
        assert remediation.dry_run_passed is False
        assert snapshot.values["incident"]["remediation"]["dry_run_passed"] is False

    asyncio.run(run())
