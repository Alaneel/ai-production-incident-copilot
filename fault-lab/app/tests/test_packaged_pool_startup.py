from contextlib import contextmanager
import os
from unittest.mock import MagicMock, patch

import psycopg
import pytest

from incident_lab.start import main, wait_for_database


@pytest.fixture
def environment(monkeypatch):
    monkeypatch.setenv("INCIDENT_LAB_DB_POOL_BACKEND", "postgres")
    monkeypatch.setenv("FAULT_INJECTION_TOKEN", "packaging-test-token")
    monkeypatch.setenv("INCIDENT_LAB_POSTGRES_URL", "host=/tmp/incident-lab-postgres dbname=incident_pool_lab user=incident_lab")


@pytest.mark.parametrize("token", ["", "local-fault-token"])
def test_startup_rejects_missing_default_token_before_connect_or_exec(environment, monkeypatch, token):
    monkeypatch.setenv("FAULT_INJECTION_TOKEN", token)
    with patch("incident_lab.start.psycopg.connect") as connect, patch("incident_lab.start.os.execvp") as execute:
        with pytest.raises(SystemExit, match="ValueError"):
            main()
        connect.assert_not_called()
        execute.assert_not_called()


def test_startup_rejects_remote_target_before_connect(environment, monkeypatch):
    monkeypatch.setenv("INCIDENT_LAB_POSTGRES_URL", "host=private.example dbname=private_test password=private-value")
    with patch("incident_lab.start.psycopg.connect") as connect:
        with pytest.raises(SystemExit) as error:
            main()
        assert str(error.value) == "Lab startup failed: ValueError"
        connect.assert_not_called()


def test_startup_retries_read_only_probe_then_closes_before_exec(environment):
    cursor = MagicMock()
    cursor.fetchone.return_value = (1,)
    connection = MagicMock()
    connection.cursor.return_value.__enter__.return_value = cursor
    closed = []

    @contextmanager
    def opened():
        try:
            yield connection
        finally:
            closed.append(True)

    now = [0]
    def sleep(seconds):
        now[0] += seconds
    with patch("incident_lab.start.psycopg.connect", side_effect=[psycopg.OperationalError("private provider details"), opened()]) as connect:
        wait_for_database(budget_seconds=3, monotonic=lambda: now[0], sleep=sleep)
    assert now == [1] and closed == [True]
    cursor.execute.assert_called_once_with("SELECT 1")
    assert connect.call_args.kwargs["host"] == "/tmp/incident-lab-postgres"
    assert connect.call_args.kwargs["connect_timeout"] == "2"
    assert connect.call_args.kwargs["options"] == "-c statement_timeout=2000"


def test_startup_exhausted_budget_does_not_execute_or_expose_connection(environment):
    now = [0]
    def sleep(seconds):
        now[0] += seconds
    with patch("incident_lab.start.psycopg.connect", side_effect=psycopg.OperationalError("private-url-key")) as connect:
        with pytest.raises(TimeoutError) as error:
            wait_for_database(budget_seconds=2, monotonic=lambda: now[0], sleep=sleep)
    assert now == [2] and connect.call_count == 2
    assert "private-url-key" not in str(error.value)


def test_startup_does_not_retry_permanent_database_errors(environment):
    with patch("incident_lab.start.psycopg.connect", side_effect=psycopg.ProgrammingError("private details")) as connect:
        with pytest.raises(SystemExit) as error:
            main()
    assert str(error.value) == "Lab startup failed: ProgrammingError"
    connect.assert_called_once()


def test_simulation_bootstrap_skips_database_and_execs_fixed_server(monkeypatch):
    monkeypatch.setenv("INCIDENT_LAB_DB_POOL_BACKEND", "simulation")
    with patch("incident_lab.start.psycopg.connect") as connect, patch("incident_lab.start.os.execvp") as execute:
        main()
    connect.assert_not_called()
    execute.assert_called_once_with("uvicorn", ["uvicorn", "incident_lab.app:app", "--host", "0.0.0.0", "--port", "8090"])


@pytest.mark.skipif(not os.getenv("INCIDENT_TEST_DATABASE_URL"), reason="dedicated local PostgreSQL test URL required")
def test_bootstrap_real_local_database_probe_closes_its_connection(environment, monkeypatch):
    monkeypatch.setenv("INCIDENT_LAB_POSTGRES_URL", os.environ["INCIDENT_TEST_DATABASE_URL"])
    original = psycopg.connect
    connections = []
    def connect(**parameters):
        connection = original(**parameters)
        connections.append(connection)
        return connection
    with patch("incident_lab.start.psycopg.connect", side_effect=connect):
        wait_for_database(budget_seconds=5)
    assert len(connections) == 1 and connections[0].closed


@pytest.mark.parametrize("budget", [0, -1, 61])
def test_startup_budget_is_bounded(environment, budget):
    with patch("incident_lab.start.psycopg.connect") as connect:
        with pytest.raises(ValueError):
            wait_for_database(budget_seconds=budget)
        connect.assert_not_called()
