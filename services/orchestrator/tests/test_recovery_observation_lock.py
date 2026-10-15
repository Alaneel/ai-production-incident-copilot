"""Recovery checks hold one incident lock across their entire observation.

The optional PostgreSQL test needs an explicitly configured isolated test URL;
the ordinary suite uses no database and creates no persistent lock records.
"""

import asyncio
import os
import unittest
from uuid import uuid4

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from incident_copilot.models import IncidentState
from incident_copilot.store import InMemoryIncidentStore, PostgresIncidentRepository


class MemoryRecoveryObservationLockTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_incident_concurrent_checks_have_one_winner(self):
        repository = InMemoryIncidentStore()
        all_entered = asyncio.Event()
        release = asyncio.Event()
        outcomes = []

        async def check():
            async with repository.recovery_observation_lock("same-incident") as acquired:
                outcomes.append(acquired)
                if len(outcomes) == 24:
                    all_entered.set()
                await release.wait()

        tasks = [asyncio.create_task(check()) for _ in range(24)]
        try:
            await asyncio.wait_for(all_entered.wait(), timeout=2)
            self.assertEqual(outcomes.count(True), 1)
            self.assertEqual(outcomes.count(False), 23)
        finally:
            release.set()
            await asyncio.gather(*tasks)
        async with repository.recovery_observation_lock("same-incident") as acquired:
            self.assertIs(acquired, True)

    async def test_distinct_incidents_are_observed_in_parallel(self):
        repository = InMemoryIncidentStore()
        all_entered = asyncio.Event()
        release = asyncio.Event()
        outcomes = []

        async def check(incident_id):
            async with repository.recovery_observation_lock(incident_id) as acquired:
                outcomes.append(acquired)
                if len(outcomes) == 2:
                    all_entered.set()
                await release.wait()

        tasks = [asyncio.create_task(check(incident_id)) for incident_id in ("first", "second")]
        try:
            await asyncio.wait_for(all_entered.wait(), timeout=2)
            self.assertEqual(outcomes, [True, True])
        finally:
            release.set()
            await asyncio.gather(*tasks)

    async def test_busy_context_does_not_release_the_active_owner(self):
        repository = InMemoryIncidentStore()
        async with repository.recovery_observation_lock("incident") as owner:
            self.assertIs(owner, True)
            async with repository.recovery_observation_lock("incident") as busy:
                self.assertIs(busy, False)
            async with repository.recovery_observation_lock("incident") as still_busy:
                self.assertIs(still_busy, False)
        async with repository.recovery_observation_lock("incident") as acquired:
            self.assertIs(acquired, True)

    async def test_store_reads_and_writes_are_not_blocked_inside_observation(self):
        repository = InMemoryIncidentStore()
        state = IncidentState(question="Investigate payment-service", environment="prod-sg")
        async with repository.recovery_observation_lock(state.incident_id) as acquired:
            self.assertIs(acquired, True)
            await asyncio.wait_for(repository.save(state), timeout=2)
            saved = await asyncio.wait_for(repository.get(state.incident_id), timeout=2)
            self.assertEqual(saved.incident_id, state.incident_id)

    async def test_exception_releases_lock(self):
        repository = InMemoryIncidentStore()
        with self.assertRaisesRegex(ValueError, "observation failed"):
            async with repository.recovery_observation_lock("incident") as acquired:
                self.assertIs(acquired, True)
                raise ValueError("observation failed")
        async with repository.recovery_observation_lock("incident") as acquired:
            self.assertIs(acquired, True)

    async def test_task_cancellation_releases_lock(self):
        repository = InMemoryIncidentStore()
        entered = asyncio.Event()

        async def check():
            async with repository.recovery_observation_lock("incident") as acquired:
                self.assertIs(acquired, True)
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(check())
        await asyncio.wait_for(entered.wait(), timeout=2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        async with repository.recovery_observation_lock("incident") as acquired:
            self.assertIs(acquired, True)

    async def test_cancelled_busy_check_does_not_release_owner(self):
        repository = InMemoryIncidentStore()
        entered = asyncio.Event()

        async def busy_check():
            async with repository.recovery_observation_lock("incident") as acquired:
                self.assertIs(acquired, False)
                entered.set()
                await asyncio.Event().wait()

        async with repository.recovery_observation_lock("incident") as owner:
            self.assertIs(owner, True)
            task = asyncio.create_task(busy_check())
            await asyncio.wait_for(entered.wait(), timeout=2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            async with repository.recovery_observation_lock("incident") as acquired:
                self.assertIs(acquired, False)


class RecordingScope:
    def __init__(self, connection, kind):
        self.connection = connection
        self.kind = kind

    async def __aenter__(self):
        self.connection.events.append((self.kind + "_enter", None))
        setattr(self.connection, self.kind + "_active", True)
        return self.connection

    async def __aexit__(self, exc_type, exc_value, traceback):
        self.connection.events.append((self.kind + "_exit", exc_type))
        setattr(self.connection, self.kind + "_active", False)
        return False


class RecordingConnection:
    def __init__(self, row, *, execute_error=None, fetch_error=None):
        self.row = row
        self.execute_error = execute_error
        self.fetch_error = fetch_error
        self.connection_active = False
        self.transaction_active = False
        self.events = []
        self.queries = []

    def transaction(self):
        return RecordingScope(self, "transaction")

    async def execute(self, query, parameters):
        assert self.connection_active and self.transaction_active
        self.events.append(("execute", None))
        self.queries.append((query, parameters))
        if self.execute_error:
            raise self.execute_error
        return self

    async def fetchone(self):
        assert self.connection_active and self.transaction_active
        self.events.append(("fetchone", None))
        if self.fetch_error:
            raise self.fetch_error
        return self.row


class RecordingPool:
    def __init__(self, connection):
        self.recorded_connection = connection
        self.checkouts = 0

    def connection(self):
        self.checkouts += 1
        return RecordingScope(self.recorded_connection, "connection")


class MockPostgresRecoveryObservationLockTests(unittest.IsolatedAsyncioTestCase):
    async def test_transaction_and_connection_cover_yield_and_id_is_parameterized(self):
        connection = RecordingConnection({"acquired": True})
        pool = RecordingPool(connection)
        repository = PostgresIncidentRepository(pool)
        incident_id = "incident'); SELECT pg_sleep(100); --"
        async with repository.recovery_observation_lock(incident_id) as acquired:
            self.assertIs(acquired, True)
            self.assertTrue(connection.connection_active)
            self.assertTrue(connection.transaction_active)
            self.assertEqual(pool.checkouts, 1)
            self.assertEqual(connection.queries, [(
                "SELECT pg_try_advisory_xact_lock(hashtextextended(%s, 0)) AS acquired",
                ("incident-copilot:recovery:" + incident_id,),
            )])
            self.assertNotIn(incident_id, connection.queries[0][0])
            connection.events.append(("body", None))
        self.assertFalse(connection.connection_active)
        self.assertFalse(connection.transaction_active)
        self.assertEqual(connection.events, [
            ("connection_enter", None), ("transaction_enter", None),
            ("execute", None), ("fetchone", None), ("body", None),
            ("transaction_exit", None), ("connection_exit", None),
        ])

    async def test_busy_lock_yields_false_and_releases_transaction(self):
        connection = RecordingConnection({"acquired": False})
        repository = PostgresIncidentRepository(RecordingPool(connection))
        async with repository.recovery_observation_lock("incident") as acquired:
            self.assertIs(acquired, False)
            self.assertTrue(connection.transaction_active)
        self.assertFalse(connection.transaction_active)
        self.assertFalse(connection.connection_active)
        self.assertEqual(len(connection.queries), 1)

    async def test_only_actual_true_row_grants_lock(self):
        for row in (None, {}, {"acquired": None}, {"acquired": 1},
                    {"acquired": "true"}, {"acquired": False}, [True]):
            with self.subTest(row=row):
                connection = RecordingConnection(row)
                repository = PostgresIncidentRepository(RecordingPool(connection))
                async with repository.recovery_observation_lock("incident") as acquired:
                    self.assertIs(acquired, False)
                self.assertFalse(connection.transaction_active)
                self.assertFalse(connection.connection_active)

    async def test_exception_rolls_back_before_returning_connection(self):
        connection = RecordingConnection({"acquired": True})
        repository = PostgresIncidentRepository(RecordingPool(connection))
        with self.assertRaisesRegex(ValueError, "observation failed"):
            async with repository.recovery_observation_lock("incident") as acquired:
                self.assertIs(acquired, True)
                raise ValueError("observation failed")
        self.assertEqual(connection.events[-2:], [
            ("transaction_exit", ValueError), ("connection_exit", ValueError),
        ])
        self.assertFalse(connection.transaction_active)
        self.assertFalse(connection.connection_active)

    async def test_cancellation_rolls_back_before_returning_connection(self):
        connection = RecordingConnection({"acquired": True})
        repository = PostgresIncidentRepository(RecordingPool(connection))
        entered = asyncio.Event()

        async def check():
            async with repository.recovery_observation_lock("incident") as acquired:
                self.assertIs(acquired, True)
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(check())
        await asyncio.wait_for(entered.wait(), timeout=2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(connection.events[-2:], [
            ("transaction_exit", asyncio.CancelledError),
            ("connection_exit", asyncio.CancelledError),
        ])
        self.assertFalse(connection.transaction_active)
        self.assertFalse(connection.connection_active)

    async def test_query_or_fetch_failure_releases_connection(self):
        for phase in ("execute_error", "fetch_error"):
            with self.subTest(phase=phase):
                connection = RecordingConnection({"acquired": True}, **{phase: ValueError("db failed")})
                repository = PostgresIncidentRepository(RecordingPool(connection))
                with self.assertRaisesRegex(ValueError, "db failed"):
                    async with repository.recovery_observation_lock("incident"):
                        self.fail("No observation may run after acquisition failure")
                self.assertEqual(connection.events[-2:], [
                    ("transaction_exit", ValueError), ("connection_exit", ValueError),
                ])
                self.assertFalse(connection.transaction_active)
                self.assertFalse(connection.connection_active)


@unittest.skipUnless(os.getenv("INCIDENT_TEST_DATABASE_URL"), "isolated PostgreSQL test URL required")
class PostgresRecoveryObservationLockTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Advisory locks need no schema setup or persistent rows. Each repository
        # has its own pool, as two independent orchestrator workers would.
        self.pools = [AsyncConnectionPool(
            os.environ["INCIDENT_TEST_DATABASE_URL"], min_size=1, max_size=2,
            open=False, kwargs={"autocommit": True, "row_factory": dict_row},
        ) for _ in range(2)]
        for pool in self.pools:
            self.addAsyncCleanup(pool.close)
            await pool.open(wait=True)
        self.first, self.second = [PostgresIncidentRepository(pool) for pool in self.pools]
        self.incident_id = "recovery-lock-test-" + uuid4().hex

    async def test_independent_repositories_conflict_and_release(self):
        async with self.first.recovery_observation_lock(self.incident_id) as first:
            self.assertIs(first, True)
            async with self.second.recovery_observation_lock(self.incident_id) as second:
                self.assertIs(second, False)
            async with self.second.recovery_observation_lock(self.incident_id + "-other") as other:
                self.assertIs(other, True)
            async with self.second.recovery_observation_lock(self.incident_id) as still_busy:
                self.assertIs(still_busy, False)
        async with self.second.recovery_observation_lock(self.incident_id) as acquired:
            self.assertIs(acquired, True)

    async def test_exception_and_cancellation_release_across_repositories(self):
        with self.assertRaisesRegex(ValueError, "observation failed"):
            async with self.first.recovery_observation_lock(self.incident_id) as acquired:
                self.assertIs(acquired, True)
                raise ValueError("observation failed")
        async with self.second.recovery_observation_lock(self.incident_id) as acquired:
            self.assertIs(acquired, True)
        entered = asyncio.Event()

        async def check():
            async with self.first.recovery_observation_lock(self.incident_id) as acquired:
                self.assertIs(acquired, True)
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(check())
        await asyncio.wait_for(entered.wait(), timeout=5)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        async with self.second.recovery_observation_lock(self.incident_id) as acquired:
            self.assertIs(acquired, True)
