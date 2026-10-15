import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from incident_copilot.approval import ApprovalReceiptSigner
from incident_copilot.langgraph_workflow import LangGraphIncidentWorkflow
from incident_copilot.models import Evidence, IncidentQuestion
from incident_copilot.tools import DemoTelemetryTools


@pytest.mark.parametrize("mode", ["old", "future", "untimed", "foreign", "conflicting", "gapped"])
@pytest.mark.parametrize("summary_capability", [False, True])
def test_unavailable_current_primary_signals_abstain_before_model_and_preserve_history(mode, summary_capability):
    async def run():
        tools = DemoTelemetryTools()
        tools.allow_simulated_signal_summaries = summary_capability
        now = datetime.now(UTC)
        offsets = {"old": (600, 585), "future": (-30, -15), "untimed": (30, 15),
                   "foreign": (30, 15), "conflicting": (15, 15), "gapped": (250, 15)}[mode]
        points = [{"timestamp": (now - timedelta(seconds=offset)).isoformat(),
                   "value": 0.95 if mode == "conflicting" and index == 0 else 1}
                  for index, offset in enumerate(offsets)]
        if mode == "untimed":
            points = [{"value": 1}, {"value": 1}]
        item = Evidence(kind="metric", service="foreign-service" if mode == "foreign" else "payment-service",
            source="prometheus", title="Pool sample", observation="Pool exhausted",
            observed_at=now, query="db_pool_active_ratio", score=0.99,
            attributes={"series": points, "anomalous": True,
                        "allow_simulated_signal_summaries": True})
        tools.query_metrics = AsyncMock(return_value=[item])
        tools.search_logs = AsyncMock(return_value=[])
        tools.get_service_dependencies = AsyncMock(return_value=[])
        tools.rollback_release, tools.restart_deployment = AsyncMock(), AsyncMock()
        generator = AsyncMock()
        generator.generate.return_value = ([], {"source": "offline-abstention-fixture"})
        workflow = LangGraphIncidentWorkflow(tools=tools, checkpointer=InMemorySaver(),
            approval_signer=ApprovalReceiptSigner("time-test-only-secret"), hypothesis_generator=generator)
        state = await workflow.start(IncidentQuestion(question="Why is payment-service slow today?"))
        # A single eligible point is not sustained evidence, but may still be
        # offered to the model for investigation rather than treated as no data.
        if mode == "gapped":
            assert state.incident_type is None
            generator.generate.assert_awaited_once()
            assert state.status.value == "insufficient_evidence" and state.remediation.tool == "none"
            tools.rollback_release.assert_not_awaited()
            return
        assert state.status.value == "insufficient_evidence"
        assert state.incident_type is None and state.hypotheses == []
        assert state.remediation.tool == "none"
        generator.generate.assert_not_awaited()
        tools.rollback_release.assert_not_awaited()
        tools.restart_deployment.assert_not_awaited()
        assert next(e for e in state.evidence if e.id == item.id) == item
        if mode == "foreign":
            assert item.id in state.diagnostic_excluded_evidence_ids
        check = next(e.payload for e in state.trace if e.event == "diagnostic_signal_time_checked")
        assert check["eligible_evidence_ids"] == []
        assert check["simulation_only_summary_compatibility"] is False
        assert await workflow.get(state.incident_id) == state
    asyncio.run(run())


@pytest.mark.parametrize("capability", [False, True])
def test_legacy_summary_requires_explicit_local_provider_capability(capability):
    async def run():
        tools = DemoTelemetryTools()
        tools.allow_simulated_signal_summaries = capability
        generator = AsyncMock()
        workflow = LangGraphIncidentWorkflow(tools=tools, checkpointer=InMemorySaver(),
            approval_signer=ApprovalReceiptSigner("summary-test-only-secret"))
        if not capability:
            workflow.hypothesis_generator = generator
        state = await workflow.start(IncidentQuestion(question="Why is payment-service slow?"))
        check = next(e.payload for e in state.trace if e.event == "diagnostic_signal_time_checked")
        assert check["simulation_only_summary_compatibility"] is capability
        assert state.status.value == ("waiting_approval" if capability else "insufficient_evidence")
        if not capability:
            generator.generate.assert_not_awaited()
    asyncio.run(run())
