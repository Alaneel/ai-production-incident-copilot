from fastapi.testclient import TestClient
from datetime import UTC, datetime, timedelta
from importlib import import_module
import pytest

from incident_lab.app import app, state


def test_fault_endpoint_requires_token() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/admin/fault",
            json={
                "mode": "db_pool_exhaustion",
                "duration_seconds": 60,
                "incident_id": "INC-TEST",
            },
        )
    assert response.status_code == 401


def test_db_pool_fault_produces_evidence_and_can_be_cleared(monkeypatch) -> None:
    # The behavior under test is explicit clearing, not real-time expiry; a
    # suspended laptop or wall-clock correction must not change this case.
    module = import_module("incident_lab.app")
    instant = datetime.now(UTC)

    class FixedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant if tz is not None else instant.replace(tzinfo=None)

    monkeypatch.setattr(module, "datetime", FixedClock)
    with TestClient(app) as client:
        injected = client.post(
            "/admin/fault",
            headers={"x-fault-token": "local-fault-token"},
            json={
                "mode": "db_pool_exhaustion",
                "duration_seconds": 60,
                "incident_id": "INC-TEST",
            },
        )
        response = client.post("/v1/work", headers={"x-trace-id": "trace-test"})
        metrics = client.get("/metrics")
        cleared = client.delete(
            "/admin/fault",
            headers={"x-fault-token": "local-fault-token"},
        )
    assert injected.status_code == 200
    assert response.status_code == 503
    assert "incident_db_pool_active" in metrics.text
    assert cleared.json()["previous_fault"] == "db_pool_exhaustion"
    assert state.active_mode() == "none"


def test_fault_expiry_clears_state_at_deadline(monkeypatch) -> None:
    module = import_module("incident_lab.app")
    instant = datetime(2026, 10, 3, tzinfo=UTC)

    class ControlledClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant if tz is not None else instant.replace(tzinfo=None)

    monkeypatch.setattr(module, "datetime", ControlledClock)
    elapsed = 100.0
    monkeypatch.setattr(module.time, "monotonic", lambda: elapsed)
    fault = module.FaultState(mode="db_pool_exhaustion", expires_at=instant + timedelta(seconds=60), expires_monotonic=elapsed + 60)
    assert fault.active_mode() == "db_pool_exhaustion"
    elapsed += 60  # Expiry does not depend on the wall clock moving.
    assert fault.active_mode() == "none"
    assert fault.expires_at is None
    assert fault.expires_monotonic is None


@pytest.mark.parametrize("jump_seconds", [3600, -3600])
def test_wall_clock_jump_cannot_shorten_or_extend_fault_ttl(monkeypatch, jump_seconds):
    module = import_module("incident_lab.app")
    instant = datetime(2026, 10, 3, tzinfo=UTC)
    elapsed = 100.0

    class ControlledClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant if tz is not None else instant.replace(tzinfo=None)

    monkeypatch.setattr(module, "datetime", ControlledClock)
    monkeypatch.setattr(module.time, "monotonic", lambda: elapsed)
    fault = module.FaultState(mode="memory_leak", expires_at=instant + timedelta(seconds=60), expires_monotonic=elapsed + 60)
    instant += timedelta(seconds=jump_seconds)
    elapsed += 59
    assert fault.active_mode() == "memory_leak"
    elapsed += 1
    assert fault.active_mode() == "none"
    assert fault.expires_monotonic is None


def test_fault_injection_sets_monotonic_deadline_and_clear_resets_it():
    module = import_module("incident_lab.app")
    with TestClient(app) as client:
        before = module.time.monotonic()
        injected = client.post("/admin/fault", headers={"x-fault-token": "local-fault-token"},
            json={"mode": "memory_leak", "duration_seconds": 60, "incident_id": "INC-CLOCK"})
        after = module.time.monotonic()
        try:
            assert injected.status_code == 200
            assert before + 60 <= state.expires_monotonic <= after + 60
            assert injected.json()["expires_at"] == state.expires_at.isoformat()
        finally:
            client.delete("/admin/fault", headers={"x-fault-token": "local-fault-token"})
        assert state.expires_monotonic is None
