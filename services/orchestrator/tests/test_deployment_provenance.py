import asyncio
import json
from datetime import UTC, datetime, timedelta
from dataclasses import replace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver

from incident_copilot.approval import ApprovalReceiptSigner
from incident_copilot.langgraph_workflow import LangGraphIncidentWorkflow
from incident_copilot.mcp_client import McpTelemetryTools
from incident_copilot.guardrails import GuardrailViolation
from incident_copilot.models import ApprovalDecision, ApprovalRequest, Evidence, IncidentQuestion, IncidentState, IncidentStatus
from incident_copilot.tools import DemoTelemetryTools
from incident_mcp.server import app as mcp_app, registry


def deployment(**attributes):
    return Evidence(kind="deployment", service="payment-service", source="contract-test",
        title="revision", observation="deployment contract fixture, not cluster acceptance",
        observed_at=datetime.now(UTC), score=0.9,
        attributes={"revision": "142", "previous_revision": "141", **attributes})


@pytest.mark.parametrize("attributes", [
    {}, {"data_origin": "synthetic"}, {"data_origin": "unknown"},
    {"data_origin": "kubernetes_api"}, {"data_origin": "kubernetes_api", "namespace": "other"},
    {"data_origin": True, "namespace": "prod-sg"},
    {"data_origin": "kubernetes_api", "namespace": "prod-sg", "previous_revision": "142"},
    {"data_origin": "kubernetes_api", "namespace": "prod-sg", "previous_revision": "141; unsafe"},
])
def test_non_simulated_provider_refuses_unverified_revision_before_even_dry_run(attributes):
    async def run():
        tools = DemoTelemetryTools()
        tools.allow_simulated_deployment_targets = False  # Contract fixture representing a remote provider.
        tools.get_recent_deployments = AsyncMock(return_value=[deployment(**attributes)])
        tools.rollback_release = AsyncMock()
        workflow = LangGraphIncidentWorkflow(tools=tools, checkpointer=InMemorySaver(),
            approval_signer=ApprovalReceiptSigner("isolated-provenance-test-secret"))
        state = await workflow.start(IncidentQuestion(question="Why is payment-service slow?"))
        assert state.status == IncidentStatus.DIAGNOSED and state.hypotheses
        assert state.remediation.tool == "none" and not state.remediation.requires_approval
        assert any(event.event == "rollback_target_unverified" for event in state.trace)
        assert not any(event.event == "workflow_interrupted" for event in state.trace)
        tools.rollback_release.assert_not_awaited()
        retained = await workflow.get(state.incident_id)
        assert retained == state
        snapshot = await workflow.graph.aget_state(workflow._config(state.incident_id))
        assert not snapshot.next and not any(task.interrupts for task in snapshot.tasks)
    asyncio.run(run())


def test_target_selection_cannot_fall_back_past_newer_unknown_or_equal_time_conflict():
    valid = deployment(data_origin="kubernetes_api", namespace="prod-sg")
    state = IncidentState(question="test", environment="prod-sg", services=["payment-service"], evidence=[valid])
    assert LangGraphIncidentWorkflow._previous_revision(state) == "141"
    unverified = deployment(data_origin="synthetic")
    unverified.observed_at = valid.observed_at + timedelta(seconds=1)
    state.evidence.append(unverified)
    assert LangGraphIncidentWorkflow._previous_revision(state) is None
    unverified.observed_at = valid.observed_at
    assert LangGraphIncidentWorkflow._previous_revision(state) is None


def test_mcp_adapter_preserves_unknown_or_synthetic_origin_not_nested_claim():
    async def run():
        async with httpx.AsyncClient() as client:
            tools = McpTelemetryTools(url="http://unused.test", read_token="read", operator_token="op", client=client)
            for origin in (None, "synthetic"):
                response = {"deployments": [{"revision": "142", "previous_revision": "141",
                    "deployed_at": datetime.now(UTC).isoformat(), "data_origin": "kubernetes_api", "namespace": "prod-sg"}]}
                if origin is not None:
                    response["data_origin"] = origin
                tools._call_tool = AsyncMock(return_value=response)
                evidence, = await tools.get_recent_deployments("payment-service")
                assert evidence.attributes["data_origin"] == (origin or "unknown")
    asyncio.run(run())


