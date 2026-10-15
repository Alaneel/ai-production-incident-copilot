import asyncio
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError, RedisError

from incident_copilot.api import app, lifespan
from incident_copilot.task_state import InMemoryTaskStateStore


ENV = {"DATABASE_URL": "", "REDIS_URL": "redis://local-test.invalid:6379/0",
       "MCP_SERVER_URL": "", "OPENAI_API_KEY": "", "OPENAI_MODEL": "",
       "ORCHESTRATOR_AUTH_MODE": "disabled"}
REDIS_BINARY = shutil.which("redis-server") or (
    "/opt/homebrew/bin/redis-server" if Path("/opt/homebrew/bin/redis-server").is_file() else None)


def fake_cache(ping):
    store = InMemoryTaskStateStore()
    store.redis = SimpleNamespace(ping=ping)
    store.close = AsyncMock()
    return store


def test_unavailable_cache_does_not_block_startup_and_original_client_can_recover():
    ping = AsyncMock(side_effect=RedisConnectionError("private endpoint/credentials"))
    cache = fake_cache(ping)
    real_publish, real_events = cache.publish, cache.events_after
    cache.publish = AsyncMock(side_effect=RedisConnectionError("private unavailable cache"))
    cache.events_after = AsyncMock(side_effect=RedisConnectionError("private unavailable cache"))
    with patch.dict(os.environ, ENV), patch("incident_copilot.api.RedisTaskStateStore.from_url", return_value=cache):
        with TestClient(app) as client:
            health = client.get("/health").json()
            assert health["task_state"] == "redis"
            assert health["event_cache_startup"] == "unavailable"
            assert "private" not in str(health)
            created = client.post("/v1/incidents", json={"question": "Why is payment-service slow today?"})
            assert created.status_code == 201
            assert created.headers["X-Incident-Event-Delivery-Reason"] == "cache-unavailable"
            state = created.json()
            assert client.get(f"/v1/incidents/{state['incident_id']}").json() == state
            events = client.get(f"/v1/incidents/{state['incident_id']}/events")
            assert "task_store_unavailable" in events.text
            assert "private" not in events.text
            assert app.state.task_store is cache
            cache.publish, cache.events_after = real_publish, real_events
            restored = client.post("/v1/incidents", json={"question": "Why is payment-service slow today?"})
            assert restored.status_code == 201
            assert restored.headers["X-Incident-Event-Delivery"] == "event-store"
            # This is a startup snapshot, not a claim of current cache health.
            assert client.get("/health").json()["event_cache_startup"] == "unavailable"
    ping.assert_awaited_once()
    cache.close.assert_awaited_once()


def test_stalled_startup_probe_is_cancelled_and_api_still_starts():
    cancelled = []

    async def ping():
        try:
            await asyncio.Future()
        finally:
            cancelled.append(True)

    cache = fake_cache(ping)
    with patch.dict(os.environ, ENV), patch("incident_copilot.api.RedisTaskStateStore.from_url", return_value=cache), \
            patch("incident_copilot.api.EVENT_CACHE_STARTUP_TIMEOUT_SECONDS", 0.01):
        with TestClient(app) as client:
            assert client.get("/health").json()["event_cache_startup"] == "timeout"
            assert app.state.task_store is cache
    assert cancelled == [True]
    cache.close.assert_awaited_once()


def test_ready_cache_startup_is_reported_without_changing_backend():
    cache = fake_cache(AsyncMock(return_value=True))
    with patch.dict(os.environ, ENV), patch("incident_copilot.api.RedisTaskStateStore.from_url", return_value=cache):
        with TestClient(app) as client:
            assert client.get("/health").json()["event_cache_startup"] == "ready"
            assert client.get("/health").json()["task_state"] == "redis"
    cache.close.assert_awaited_once()


def test_invalid_cache_configuration_fails_startup_and_closes_repository():
    closed = AsyncMock()
    with patch.dict(os.environ, ENV), patch("incident_copilot.api.RedisTaskStateStore.from_url", side_effect=ValueError("invalid test URL")), \
            patch("incident_copilot.api.InMemoryIncidentStore.close", closed):
        with pytest.raises(ValueError, match="invalid test URL"), TestClient(app):
            pass
    closed.assert_awaited_once()


