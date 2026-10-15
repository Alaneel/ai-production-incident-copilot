from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from incident_copilot.api import app
from incident_copilot.api_auth import ApiAuthorization

READ = "test-reader-" + "r" * 32
OPERATOR = "test-operator-" + "o" * 32


@pytest.fixture
def authenticated(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_AUTH_MODE", "required")
    monkeypatch.setenv("ORCHESTRATOR_READ_TOKEN", READ)
    monkeypatch.setenv("ORCHESTRATOR_OPERATOR_TOKEN", OPERATOR)
    monkeypatch.setenv("ORCHESTRATOR_OPERATOR_ACTOR", "bound-test-operator")
    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize("method,path", [
    ("POST", "/v1/incidents"), ("GET", "/v1/incidents/unknown"),
    ("GET", "/v1/incidents/unknown/history"), ("GET", "/v1/incidents/unknown/events"),
    ("POST", "/v1/incidents/unknown/approval"),
    ("GET", "/v1/incidents/unknown/operation-status"),
    ("GET", "/v1/incidents/unknown/operation-inspections"),
    ("POST", "/v1/incidents/unknown/recovery-check"),
])
def test_incident_routes_deny_unauthenticated_requests_before_workflow(authenticated, method, path):
    app.state.workflow.start = AsyncMock()
    app.state.workflow.get = AsyncMock()
    response = authenticated.request(method, path, json={})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    app.state.workflow.start.assert_not_awaited()
    app.state.workflow.get.assert_not_awaited()


def test_read_credential_diagnoses_and_reads_but_cannot_approve(authenticated):
    headers = {"Authorization": f"Bearer {READ}"}
    created = authenticated.post("/v1/incidents", headers=headers, json={"question": "Why is payment-service slow today?"})
    assert created.status_code == 201
    incident_id = created.json()["incident_id"]
    assert authenticated.get(f"/v1/incidents/{incident_id}", headers=headers).status_code == 200
    assert authenticated.get(f"/v1/incidents/{incident_id}/history", headers=headers).status_code == 200
    assert authenticated.get(f"/v1/incidents/{incident_id}/events", headers=headers).status_code == 200
    assert authenticated.get(f"/v1/incidents/{incident_id}/operation-inspections", headers=headers).json() == []
    app.state.repository.claim_approval = AsyncMock()
    denied = authenticated.post(f"/v1/incidents/{incident_id}/approval", headers=headers, json={})
    assert denied.status_code == 403
    app.state.repository.claim_approval.assert_not_awaited()
    app.state.workflow.inspect_operation = AsyncMock()
    assert authenticated.get(f"/v1/incidents/{incident_id}/operation-status", headers=headers).status_code == 403
    app.state.workflow.inspect_operation.assert_not_awaited()


def test_operator_cannot_spoof_actor_and_bound_rejection_is_audited(authenticated):
    headers = {"Authorization": f"Bearer {OPERATOR}"}
    created = authenticated.post("/v1/incidents", headers=headers, json={"question": "Why is payment-service slow today?"}).json()
    incident_id = created["incident_id"]
    tools = app.state.workflow.tools
    tools.rollback_release = AsyncMock()
    body = {"decision": "reject", "actor": "spoofed-operator", "reason": "test verification", "idempotency_key": "auth-bound-rejection"}
    assert authenticated.post(f"/v1/incidents/{incident_id}/approval", headers=headers, json=body).status_code == 403
    assert authenticated.get(f"/v1/incidents/{incident_id}", headers=headers).json()["status"] == "waiting_approval"
    body["actor"] = "bound-test-operator"
    accepted = authenticated.post(f"/v1/incidents/{incident_id}/approval", headers=headers, json=body)
    assert accepted.status_code == 200
    assert accepted.json()["approval_actor"] == "bound-test-operator"
    assert accepted.json()["status"] == "rejected"
    assert READ not in accepted.text and OPERATOR not in accepted.text
    tools.rollback_release.assert_not_awaited()


def test_operator_can_inspect_original_pending_operation(authenticated):
    from incident_copilot.mcp_client import McpToolError

    headers = {"Authorization": f"Bearer {OPERATOR}"}
    created = authenticated.post("/v1/incidents", headers=headers,
                                 json={"question": "Why is payment-service slow today?"}).json()
    tools = app.state.workflow.tools
    tools.rollback_release = AsyncMock(side_effect=McpToolError("lost response"))
    pending = authenticated.post(f"/v1/incidents/{created['incident_id']}/approval", headers=headers, json={
        "decision": "approve", "actor": "bound-test-operator", "reason": "reviewed",
        "idempotency_key": "authenticated-inspection-key",
    })
    assert pending.json()["status"] == "remediation_pending"
    tools.query_operation_status = AsyncMock(return_value={
        "status": "unknown", "source": "process_cache", "read_only": True,
        "rollout_health_verified": False,
    })
    inspected = authenticated.get(f"/v1/incidents/{created['incident_id']}/operation-status", headers=headers)
    assert inspected.status_code == 200
    assert inspected.json()["incident_status"] == "remediation_pending"
    assert tools.query_operation_status.call_args.kwargs["approval_actor"] == "bound-test-operator"
    records = authenticated.get(f"/v1/incidents/{created['incident_id']}/operation-inspections",
                                headers={"Authorization": f"Bearer {READ}"}).json()
    assert records[0]["request"]["request_principal"] == "bound-test-operator"
    assert records[0]["request"]["auth_mode"] == "required"


@pytest.mark.parametrize("credential", ["Bearer wrong", "Basic abc", "Bearer é", "Bearer " + "x" * 4097])
def test_invalid_credentials_do_not_leak_configured_secrets(authenticated, credential):
    response = authenticated.get("/v1/incidents/unknown", headers={"Authorization": credential.encode("utf-8")})
    assert response.status_code == 401
    assert READ not in response.text and OPERATOR not in response.text


def test_health_exposes_auth_mode_not_credentials(authenticated):
    response = authenticated.get("/health")
    assert response.json()["api_auth"] == "required"
    assert READ not in response.text and OPERATOR not in response.text


def test_cors_preflight_and_unauthorized_response_remain_browser_readable(authenticated):
    headers = {"Origin": "http://localhost:3000"}
    denied = authenticated.get("/v1/incidents/unknown", headers=headers)
    assert denied.status_code == 401
    assert denied.headers["access-control-allow-origin"] == headers["Origin"]
    preflight = authenticated.options("/v1/incidents", headers={**headers,
        "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "Authorization,Content-Type"})
    assert preflight.status_code == 200


def test_required_mode_fails_closed_on_incomplete_configuration():
    with pytest.raises(ValueError, match="distinct"):
        ApiAuthorization.from_environment({"ORCHESTRATOR_AUTH_MODE": "required"})
    with pytest.raises(ValueError, match="distinct"):
        ApiAuthorization.from_environment({"ORCHESTRATOR_AUTH_MODE": "required", "ORCHESTRATOR_READ_TOKEN": READ, "ORCHESTRATOR_OPERATOR_TOKEN": READ})
    with pytest.raises(ValueError, match="actor"):
        ApiAuthorization.from_environment({"ORCHESTRATOR_AUTH_MODE": "required", "ORCHESTRATOR_READ_TOKEN": READ, "ORCHESTRATOR_OPERATOR_TOKEN": OPERATOR})
    with pytest.raises(ValueError, match="AUTH_MODE"):
        ApiAuthorization.from_environment({"ORCHESTRATOR_AUTH_MODE": "unknown"})
