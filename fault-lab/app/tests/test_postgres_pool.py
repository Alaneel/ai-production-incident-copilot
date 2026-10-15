import asyncio
import os
import time
from importlib import import_module
from unittest.mock import AsyncMock, patch

import psycopg
import pytest
from fastapi.testclient import TestClient

from incident_lab.postgres_pool import LocalPostgresPool, PoolExhausted, local_parameters


@pytest.mark.parametrize("url", [
    "postgresql://remote.example/test", "dbname=test", "host=127.0.0.1,remote dbname=test",
    "host=/var/run/postgresql dbname=test", "host=127.0.0.1 hostaddr=192.0.2.1 dbname=test",
    "host=127.0.0.1 service=production dbname=test", "host=127.0.0.1 dbname=postgresql://remote/test",
])
def test_real_pool_requires_explicit_local_target_before_connecting(url):
    async def run():
        with patch("incident_lab.postgres_pool.psycopg.AsyncConnection.connect", new_callable=AsyncMock) as connect:
            with pytest.raises(ValueError):
                await LocalPostgresPool().open(url)
            connect.assert_not_awaited()
    asyncio.run(run())


def test_libpq_environment_cannot_redirect_local_pool():
    params = local_parameters("host=localhost dbname=test")
    assert params["hostaddr"] == "127.0.0.1"
    assert "service" not in params
    assert params["options"] == "-c statement_timeout=2000"


@pytest.mark.parametrize("name", ["PGSERVICE", "PGSERVICEFILE", "PGHOSTADDR"])
def test_environment_redirects_are_rejected_without_exposing_values(monkeypatch, name):
    monkeypatch.setenv(name, "private-override")
    with pytest.raises(ValueError) as error:
        local_parameters("host=127.0.0.1 dbname=test")
    assert "private-override" not in str(error.value)


@pytest.mark.parametrize("cancel", [False, True])
def test_partial_exhaustion_timeout_or_cancellation_returns_all_reserved_leases(cancel):
    async def run():
        pool = LocalPostgresPool(size=2, acquire_timeout=0.01)
        connections = [AsyncMock(), AsyncMock()]
        with patch("incident_lab.postgres_pool.psycopg.AsyncConnection.connect", new_callable=AsyncMock,
                   side_effect=connections):
            await pool.open("host=127.0.0.1 dbname=test")
        try:
            async with pool.lease():
                if cancel:
                    task = asyncio.create_task(pool.exhaust(60))
                    async def acquired():
                        while pool.active != 2:
                            await asyncio.sleep(0)
                    await asyncio.wait_for(acquired(), timeout=1)
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                else:
                    with pytest.raises(PoolExhausted):
                        await pool.exhaust(60)
                assert pool.active == 1
                assert pool._held is None
            assert pool.active == 0
            assert pool.available.qsize() == 2
        finally:
            await pool.close()
        for connection in connections:
            connection.close.assert_awaited_once()
    asyncio.run(run())


def test_partial_startup_closes_every_opened_connection():
    async def run():
        first, second = AsyncMock(), AsyncMock()
        pool = LocalPostgresPool()
        with patch("incident_lab.postgres_pool.psycopg.AsyncConnection.connect", new_callable=AsyncMock,
                   side_effect=[first, second, psycopg.OperationalError("redacted fixture")]):
            with pytest.raises(psycopg.OperationalError):
                await pool.open("host=127.0.0.1 dbname=test")
        first.close.assert_awaited_once()
        second.close.assert_awaited_once()
    asyncio.run(run())


@pytest.mark.parametrize("configured", [False, True])
def test_real_lab_backend_rejects_missing_or_default_fault_token_before_database_access(monkeypatch, configured):
    module = import_module("incident_lab.app")
    monkeypatch.setenv("INCIDENT_LAB_DB_POOL_BACKEND", "postgres")
    monkeypatch.setenv("INCIDENT_LAB_POSTGRES_URL", "host=127.0.0.1 dbname=test")
    monkeypatch.setattr(module, "FAULT_TOKEN", "local-fault-token")
    if configured:
        monkeypatch.setenv("FAULT_INJECTION_TOKEN", "local-fault-token")
    else:
        monkeypatch.delenv("FAULT_INJECTION_TOKEN", raising=False)
    with patch("incident_lab.postgres_pool.LocalPostgresPool.open", new_callable=AsyncMock) as opened:
        with pytest.raises(ValueError, match="non-default fault token"), TestClient(module.app):
            pass
        opened.assert_not_awaited()


