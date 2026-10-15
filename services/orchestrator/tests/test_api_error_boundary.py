from unittest.mock import AsyncMock
import os

import pytest
from fastapi.testclient import TestClient
from psycopg import OperationalError

from incident_copilot.api import app
from incident_copilot.guardrails import GuardrailViolation
from incident_copilot.mcp_client import McpToolError
from incident_copilot.models import IncidentState
from incident_copilot.reasoning import ModelGenerationError


@pytest.fixture
def client(monkeypatch):
    for name in ("OPENAI_API_KEY", "OPENAI_MODEL", "DATABASE_URL", "REDIS_URL", "MCP_SERVER_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ORCHESTRATOR_AUTH_MODE", "disabled")
    with TestClient(app) as value:
        yield value


@pytest.mark.parametrize("error,expected", [
    (GuardrailViolation("private-input-policy-diagnostic"), 400),
    (McpToolError("private-mcp-response-with-credential"), 502),
    (ModelGenerationError("private-model-provider-response"), 502),
])
def test_diagnosis_error_does_not_echo_provider_details(client, error, expected):
    app.state.workflow.start = AsyncMock(side_effect=error)
    app.state.repository.save = AsyncMock()
    response = client.post("/v1/incidents", json={"question": "Investigate payment-service"})
    assert response.status_code == expected
    assert "private" not in response.text
    app.state.repository.save.assert_not_awaited()


@pytest.mark.parametrize("error,expected", [
    (GuardrailViolation("private-recovery-policy"), 409),
    (ValueError("private-malformed-tool-response"), 409),
    (McpToolError("private-response-lost-after-dispatch"), 502),
    (TimeoutError("private-timeout"), 502),
    (OperationalError("private-checkpoint-database-error"), 503),
])
def test_post_claim_recovery_failure_retains_lock_even_with_stale_checkpoint(client, error, expected):
    incident = client.post("/v1/incidents", json={"question": "Why is payment-service slow today?"}).json()
    # Deliberately keep showing the same stale pre-approval checkpoint.
    app.state.workflow.get = AsyncMock(return_value=IncidentState.model_validate(incident))
    app.state.workflow.resume_with_approval = AsyncMock(side_effect=error)
    app.state.repository.release_idempotency_key = AsyncMock()
    body = {"decision": "approve", "actor": "test-operator", "reason": "Reviewed evidence",
            "idempotency_key": f"{incident['incident_id']}-original-key"}
    path = f"/v1/incidents/{incident['incident_id']}/approval"
    response = client.post(path, json=body)
    assert response.status_code == expected
    assert "private" not in response.text
    assert client.post(path, json=body).status_code == 409
    assert client.post(path, json={**body, "idempotency_key": f"{incident['incident_id']}-another-key"}).status_code == 409
    app.state.workflow.resume_with_approval.assert_awaited_once()
    app.state.repository.release_idempotency_key.assert_not_awaited()


def test_repository_failure_after_resume_also_preserves_claim(client):
    incident = client.post("/v1/incidents", json={"question": "Why is payment-service slow today?"}).json()
    paused = IncidentState.model_validate(incident)
    app.state.workflow.get = AsyncMock(return_value=paused)
    app.state.workflow.resume_with_approval = AsyncMock(return_value=paused.model_copy(update={"status": "resolved"}))
    app.state.repository.save = AsyncMock(side_effect=OperationalError("private-write-error-after-resume"))
    body = {"decision": "approve", "actor": "test-operator", "reason": "Reviewed evidence",
            "idempotency_key": f"{incident['incident_id']}-post-resume-key"}
    path = f"/v1/incidents/{incident['incident_id']}/approval"
    response = client.post(path, json=body)
    assert response.status_code == 503
    assert "private" not in response.text
    assert client.post(path, json={**body, "idempotency_key": f"{incident['incident_id']}-new-key"}).status_code == 409
    app.state.workflow.resume_with_approval.assert_awaited_once()


@pytest.mark.skipif(not os.getenv("INCIDENT_TEST_DATABASE_URL"), reason="isolated PostgreSQL test URL required")
def test_failed_approval_claim_stays_locked_after_real_api_restart(monkeypatch):
    database_url = os.environ["INCIDENT_TEST_DATABASE_URL"]
    for name in ("OPENAI_API_KEY", "OPENAI_MODEL", "REDIS_URL", "MCP_SERVER_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("ORCHESTRATOR_AUTH_MODE", "disabled")
    with TestClient(app) as first:
        incident = first.post("/v1/incidents", json={"question": "Why is payment-service slow today?"}).json()
        app.state.workflow.resume_with_approval = AsyncMock(side_effect=ValueError("private-uncertain-recovery"))
        body = {"decision": "approve", "actor": "test-operator", "reason": "Reviewed evidence",
                "idempotency_key": f"{incident['incident_id']}-durable-error-key"}
        path = f"/v1/incidents/{incident['incident_id']}/approval"
        assert first.post(path, json=body).status_code == 409
    # Reconstruct both the graph/checkpointer and repository connections.
    with TestClient(app) as fresh:
        app.state.workflow.resume_with_approval = AsyncMock()
        assert fresh.get(f"/v1/incidents/{incident['incident_id']}").json()["status"] == "waiting_approval"
        assert fresh.post(path, json=body).status_code == 409
        assert fresh.post(path, json={**body, "idempotency_key": f"{incident['incident_id']}-fresh-key"}).status_code == 409
        app.state.workflow.resume_with_approval.assert_not_awaited()
