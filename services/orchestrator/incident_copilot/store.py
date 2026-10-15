from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import AsyncContextManager, AsyncIterator, Protocol

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from .models import ApprovalRequest, IncidentState


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS incidents (
    incident_id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    current_step TEXT NOT NULL,
    environment TEXT NOT NULL,
    question TEXT NOT NULL,
    state JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS incidents_status_updated_idx
    ON incidents (status, updated_at DESC);

CREATE TABLE IF NOT EXISTS approval_decisions (
    id BIGSERIAL PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES incidents(incident_id),
    remediation_id TEXT,
    decision TEXT NOT NULL CHECK (decision IN ('approve', 'reject')),
    actor TEXT NOT NULL,
    reason TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    decided_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS idempotency_keys (
    key TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES incidents(incident_id),
    operation TEXT NOT NULL,
    claimed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS incident_operation_claims (
    incident_id TEXT NOT NULL REFERENCES incidents(incident_id),
    operation TEXT NOT NULL,
    key TEXT NOT NULL UNIQUE REFERENCES idempotency_keys(key) ON DELETE CASCADE,
    PRIMARY KEY (incident_id, operation)
);

CREATE TABLE IF NOT EXISTS tool_audit_events (
    id BIGSERIAL PRIMARY KEY,
    incident_id TEXT REFERENCES incidents(incident_id),
    trace_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    principal TEXT NOT NULL,
    mode TEXT NOT NULL,
    arguments JSONB NOT NULL,
    outcome JSONB,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS evaluation_runs (
    run_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    configuration JSONB NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS operation_inspection_audits (
    audit_id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES incidents(incident_id),
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    request JSONB NOT NULL,
    outcome JSONB
);
CREATE INDEX IF NOT EXISTS operation_inspection_incident_idx
    ON operation_inspection_audits (incident_id, started_at DESC);

CREATE TABLE IF NOT EXISTS evaluation_results (
    run_id TEXT NOT NULL REFERENCES evaluation_runs(run_id),
    case_id TEXT NOT NULL,
    prediction JSONB NOT NULL,
    metrics JSONB NOT NULL,
    latency_ms DOUBLE PRECISION NOT NULL,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    cost_usd NUMERIC(12, 6),
    PRIMARY KEY (run_id, case_id)
);
"""


class ApprovalConflictError(ValueError):
    """An approval key cannot be rebound to another decision or actor."""


class IncidentRepository(Protocol):
    def recovery_observation_lock(self, incident_id: str) -> AsyncContextManager[bool]: ...

    async def begin_inspection(self, audit_id: str, request: dict) -> None: ...
    async def finish_inspection(self, audit_id: str, outcome: dict) -> None: ...
    async def inspection_history(self, incident_id: str) -> list[dict]: ...
    async def claim_approval(self, state: IncidentState, approval: ApprovalRequest) -> bool: ...
    async def save(self, state: IncidentState) -> None: ...

    async def get(self, incident_id: str) -> IncidentState | None: ...

    async def record_approval(
        self, state: IncidentState, approval: ApprovalRequest
    ) -> None: ...

    async def claim_idempotency_key(
        self, incident_id: str, key: str, operation: str
    ) -> bool: ...

    async def release_idempotency_key(self, key: str) -> None: ...

    async def close(self) -> None: ...


class InMemoryIncidentStore:
    """Test repository with the same concurrency semantics as PostgreSQL."""

    def __init__(self) -> None:
        self._states: dict[str, IncidentState] = {}
        self._idempotency_keys: dict[str, tuple[str, str]] = {}
        self._operation_claims: set[tuple[str, str]] = set()
        self._approvals: list[tuple[str, ApprovalRequest]] = []
        self._approval_bindings: dict[str, tuple[str, str | None, dict]] = {}
        self._lock = asyncio.Lock()
        self._active_recovery_checks: set[str] = set()
        self._inspection_audits: dict[str, dict] = {}

    @asynccontextmanager
    async def recovery_observation_lock(self, incident_id: str) -> AsyncIterator[bool]:
        async with self._lock:
            acquired = incident_id not in self._active_recovery_checks
            if acquired:
                self._active_recovery_checks.add(incident_id)
        try:
            yield acquired
        finally:
            if acquired:
                async with self._lock:
                    self._active_recovery_checks.discard(incident_id)

    async def begin_inspection(self, audit_id: str, request: dict) -> None:
        async with self._lock:
            if request["incident_id"] not in self._states:
                raise ValueError("Incident must be recorded before inspection.")
            if audit_id in self._inspection_audits:
                raise ValueError("Inspection audit already exists.")
            self._inspection_audits[audit_id] = {
                "audit_id": audit_id, "incident_id": request["incident_id"],
                "started_at": datetime.now(UTC).isoformat(),
                "request": json.loads(json.dumps(request)), "outcome": None,
            }

    async def finish_inspection(self, audit_id: str, outcome: dict) -> None:
        async with self._lock:
            record = self._inspection_audits[audit_id]
            if record["outcome"] is not None:
                raise ValueError("Inspection outcome is already recorded.")
            record["outcome"] = json.loads(json.dumps(outcome))

    async def inspection_history(self, incident_id: str) -> list[dict]:
        async with self._lock:
            return json.loads(json.dumps([record for record in reversed(list(self._inspection_audits.values()))
                                         if record["incident_id"] == incident_id][:100]))

    async def save(self, state: IncidentState) -> None:
        async with self._lock:
            self._states[state.incident_id] = state.model_copy(deep=True)

    async def get(self, incident_id: str) -> IncidentState | None:
        async with self._lock:
            state = self._states.get(incident_id)
            return state.model_copy(deep=True) if state else None

    async def record_approval(
        self, state: IncidentState, approval: ApprovalRequest
    ) -> None:
        async with self._lock:
            self._approvals.append((state.incident_id, approval.model_copy(deep=True)))

    async def claim_approval(self, state: IncidentState, approval: ApprovalRequest) -> bool:
        async with self._lock:
            binding = (state.incident_id, state.remediation.id if state.remediation else None,
                       approval.model_dump(mode="json"))
            old = self._approval_bindings.get(approval.idempotency_key)
            if old is not None and old != binding:
                raise ApprovalConflictError("Approval key is bound to a different decision.")
            claim = (state.incident_id, "remediation_approval")
            if approval.idempotency_key in self._idempotency_keys or claim in self._operation_claims:
                return False
            self._approval_bindings[approval.idempotency_key] = binding
            self._idempotency_keys[approval.idempotency_key] = claim
            self._operation_claims.add(claim)
            if old is None:
                self._approvals.append((state.incident_id, approval.model_copy(deep=True)))
            return True

    async def claim_idempotency_key(
        self, incident_id: str, key: str, operation: str = "approval"
    ) -> bool:
        async with self._lock:
            claim = (incident_id, operation)
            if key in self._idempotency_keys or claim in self._operation_claims:
                return False
            self._idempotency_keys[key] = claim
            self._operation_claims.add(claim)
            return True

    async def release_idempotency_key(self, key: str) -> None:
        async with self._lock:
            claim = self._idempotency_keys.pop(key, None)
            if claim is not None:
                self._operation_claims.discard(claim)

    async def close(self) -> None:
        return None


class PostgresIncidentRepository:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self.pool = pool

    @asynccontextmanager
    async def recovery_observation_lock(self, incident_id: str) -> AsyncIterator[bool]:
        # Transaction-scoped locks coordinate independent workers and release on
        # exit or connection loss without durable lease rows or a reaper.
        async with self.pool.connection() as connection, connection.transaction():
            cursor = await connection.execute(
                "SELECT pg_try_advisory_xact_lock(hashtextextended(%s, 0)) AS acquired",
                ("incident-copilot:recovery:" + incident_id,),
            )
            row = await cursor.fetchone()
            yield isinstance(row, dict) and row.get("acquired") is True

    @classmethod
    async def connect(cls, connection_string: str) -> "PostgresIncidentRepository":
        pool = AsyncConnectionPool(
            connection_string,
            min_size=1,
            max_size=10,
            open=False,
            kwargs={"autocommit": True, "row_factory": dict_row},
        )
        await pool.open()
        repository = cls(pool)
        await repository.setup()
        return repository

    async def setup(self) -> None:
        async with self.pool.connection() as connection:
            await connection.execute(SCHEMA_SQL)

    async def begin_inspection(self, audit_id: str, request: dict) -> None:
        async with self.pool.connection() as connection:
            await connection.execute(
                "INSERT INTO operation_inspection_audits (audit_id, incident_id, request) VALUES (%s, %s, %s::jsonb)",
                (audit_id, request["incident_id"], json.dumps(request)),
            )

    async def finish_inspection(self, audit_id: str, outcome: dict) -> None:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                "UPDATE operation_inspection_audits SET outcome = %s::jsonb WHERE audit_id = %s AND outcome IS NULL RETURNING audit_id",
                (json.dumps(outcome), audit_id),
            )
            if await cursor.fetchone() is None:
                raise ValueError("Inspection audit missing or outcome already recorded.")

    async def inspection_history(self, incident_id: str) -> list[dict]:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT audit_id, incident_id, started_at, request, outcome FROM operation_inspection_audits WHERE incident_id = %s ORDER BY started_at DESC, audit_id DESC LIMIT 100",
                (incident_id,),
            )
            return [{**row, "started_at": row["started_at"].isoformat()} for row in await cursor.fetchall()]

    async def save(self, state: IncidentState) -> None:
        payload = json.dumps(state.model_dump(mode="json"), separators=(",", ":"))
        async with self.pool.connection() as connection:
            await connection.execute(
                """
                INSERT INTO incidents (
                    incident_id, trace_id, status, current_step, environment,
                    question, state, created_at, updated_at
                )
                VALUES (
                    %(incident_id)s, %(trace_id)s, %(status)s, %(current_step)s,
                    %(environment)s, %(question)s, %(state)s::jsonb,
                    %(created_at)s, %(updated_at)s
                )
                ON CONFLICT (incident_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    current_step = EXCLUDED.current_step,
                    state = EXCLUDED.state,
                    updated_at = EXCLUDED.updated_at
                """,
                {
                    "incident_id": state.incident_id,
                    "trace_id": state.trace_id,
                    "status": state.status.value,
                    "current_step": state.current_step.value,
                    "environment": state.environment,
                    "question": state.question,
                    "state": payload,
                    "created_at": state.created_at,
                    "updated_at": state.updated_at,
                },
            )

    async def get(self, incident_id: str) -> IncidentState | None:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT state FROM incidents WHERE incident_id = %s",
                (incident_id,),
            )
            row = await cursor.fetchone()
        return IncidentState.model_validate(row["state"]) if row else None

    async def record_approval(
        self, state: IncidentState, approval: ApprovalRequest
    ) -> None:
        remediation_id = state.remediation.id if state.remediation else None
        async with self.pool.connection() as connection:
            await connection.execute(
                """
                INSERT INTO approval_decisions (
                    incident_id, remediation_id, decision, actor, reason,
                    idempotency_key, decided_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (idempotency_key) DO NOTHING
                """,
                (
                    state.incident_id,
                    remediation_id,
                    approval.decision.value,
                    approval.actor,
                    approval.reason,
                    approval.idempotency_key,
                    datetime.now(UTC),
                ),
            )

    async def claim_idempotency_key(
        self, incident_id: str, key: str, operation: str = "approval"
    ) -> bool:
        async with self.pool.connection() as connection, connection.transaction():
            return await self._claim_key(connection, incident_id, key, operation)

    @staticmethod
    async def _claim_key(connection, incident_id: str, key: str, operation: str) -> bool:
        cursor = await connection.execute(
            """
            INSERT INTO idempotency_keys (key, incident_id, operation)
            VALUES (%s, %s, %s)
            ON CONFLICT (key) DO NOTHING
            RETURNING key
            """,
            (key, incident_id, operation),
        )
        if await cursor.fetchone() is None:
            return False
        cursor = await connection.execute(
            """
            INSERT INTO incident_operation_claims (incident_id, operation, key)
            VALUES (%s, %s, %s)
            ON CONFLICT DO NOTHING
            RETURNING key
            """,
            (incident_id, operation, key),
        )
        if await cursor.fetchone() is not None:
            return True
        await connection.execute("DELETE FROM idempotency_keys WHERE key = %s", (key,))
        return False

    async def claim_approval(self, state: IncidentState, approval: ApprovalRequest) -> bool:
        binding = {
            "incident_id": state.incident_id,
            "remediation_id": state.remediation.id if state.remediation else None,
            "decision": approval.decision.value, "actor": approval.actor,
            "reason": approval.reason,
        }
        async with self.pool.connection() as connection, connection.transaction():
            cursor = await connection.execute(
                "SELECT incident_id, remediation_id, decision, actor, reason FROM approval_decisions WHERE idempotency_key = %s",
                (approval.idempotency_key,),
            )
            existing = await cursor.fetchone()
            if existing is not None and existing != binding:
                raise ApprovalConflictError("Approval key is bound to a different decision.")
            if not await self._claim_key(connection, state.incident_id,
                                         approval.idempotency_key, "remediation_approval"):
                return False
            # The claim and immutable decision commit or roll back together.
            await connection.execute(
                """
                INSERT INTO approval_decisions
                    (incident_id, remediation_id, decision, actor, reason, idempotency_key)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (idempotency_key) DO NOTHING
                """,
                (state.incident_id, binding["remediation_id"], approval.decision.value,
                 approval.actor, approval.reason, approval.idempotency_key),
            )
            return True

    async def release_idempotency_key(self, key: str) -> None:
        async with self.pool.connection() as connection:
            await connection.execute(
                "DELETE FROM idempotency_keys WHERE key = %s",
                (key,),
            )

    async def close(self) -> None:
        await self.pool.close()
