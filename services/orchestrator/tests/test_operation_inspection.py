from unittest.mock import AsyncMock
import asyncio

import pytest
from fastapi.testclient import TestClient

from incident_copilot.api import app
from incident_copilot.mcp_client import McpTelemetryTools, McpToolError
from incident_copilot.tools import DemoTelemetryTools


@pytest.fixture
def pending(monkeypatch):
    for key in ("OPENAI_API_KEY", "OPENAI_MODEL", "DATABASE_URL", "REDIS_URL", "MCP_SERVER_URL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ORCHESTRATOR_AUTH_MODE", "disabled")
    with TestClient(app) as client:
        incident = client.post("/v1/incidents", json={"question": "Why is payment-service slow today?"}).json()
        tools = app.state.workflow.tools
        tools.rollback_release = AsyncMock(side_effect=McpToolError("private lost-response detail"))
        tools.restart_deployment = AsyncMock()
        response = client.post(f"/v1/incidents/{incident['incident_id']}/approval", json={
            "decision": "approve", "actor": "test-operator", "reason": "reviewed",
            "idempotency_key": "inspection-original-key",
        })
        assert response.status_code == 200
        assert response.json()["status"] == "remediation_pending"
        tools.rollback_release.reset_mock()
        yield client, response.json(), tools


@pytest.mark.parametrize("status", ["marker_matches", "not_observed", "unverified_marker", "conflicting_marker"])
def test_inspection_is_bound_and_does_not_resume_or_unlock(pending, status):
    client, incident, tools = pending
    workflow = app.state.workflow
    tools.query_operation_status = AsyncMock(return_value={
        "status": status, "source": "kubernetes_deployment_marker", "read_only": True,
        "rollout_health_verified": False, "note": "private-provider-detail",
        "approval_receipt": "private-signed-receipt", "executed": True,
    })
    workflow.resume_with_approval = AsyncMock()
    before_history = client.get(f"/v1/incidents/{incident['incident_id']}/history").json()
    response = client.get(f"/v1/incidents/{incident['incident_id']}/operation-status")
    assert response.status_code == 200
    assert response.json()["observation"]["status"] == status
    assert response.json()["incident_status"] == "remediation_pending"
    assert "private" not in response.text
    assert "executed" not in response.json()["observation"]
    tools.query_operation_status.assert_awaited_once_with(
        service=incident["remediation"]["arguments"]["service"],
        namespace=incident["remediation"]["arguments"]["namespace"],
        revision=incident["remediation"]["arguments"]["revision"],
        tool="rollback_release", approval_actor="test-operator",
        incident_id=incident["incident_id"], idempotency_key="inspection-original-key",
    )
    assert client.get(f"/v1/incidents/{incident['incident_id']}").json() == incident
    assert client.get(f"/v1/incidents/{incident['incident_id']}/history").json() == before_history
    assert client.portal.call(app.state.repository.get, incident["incident_id"]).model_dump(mode="json") == incident
    denied = client.post(f"/v1/incidents/{incident['incident_id']}/approval", json={
        "decision": "approve", "actor": "test-operator", "reason": "retry",
        "idempotency_key": "another-inspection-key",
    })
    assert denied.status_code == 409
    workflow.resume_with_approval.assert_not_awaited()
    tools.rollback_release.assert_not_awaited()
    tools.restart_deployment.assert_not_awaited()


@pytest.mark.parametrize("result", [
    {"status": "resolved", "source": "kubernetes_deployment_marker", "read_only": True, "rollout_health_verified": False},
    {"status": "marker_matches", "source": "fixture", "read_only": True, "rollout_health_verified": False},
    {"status": "unknown", "source": "process_cache", "read_only": 1, "rollout_health_verified": False},
    McpToolError("private-token-should-not-leak"), TimeoutError("private-timeout"),
])
def test_invalid_or_unavailable_provider_never_changes_state(pending, result):
    client, incident, tools = pending
    tools.query_operation_status = AsyncMock(**({"side_effect": result} if isinstance(result, Exception) else {"return_value": result}))
    response = client.get(f"/v1/incidents/{incident['incident_id']}/operation-status")
    assert response.status_code == 502
    assert "private" not in response.text
    assert client.get(f"/v1/incidents/{incident['incident_id']}").json() == incident
    tools.rollback_release.assert_not_awaited()


def test_target_overrides_and_unapproved_state_are_denied(pending):
    client, incident, tools = pending
    tools.query_operation_status = AsyncMock()
    path = f"/v1/incidents/{incident['incident_id']}/operation-status"
    assert client.get(path + "?service=another-service").status_code == 400
    tools.rollback_release = AsyncMock(wraps=DemoTelemetryTools().rollback_release)
    created = client.post("/v1/incidents", json={"question": "Why is payment-service slow today?"}).json()
    assert created["status"] == "waiting_approval"
    assert client.get(f"/v1/incidents/{created['incident_id']}/operation-status").status_code == 409
    assert client.get("/v1/incidents/unknown/operation-status").status_code == 404
    tools.query_operation_status.assert_not_awaited()


def test_mcp_inspection_uses_operator_without_approval_receipt():
    async def scenario():
        tools = McpTelemetryTools(url="http://localhost.invalid/mcp", read_token="reader", operator_token="operator")
        tools._call_tool = AsyncMock(return_value={"status": "unknown"})
        arguments = dict(service="payment-service", namespace="prod-sg", tool="restart_deployment",
                         revision=None, idempotency_key="inspection-key", approval_actor="operator",
                         incident_id="inc-test")
        try:
            await tools.query_operation_status(**arguments)
            tools._call_tool.assert_awaited_once_with("query_operation_status", arguments, operator=True)
        finally:
            await tools.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("field", ["actor", "tool", "arguments", "idempotency_key", "missing_intent"])
def test_changed_or_legacy_intent_cannot_query_provider(pending, field):
    from incident_copilot.models import IncidentState

    client, incident, tools = pending
    state = IncidentState.model_validate(incident)
    if field == "missing_intent":
        state.trace = [e for e in state.trace if e.event != "execution_intent_prepared"]
    else:
        intent = next(e for e in state.trace if e.event == "execution_intent_prepared")
        intent.payload[field] = {} if field == "arguments" else "mismatched-record"
    app.state.workflow.get = AsyncMock(return_value=state)
    tools.query_operation_status = AsyncMock()
    response = client.get(f"/v1/incidents/{incident['incident_id']}/operation-status")
    assert response.status_code == 409
    tools.query_operation_status.assert_not_awaited()


def test_fixture_status_does_not_claim_execution(pending):
    client, incident, tools = pending
    response = client.get(f"/v1/incidents/{incident['incident_id']}/operation-status")
    assert response.status_code == 200
    assert response.json()["observation"]["status"] == "unknown"
    assert response.json()["observation"]["source"] == "fixture"
    tools.rollback_release.assert_not_awaited()