@pytest.mark.skipif(not os.getenv("INCIDENT_TEST_DATABASE_URL"), reason="dedicated local PostgreSQL test URL required")
def test_real_sessions_exhaust_acquisition_expire_and_release_without_database_writes():
    async def run():
        url = os.environ["INCIDENT_TEST_DATABASE_URL"]
        observed = []
        pool = LocalPostgresPool(observe=lambda active, maximum: observed.append((active, maximum)))
        await pool.open(url)
        try:
            pids = [connection.info.backend_pid for connection in pool.connections]
            assert len(set(pids)) == 4
            assert await pool.query() == 1
            await pool.exhaust(60)
            assert pool.active == 4
            async with await psycopg.AsyncConnection.connect(autocommit=True, **local_parameters(url)) as probe:
                async with probe.cursor() as cursor:
                    await cursor.execute("SELECT pid, state FROM pg_stat_activity WHERE pid = ANY(%s)", (pids,))
                    rows = await cursor.fetchall()
                    assert len(rows) == 4
                    assert all(state == "idle" for _, state in rows)
            started = time.monotonic()
            with pytest.raises(PoolExhausted):
                await pool.query()
            assert time.monotonic() - started >= 0.4
            await pool.clear_fault()
            assert await pool.query() == 1
            assert pool.active == 0
            await pool.exhaust(0.03)
            await asyncio.sleep(0.06)
            assert pool.active == 0
            assert await pool.query() == 1
            assert (4, 4) in observed and observed[-1] == (0, 4)
        finally:
            await pool.close()
        assert all(connection.closed for connection in pool.connections)
    asyncio.run(run())


@pytest.mark.skipif(not os.getenv("INCIDENT_TEST_DATABASE_URL"), reason="dedicated local PostgreSQL test URL required")
def test_real_pool_is_wired_to_lab_work_metrics_and_authorized_fault_controls(monkeypatch):
    module = import_module("incident_lab.app")
    monkeypatch.setenv("INCIDENT_LAB_DB_POOL_BACKEND", "postgres")
    monkeypatch.setenv("INCIDENT_LAB_POSTGRES_URL", os.environ["INCIDENT_TEST_DATABASE_URL"])
    monkeypatch.setenv("FAULT_INJECTION_TOKEN", "isolated-real-pool-test-token")
    monkeypatch.setattr(module, "FAULT_TOKEN", "isolated-real-pool-test-token")
    with TestClient(module.app) as client:
        assert client.get("/health").json()["db_pool_backend"] == "postgres"
        assert client.post("/v1/work").status_code == 200
        fault = {"mode": "db_pool_exhaustion", "duration_seconds": 60, "incident_id": "REAL-POOL-TEST"}
        assert client.post("/admin/fault", json=fault).status_code == 401
        assert module.postgres_pool.active == 0
        headers = {"x-fault-token": module.FAULT_TOKEN}
        assert client.get("/admin/pool").status_code == 401
        before = client.get("/admin/pool", headers=headers).json()
        assert before["backend"] == "postgres" and before["active"] == 0
        assert len(set(before["backend_pids"])) == before["maximum"] == 4
        assert client.post("/admin/fault", headers=headers, json=fault).status_code == 200
        during = client.get("/admin/pool", headers=headers).json()
        assert during["active"] == 4 and during["backend_pids"] == before["backend_pids"]
        assert module.postgres_pool.active == 4
        assert client.post("/v1/work").status_code == 503
        text = client.get("/metrics").text
        assert f'incident_db_pool_active{{service="{module.SERVICE_NAME}"}} 4.0' in text
        assert f'incident_db_pool_max{{service="{module.SERVICE_NAME}"}} 4.0' in text
        assert client.delete("/admin/fault").status_code == 401
        assert module.postgres_pool.active == 4
        assert client.delete("/admin/fault", headers=headers).status_code == 200
        assert client.post("/v1/work").status_code == 200
        pool = module.postgres_pool
    assert module.postgres_pool is None
    assert all(connection.closed for connection in pool.connections)