def test_startup_cancellation_closes_cache_and_repository():
    entered = asyncio.Event()

    async def ping():
        entered.set()
        await asyncio.Future()

    cache = fake_cache(ping)
    closed = AsyncMock()

    async def run():
        async def start():
            async with lifespan(app):
                pytest.fail("Cancelled startup must not reach readiness")
        task = asyncio.create_task(start())
        await asyncio.wait_for(entered.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    with patch.dict(os.environ, ENV), patch("incident_copilot.api.RedisTaskStateStore.from_url", return_value=cache), \
            patch("incident_copilot.api.InMemoryIncidentStore.close", closed):
        asyncio.run(run())
    cache.close.assert_awaited_once()
    closed.assert_awaited_once()


@pytest.mark.skipif(not os.getenv("INCIDENT_TEST_DATABASE_URL"), reason="isolated PostgreSQL test URL required")
def test_real_missing_redis_socket_preserves_postgres_state_across_api_startups(tmp_path):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "fault-lab"))
    from verify_observed_topology import checkpoint_url
    checkpoint_url(True, True)
    env = {**ENV, "DATABASE_URL": os.environ["INCIDENT_TEST_DATABASE_URL"],
           "REDIS_URL": "unix://" + str(tmp_path / "never-created-redis.sock")}
    with patch.dict(os.environ, env):
        with TestClient(app) as client:
            assert client.get("/health").json()["checkpoint"] == "postgres"
            assert client.get("/health").json()["event_cache_startup"] == "unavailable"
            response = client.post("/v1/incidents", json={"question": "Why is payment-service slow today?"})
            assert response.status_code == 201
            assert response.headers["X-Incident-Event-Delivery-Reason"] == "cache-unavailable"
            state = response.json()
            assert state["status"] == "waiting_approval"
            assert state["approval_decision"] is None
        with TestClient(app) as client:
            assert client.get("/health").json()["event_cache_startup"] == "unavailable"
            retained = client.get(f"/v1/incidents/{state['incident_id']}")
            assert retained.status_code == 200
            assert retained.json() == state
            stored = client.portal.call(app.state.repository.get, state["incident_id"])
            assert stored.model_dump(mode="json") == state
            events = client.get(f"/v1/incidents/{state['incident_id']}/events")
            assert events.status_code == 200
            assert "task_store_unavailable" in events.text
            assert "workflow_checkpoint" in events.text
            assert '"checkpoint_backend": "postgres"' in events.text
            assert '"read_only": true' in events.text
            assert events.text.count("event: trace") == len(state["trace"])


@pytest.mark.skipif(not REDIS_BINARY, reason="local Redis binary required for owned Unix-socket test")
def test_real_client_reconnects_when_owned_redis_starts_after_api():
    # Own a short Unix socket path, no TCP listener, no durable Redis files.
    with tempfile.TemporaryDirectory(prefix="copilot-startup-redis-", dir="/private/tmp") as directory:
        socket = Path(directory) / "redis.sock"
        env = {**ENV, "REDIS_URL": "unix://" + str(socket)}
        process = None
        try:
            with patch.dict(os.environ, env), TestClient(app) as client:
                cache = app.state.task_store
                assert client.get("/health").json()["event_cache_startup"] == "unavailable"
                degraded = client.post("/v1/incidents", json={"question": "Why is payment-service slow today?"})
                assert degraded.status_code == 201
                assert degraded.headers["X-Incident-Event-Delivery"] == "checkpoint-fallback"
                process = subprocess.Popen([REDIS_BINARY, "--port", "0", "--save", "", "--appendonly", "no",
                                            "--unixsocket", str(socket), "--unixsocketperm", "700", "--dir", directory],
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

                async def ready():
                    async def probe():
                        while process.poll() is None:
                            try:
                                await cache.redis.ping()
                                return
                            except RedisError:
                                await asyncio.sleep(0.05)
                        raise AssertionError("Owned Redis stopped before readiness")
                    await asyncio.wait_for(probe(), timeout=5)
                client.portal.call(ready)
                assert app.state.task_store is cache
                restored = client.post("/v1/incidents", json={"question": "Why is payment-service slow today?"})
                assert restored.status_code == 201
                assert restored.headers["X-Incident-Event-Delivery"] == "event-store"
                state = restored.json()
                stream = client.get(f"/v1/incidents/{state['incident_id']}/events")
                assert stream.text.count("event: trace") == len(state["trace"])
                assert "event: replay_source" not in stream.text
                assert state["approval_decision"] is None
                assert state["status"] == "waiting_approval"
                assert client.get("/health").json()["event_cache_startup"] == "unavailable"
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