def test_actual_mcp_fixture_catalog_is_labeled_and_cannot_create_rollback_proposal(caplog):
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=mcp_app), base_url="http://mcp.test") as client:
            tools = McpTelemetryTools(url="http://mcp.test/mcp", read_token="local-read-token",
                operator_token="local-operator-token", client=client)
            workflow = LangGraphIncidentWorkflow(tools=tools, checkpointer=InMemorySaver(),
                approval_signer=ApprovalReceiptSigner("local-approval-secret-change-me"))
            original_metric = registry.tools["query_metrics"]
            async def current_metric(arguments, principal):
                result = await original_metric.handler(arguments, principal)
                result["series"] = [{**point, "timestamp":
                    (arguments.end - timedelta(seconds=(len(result["series"]) - 1 - index) * 15)).isoformat()}
                    for index, point in enumerate(result["series"])]
                return result
            # Supply current synthetic signals so this test still isolates the
            # deployment-provenance gate, not the earlier no-current-data gate.
            with patch.dict(registry.tools, {"query_metrics": replace(original_metric, handler=current_metric)}), \
                    caplog.at_level("INFO", logger="incident-mcp"):
                state = await workflow.start(IncidentQuestion(question="Why is payment-service slow today?"))
            deployments = [item for item in state.evidence if item.kind.value == "deployment"]
            assert deployments and all(item.attributes["data_origin"] == "synthetic" and "synthetic" in item.source for item in deployments)
            assert state.incident_type == "database_connection_pool_exhaustion"
            assert state.status == IncidentStatus.DIAGNOSED and state.remediation.tool == "none"
            events = [json.loads(record.getMessage()) for record in caplog.records if record.name == "incident-mcp"]
            assert any(event.get("tool") == "get_recent_deployments" for event in events)
            assert not any(event.get("tool") in {"rollback_release", "restart_deployment"} for event in events)
    asyncio.run(run())


def test_restored_legacy_proposal_cannot_prepare_execution_on_remote_provider():
    async def run():
        saver = InMemorySaver()
        signer = ApprovalReceiptSigner("legacy-rollback-test-secret")
        writer = LangGraphIncidentWorkflow(tools=DemoTelemetryTools(), checkpointer=saver, approval_signer=signer)
        old = await writer.start(IncidentQuestion(question="Why is payment-service slow today?"))
        assert old.status == IncidentStatus.WAITING_APPROVAL
        tools = DemoTelemetryTools()
        tools.allow_simulated_deployment_targets = False
        tools.rollback_release = AsyncMock()
        reader = LangGraphIncidentWorkflow(tools=tools, checkpointer=saver, approval_signer=signer)
        with pytest.raises(GuardrailViolation, match="verified deployment evidence"):
            await reader.resume_with_approval(old.incident_id, ApprovalRequest(decision="approve", actor="operator",
                reason="Legacy proposal reviewed", idempotency_key="legacy-approval-key-0001"))
        tools.rollback_release.assert_not_awaited()
        retained = await reader.get(old.incident_id)
        assert retained.approval_decision == "approve"
        assert not any(event.event == "execution_intent_prepared" for event in retained.trace)
    asyncio.run(run())


def test_existing_execution_intent_cannot_dispatch_with_unverified_metadata():
    async def run():
        tools = DemoTelemetryTools()
        saver = InMemorySaver()
        workflow = LangGraphIncidentWorkflow(tools=tools, checkpointer=saver,
            approval_signer=ApprovalReceiptSigner("legacy-execution-test-secret"))
        state = await workflow.start(IncidentQuestion(question="Why is payment-service slow today?"))
        state.approval_decision = ApprovalDecision.APPROVE
        state.approval_actor = "operator"
        raw = {"incident": {**state.model_dump(mode="json"), "_approval_idempotency_key": "legacy-execution-key-0001"}}
        prepared = await workflow._prepare_execution(raw)
        tools.allow_simulated_deployment_targets = False
        tools.rollback_release = AsyncMock()
        with pytest.raises(GuardrailViolation, match="verified deployment evidence"):
            await workflow._execute(prepared)
        tools.rollback_release.assert_not_awaited()
    asyncio.run(run())
