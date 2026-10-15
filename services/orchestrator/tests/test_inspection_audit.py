import asyncio
import json
import os
from contextlib import contextmanager
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from psycopg import OperationalError

from incident_copilot.api import app
from incident_copilot.mcp_client import McpToolError
from incident_copilot.store import PostgresIncidentRepository


@contextmanager
def pending_client(monkeypatch, database_url=None):
    for name in ("OPENAI_API_KEY", "OPENAI_MODEL", "DATABASE_URL", "REDIS_URL", "MCP_SERVER_URL"):
        monkeypatch.delenv(name, raising=False)
    if database_url:
        monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("ORCHESTRATOR_AUTH_MODE", "disabled")
    with TestClient(app) as client:
        created = client.post("/v1/incidents", json={"question": "Why is payment-service slow today?"}).json()
        tools = app.state.workflow.tools
        tools.rollback_release = AsyncMock(side_effect=McpToolError("Synthetic lost response"))
        tools.restart_deployment = AsyncMock()
        pending = client.post(f"/v1/incidents/{created['incident_id']}/approval", json={
            "decision": "approve", "actor": "approved-test-operator", "reason": "reviewed",
            "idempotency_key": f"{created['incident_id']}-audit-operation",
        }).json()
        assert pending["status"] == "remediation_pending"
        tools.rollback_release.reset_mock()
        yield client, pending, tools


def marker():
    return {"status": "marker_matches", "source": "kubernetes_deployment_marker", "read_only": True,
            "rollout_health_verified": False, "note": "private-provider-detail", "approval_receipt": "private-receipt"}


def test_inspection_records_start_before_query_and_completed_result_separately(monkeypatch):
    with pending_client(monkeypatch) as (client, incident, tools):
        async def inspect(**kwargs):
            records = await app.state.repository.inspection_history(incident["incident_id"])
            assert len(records) == 1
            assert records[0]["outcome"] is None
            assert records[0]["request"]["idempotency_key"] == kwargs["idempotency_key"]
            return marker()
        tools.query_operation_status = AsyncMock(side_effect=inspect)
        result = client.get(f"/v1/incidents/{incident['incident_id']}/operation-status")
        assert result.status_code == 200
        assert result.headers["cache-control"] == "no-store"
        records = client.get(f"/v1/incidents/{incident['incident_id']}/operation-inspections").json()
        assert records[0]["audit_id"] == result.json()["inspection_audit_id"]
        assert records[0]["request"]["request_principal"] == "unauthenticated-demo"
        assert records[0]["request"]["approval_actor"] == "approved-test-operator"
        assert records[0]["outcome"]["status"] == "completed"
        assert "private" not in json.dumps(records)
        assert client.get(f"/v1/incidents/{incident['incident_id']}").json() == incident
        tools.rollback_release.assert_not_awaited()
        tools.restart_deployment.assert_not_awaited()


def test_query_failure_is_durably_redacted_in_separate_audit(monkeypatch):
    with pending_client(monkeypatch) as (client, incident, tools):
        tools.query_operation_status = AsyncMock(side_effect=McpToolError("private-provider-token"))
        response = client.get(f"/v1/incidents/{incident['incident_id']}/operation-status")
        assert response.status_code == 502
        records = client.get(f"/v1/incidents/{incident['incident_id']}/operation-inspections").json()
        assert records[0]["outcome"]["status"] == "unavailable"
        assert records[0]["outcome"]["error_type"] == "McpToolError"
        assert "private" not in json.dumps(records)


@pytest.mark.parametrize("stage", ["begin", "finish"])
def test_audit_write_failure_is_explicit_without_execution_retry(monkeypatch, stage):
    with pending_client(monkeypatch) as (client, incident, tools):
        tools.query_operation_status = AsyncMock(return_value=marker())
        setattr(app.state.repository, f"{stage}_inspection", AsyncMock(side_effect=OperationalError("private-database-detail")))
        response = client.get(f"/v1/incidents/{incident['incident_id']}/operation-status")
        assert response.status_code == 503
        assert "private" not in response.text
        if stage == "begin":
            tools.query_operation_status.assert_not_awaited()
        else:
            tools.query_operation_status.assert_awaited_once()
            rows = client.get(f"/v1/incidents/{incident['incident_id']}/operation-inspections").json()
            assert rows[0]["outcome"] is None
        assert client.get(f"/v1/incidents/{incident['incident_id']}").json() == incident
        tools.rollback_release.assert_not_awaited()


def test_ineligible_query_has_no_audit_and_history_is_bounded(monkeypatch):
    with pending_client(monkeypatch) as (client, incident, tools):
        response = client.get("/v1/incidents/unknown/operation-inspections")
        assert response.status_code == 404
        assert client.get(f"/v1/incidents/{incident['incident_id']}/operation-status?service=override").status_code == 400
        assert client.get(f"/v1/incidents/{incident['incident_id']}/operation-inspections").json() == []
        async def populate():
            repository = app.state.repository
            for index in range(105):
                await repository.begin_inspection(f"bounded-{index}", {"incident_id": incident["incident_id"]})
            rows = await repository.inspection_history(incident["incident_id"])
            assert len(rows) == 100
            assert rows[0]["audit_id"] == "bounded-104"
            rows[0]["request"]["incident_id"] = "changed-copy"
            assert (await repository.inspection_history(incident["incident_id"]))[0]["incident_id"] == incident["incident_id"]
            await repository.finish_inspection("bounded-104", {"status": "completed"})
            with pytest.raises(ValueError):
                await repository.finish_inspection("bounded-104", {"status": "changed"})
        client.portal.call(populate)


@pytest.mark.skipif(not os.getenv("INCIDENT_TEST_DATABASE_URL"), reason="isolated PostgreSQL test URL required")
def test_postgres_inspection_audit_survives_fresh_connections(monkeypatch):
    url = os.environ["INCIDENT_TEST_DATABASE_URL"]
    with pending_client(monkeypatch, url) as (client, incident, tools):
        path = f"/v1/incidents/{incident['incident_id']}/operation-status"
        tools.query_operation_status = AsyncMock(return_value=marker())
        success = client.get(path)
        assert success.status_code == 200
        tools.query_operation_status = AsyncMock(side_effect=McpToolError("private-error"))
        assert client.get(path).status_code == 502
        tools.query_operation_status = AsyncMock(return_value=marker())
        app.state.repository.finish_inspection = AsyncMock(side_effect=OperationalError("private-database-error"))
        assert client.get(path).status_code == 503

    async def recover():
        repository = await PostgresIncidentRepository.connect(url)
        try:
            records = await repository.inspection_history(incident["incident_id"])
            assert len(records) == 3
            assert records[0]["outcome"] is None
            assert records[1]["outcome"]["status"] == "unavailable"
            assert records[2]["outcome"]["status"] == "completed"
            assert records[2]["audit_id"] == success.json()["inspection_audit_id"]
            assert records[2]["request"]["arguments"] == incident["remediation"]["arguments"]
            assert "private" not in json.dumps(records)
            assert (await repository.get(incident["incident_id"])).model_dump(mode="json") == incident
            with pytest.raises(ValueError):
                await repository.finish_inspection(records[2]["audit_id"], {"status": "tampered"})
        finally:
            await repository.close()
    asyncio.run(recover())
