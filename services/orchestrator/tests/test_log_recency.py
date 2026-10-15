import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver

from incident_copilot.approval import ApprovalReceiptSigner
from incident_copilot.incident_profiles import infer_profile_from_evidence
from incident_copilot.langgraph_workflow import LangGraphIncidentWorkflow
from incident_copilot.mcp_client import McpTelemetryTools, McpToolError
from incident_copilot.models import Evidence, IncidentQuestion
from incident_copilot.tools import DemoTelemetryTools


def logs(message, timestamp):
    event = {"message": message}
    if timestamp is not None:
        event["timestamp"] = timestamp
    return Evidence(kind="log", source="snapshot", title="retained logs", observation=message,
                    service="payment-service", observed_at=datetime.now(UTC), score=0.9,
                    attributes={"events": [event]})


@pytest.mark.parametrize("message", ["invalid ledger response shape", "downstream request timed out", "acquisition timed out"])
@pytest.mark.parametrize("age", ["old", "future", "missing", "naive", "malformed"])
def test_stale_or_unverifiable_signature_cannot_classify_current_incident(message, age):
    now = datetime.now(UTC)
    timestamp = {"old": (now - timedelta(minutes=20)).isoformat(),
                 "future": (now + timedelta(minutes=1)).isoformat(), "missing": None,
                 "naive": now.replace(tzinfo=None).isoformat(), "malformed": "broken"}[age]
    assert infer_profile_from_evidence([logs(message, timestamp)], None) is None


def test_current_log_signature_still_classifies():
    evidence = logs("invalid ledger response shape", datetime.now(UTC).isoformat())
    assert infer_profile_from_evidence([evidence], None).key == "deployment_regression"


def test_mcp_log_boundary_deduplicates_sorts_and_does_not_trust_match_count():
    async def scenario():
        now = datetime.now(UTC)
        first = (now - timedelta(seconds=20)).isoformat()
        last = (now - timedelta(seconds=10)).isoformat()
        event = {"timestamp": first, "message": "valid", "active": "broken", "max": None}
        raw = [event, {"timestamp": last, "message": "valid later", "active": 0, "max": 0}, event,
               {"message": "missing time"}, {"timestamp": "broken", "message": "bad"},
               {"timestamp": (now + timedelta(minutes=1)).isoformat(), "message": "future"},
               {"timestamp": (now - timedelta(minutes=40)).isoformat(), "message": "old"},
               {"timestamp": now.replace(tzinfo=None).isoformat(), "message": "naive"}, None]
        async with httpx.AsyncClient() as client:
            tools = McpTelemetryTools(url="http://mcp.test/mcp", read_token="read", operator_token="operator", client=client)
            tools._call_tool = AsyncMock(return_value={"events": raw, "matches": 999})
            item, = await tools.search_logs("payment-service", "")
        assert len(item.attributes["events"]) == 2
        assert item.attributes["discarded_events"] == 7
        assert item.observed_at == datetime.fromisoformat(last)
        assert "999" not in item.observation and "saturated" not in item.observation
    asyncio.run(scenario())


def test_malformed_log_container_fails_at_transport_boundary():
    async def scenario():
        async with httpx.AsyncClient() as client:
            tools = McpTelemetryTools(url="http://mcp.test/mcp", read_token="read", operator_token="operator", client=client)
            tools._call_tool = AsyncMock(return_value={"events": {"message": "not an event list"}})
            with pytest.raises(McpToolError):
                await tools.search_logs("payment-service", "")
    asyncio.run(scenario())


def test_old_deployment_logs_are_retained_as_context_without_a_rollback_proposal():
    async def scenario():
        tools = DemoTelemetryTools()
        tools.query_metrics = AsyncMock(return_value=[])
        evidence = logs("invalid ledger response shape", (datetime.now(UTC) - timedelta(minutes=20)).isoformat())
        tools.search_logs = AsyncMock(return_value=[evidence])
        tools.rollback_release = AsyncMock()
        workflow = LangGraphIncidentWorkflow(tools=tools, checkpointer=InMemorySaver(),
                                             approval_signer=ApprovalReceiptSigner("test-only-approval-secret"))
        state = await workflow.start(IncidentQuestion(question="Investigate payment-service deployment regression"))
        assert state.status == "insufficient_evidence"
        assert state.hypotheses == []
        assert state.remediation.tool == "none"
        assert evidence.id in {item.id for item in state.evidence}
        tools.rollback_release.assert_not_awaited()
    asyncio.run(scenario())
