from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from psycopg import OperationalError

from incident_copilot.api import app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_AUTH_MODE", "disabled")
    with TestClient(app) as client:
        app.state.workflow.recheck_recovery = AsyncMock()
        app.state.repository.save = AsyncMock()
        yield client


def test_busy_repository_lock_returns_conflict_without_resuming(client):
    lock = app.state.repository.recovery_observation_lock("INC-busy-recovery")
    assert client.portal.call(lock.__aenter__) is True
    try:
        result = client.post("/v1/incidents/INC-busy-recovery/recovery-check")
        assert result.status_code == 409
        assert result.headers["cache-control"] == "no-store"
        app.state.workflow.recheck_recovery.assert_not_awaited()
        app.state.repository.save.assert_not_awaited()
    finally:
        client.portal.call(lock.__aexit__, None, None, None)


def test_capacity_bulkhead_leaves_pool_headroom_without_resuming(client):
    slots = app.state.recovery_check_slots
    for _ in range(4):
        client.portal.call(slots.acquire)
    try:
        assert client.post("/v1/incidents/INC-busy-capacity/recovery-check").status_code == 503
        app.state.workflow.recheck_recovery.assert_not_awaited()
        app.state.repository.save.assert_not_awaited()
    finally:
        for _ in range(4):
            slots.release()


def test_lock_acquisition_failure_is_redacted_before_graph_queries(client):
    @asynccontextmanager
    async def unavailable(incident_id):
        raise OperationalError("private-database-diagnostic")
        yield False

    app.state.repository.recovery_observation_lock = unavailable
    response = client.post("/v1/incidents/INC-lock-unavailable/recovery-check")
    assert response.status_code == 503 and "private-database-diagnostic" not in response.text
    app.state.workflow.recheck_recovery.assert_not_awaited()


def test_missing_checkpoint_does_not_save_an_invented_observation(client):
    app.state.workflow.recheck_recovery.return_value = None
    response = client.post("/v1/incidents/INC-missing/recovery-check")
    assert response.status_code == 404
    app.state.repository.save.assert_not_awaited()
    app.state.workflow.recheck_recovery.assert_awaited_once_with("INC-missing", request_actor="unauthenticated-demo")
