"""Bounded, read-only database readiness for the opt-in packaged pool lab."""
from __future__ import annotations

import os
import time

import psycopg

from .postgres_pool import local_parameters


def wait_for_database(*, budget_seconds=60, monotonic=time.monotonic, sleep=time.sleep):
    if os.getenv("INCIDENT_LAB_DB_POOL_BACKEND", "simulation") != "postgres":
        return
    token = os.getenv("FAULT_INJECTION_TOKEN", "")
    if not token or token == "local-fault-token":
        raise ValueError("Real pool startup requires an explicit non-default fault token")
    if not 0 < budget_seconds <= 60:
        raise ValueError("Lab readiness budget must be between zero and sixty seconds")
    params = local_parameters(os.environ["INCIDENT_LAB_POSTGRES_URL"])
    deadline = monotonic() + budget_seconds
    while monotonic() < deadline:
        try:
            with psycopg.connect(autocommit=True, **params) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    if cursor.fetchone() != (1,):
                        raise RuntimeError("Local database readiness probe returned unexpected data")
            return
        except psycopg.OperationalError:
            # No provider text, DSN or credentials in readiness errors/logs.
            remaining = deadline - monotonic()
            if remaining > 0:
                sleep(min(1, remaining))
    raise TimeoutError("Dedicated local lab database did not become ready") from None


def main():
    try:
        wait_for_database()
    except (ValueError, KeyError, psycopg.Error, TimeoutError, RuntimeError) as error:
        raise SystemExit("Lab startup failed: " + type(error).__name__) from None
    os.execvp("uvicorn", ["uvicorn", "incident_lab.app:app", "--host", "0.0.0.0", "--port", "8090"])


if __name__ == "__main__":
    main()
