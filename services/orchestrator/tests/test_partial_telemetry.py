import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from incident_copilot.approval import ApprovalReceiptSigner
from incident_copilot.langgraph_workflow import LangGraphIncidentWorkflow
from incident_copilot.mcp_client import McpToolError
from incident_copilot.models import Evidence, IncidentQuestion
from incident_copilot.tools import DemoTelemetryTools


@pytest.mark.parametrize("failed_tool", ["search_logs", "query_traces"])
@pytest.mark.parametrize("error_type", [McpToolError, TimeoutError])
def test_successful_metrics_survive_other_query_failures(failed_tool, error_type):
    async def scenario():
        tools = DemoTelemetryTools()
        now = datetime.now(UTC)
        metric = Evidence(kind="metric", service="payment-service", source="test",
                          title="pool samples", observation="observed saturation", observed_at=now,
                          score=0.9, query="db_pool_active_ratio",
                          attributes={"series": [{"timestamp": (now - timedelta(seconds=offset)).isoformat(), "value": 1}
                                                 for offset in (30, 15, 0)]})
        tools.query_metrics = AsyncMock(return_value=[metric])
        tools.get_service_dependencies = AsyncMock(return_value=[])
        setattr(tools, failed_tool, AsyncMock(side_effect=error_type("private query detail")))
        workflow = LangGraphIncidentWorkflow(tools=tools, checkpointer=InMemorySaver(),
                                             approval_signer=ApprovalReceiptSigner("test-only-secret"))
        state = await workflow.start(IncidentQuestion(question="Why is payment-service slow today?"))
        assert state.incident_type == "database_connection_pool_exhaustion"
        assert state.status == "waiting_approval"
        assert metric.id in [item.id for item in state.evidence]
        failure = next(event for event in state.trace if event.event == "tool_error")
        assert failure.payload["tool"] == failed_tool
        assert failure.payload["error_type"] == error_type.__name__
        assert not any(event.event == "tool_result" and event.payload.get("tool") == failed_tool for event in state.trace)
        assert "private query detail" not in state.model_dump_json()
        retained = await workflow.get(state.incident_id)
        assert retained.trace == state.trace
        failed_kind = "log" if failed_tool == "search_logs" else "trace"
        assert not any(item.kind.value == failed_kind for item in state.evidence)
    asyncio.run(scenario())


def test_all_queries_unavailable_abstains_durably_without_model_or_execution():
    async def scenario():
        tools = DemoTelemetryTools()
        tools.get_service_dependencies = AsyncMock(return_value=[])
        for name in ("query_metrics", "search_logs", "query_traces"):
            setattr(tools, name, AsyncMock(side_effect=McpToolError("private query detail")))
        tools.rollback_release = AsyncMock()
        tools.restart_deployment = AsyncMock()
        generator = AsyncMock()
        workflow = LangGraphIncidentWorkflow(tools=tools, checkpointer=InMemorySaver(),
                                             approval_signer=ApprovalReceiptSigner("test-only-secret"), hypothesis_generator=generator)
        state = await workflow.start(IncidentQuestion(question="Why is payment-service slow today?"))
        assert state.status == "insufficient_evidence"
        assert state.hypotheses == []
        assert len([event for event in state.trace if event.event == "tool_error"]) == 3
        assert (await workflow.get(state.incident_id)).status == "insufficient_evidence"
        generator.generate.assert_not_awaited()
        tools.rollback_release.assert_not_awaited()
        tools.restart_deployment.assert_not_awaited()
    asyncio.run(scenario())


def test_unexpected_provider_errors_are_not_misreported_as_missing_data():
    async def scenario():
        tools = DemoTelemetryTools()
        tools.query_metrics = AsyncMock(side_effect=TypeError("provider bug"))
        workflow = LangGraphIncidentWorkflow(tools=tools, checkpointer=InMemorySaver(),
                                             approval_signer=ApprovalReceiptSigner("test-only-secret"))
        with pytest.raises(TypeError, match="provider bug"):
            await workflow.start(IncidentQuestion(question="Why is payment-service slow today?"))
    asyncio.run(scenario())
