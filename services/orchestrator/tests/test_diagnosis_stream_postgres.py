"""Real PostgreSQL stream durability, synthetic tools, no model/Kubernetes.

These tests consume the API's actual StreamingResponse body iterator directly
to control backpressure. They do not claim a separate HTTP transport test.
"""
import asyncio
import json
import os
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from starlette.requests import Request

from incident_copilot.api import (
    create_incident_stream,
    decide_remediation,
    get_incident,
    get_incident_history,
)
from incident_copilot.guardrails import GuardrailViolation
from incident_copilot.models import ApprovalRequest, IncidentQuestion, IncidentState
from incident_copilot.store import PostgresIncidentRepository
from incident_copilot.task_state import InMemoryTaskStateStore
from incident_copilot.tools import DemoTelemetryTools
from test_cross_service_postgres_recovery import (
    SyntheticCrossServiceTools,
    ToolFreeRecovery,
    assert_advisory,
    backend_pid,
    dedicated_url,
    workflow,
)


pytestmark = pytest.mark.skipif(
    not os.getenv("INCIDENT_TEST_DATABASE_URL"), reason="isolated PostgreSQL test URL required",
)


def request_for(runtime, repository, task_store):
    application = SimpleNamespace(state=SimpleNamespace(
        workflow=runtime, repository=repository, task_store=task_store,
    ))
    request = Request({"type": "http", "method": "POST", "path": "/v1/incidents/stream",
                       "headers": [], "app": application})
    request.state.authenticated_actor = None
    return request


def parse_frame(frame):
    text = frame.decode() if isinstance(frame, bytes) else frame
    if text.startswith(":"):
        return None
    fields = dict(line.split(": ", 1) for line in text.strip().splitlines())
    return fields["event"], json.loads(fields["data"]), fields.get("id")


@asynccontextmanager
async def postgres_runtime(tools):
    url = dedicated_url()
    async with AsyncPostgresSaver.from_conn_string(url) as saver:
        await saver.setup()
        repository = await PostgresIncidentRepository.connect(url)
        task_store = InMemoryTaskStateStore()
        runtime = workflow(tools, saver)
        request = request_for(runtime, repository, task_store)
        try:
            yield SimpleNamespace(saver=saver, runtime=runtime, repository=repository,
                                  task_store=task_store, request=request,
                                  pid=await backend_pid(saver))
        finally:
            await repository.close()
            await task_store.close()


async def exact_retained(context, payload):
    state = IncidentState.model_validate(payload)
    saved = await context.runtime.get(state.incident_id)
    canonical = await context.repository.get(state.incident_id)
    assert saved.model_dump(mode="json") == canonical.model_dump(mode="json") == payload
    assert state.approval_decision is None and state.approval_actor is None
    assert not any(event.event in {"human_decision", "execution_intent_prepared", "execution_acknowledged"}
                   for event in state.trace)
    return state


async def fresh_read_only_restore(expected, previous_pid, *, terminal):
    tools = ToolFreeRecovery()
    async with postgres_runtime(tools) as restored:
        assert restored.pid != previous_pid
        retained = await get_incident(expected.incident_id, restored.request)
        assert retained.model_dump(mode="json") == expected.model_dump(mode="json")
        assert (await restored.repository.get(expected.incident_id)).model_dump(mode="json") == expected.model_dump(mode="json")
        history = await get_incident_history(expected.incident_id, restored.request)
        assert any(item.model_dump(mode="json") == expected.model_dump(mode="json") for item in history)
        snapshot = await restored.runtime.graph.aget_state(restored.runtime._config(expected.incident_id))
        assert bool(snapshot.next) is not terminal
        if not terminal:
            with pytest.raises(GuardrailViolation, match="not waiting"):
                await restored.runtime.resume_with_approval(expected.incident_id, ApprovalRequest(
                    decision="approve", actor="isolated-test-operator", reason="Partial diagnosis is not an approval checkpoint",
                    idempotency_key=f"{expected.incident_id}-partial-stream-invalid-approval",
                ))
        assert not tools.calls
    assert not tools.calls


def test_every_streamed_progress_matches_real_saver_and_canonical_repository_before_delivery():
    async def scenario():
        tools, frames = SyntheticCrossServiceTools(), []
        async with postgres_runtime(tools) as context:
            response = await create_incident_stream(
                IncidentQuestion(question="Why is checkout-api suddenly slow?"), context.request,
            )
            try:
                async for raw in response.body_iterator:
                    if (parsed := parse_frame(raw)) is None:
                        continue
                    event, payload, identifier = parsed
                    assert event in {"incident", "complete"}
                    state = await exact_retained(context, payload)
                    assert identifier == str(len(state.trace))
                    frames.append((event, state))
            finally:
                await response.body_iterator.aclose()
            assert len(frames) >= 4
            assert [event for event, _ in frames].count("complete") == 1
            assert frames[-1][0] == "complete"
            assert frames[-2][0] == "incident"
            assert frames[-2][1].model_dump(mode="json") == frames[-1][1].model_dump(mode="json")
            progress = [state for event, state in frames if event == "incident"]
            assert len({json.dumps(state.model_dump(mode="json"), sort_keys=True) for state in progress}) == len(progress)
            assert any(state.root_cause_candidates and not state.hypotheses for state in progress)
            assert [len(state.trace) for state in progress] == sorted(len(state.trace) for state in progress)
            final, original_pid = frames[-1][1], context.pid
            assert_advisory(final)
            assert (await context.task_store.events_after(final.incident_id, 0)) == final.trace
        assert context.saver.conn.closed
        calls = list(tools.calls)
        await fresh_read_only_restore(final, original_pid, terminal=True)
        assert tools.calls == calls and not any(call[0] == "unexpected" for call in tools.calls)
    asyncio.run(scenario())


async def prefix_at_service_selection(context):
    response = await create_incident_stream(
        IncidentQuestion(question="Why is checkout-api suddenly slow?"), context.request,
    )
    async for raw in response.body_iterator:
        if (parsed := parse_frame(raw)) is None:
            continue
        event, payload, _ = parsed
        assert event == "incident"
        state = await exact_retained(context, payload)
        if state.current_step == "select_services":
            return response, state
    raise AssertionError("No retained service-selection progress was delivered")


def test_closing_partial_stream_leaves_only_a_readable_prefix_without_tools_resume_or_approval():
    async def scenario():
        tools = SyntheticCrossServiceTools()
        async with postgres_runtime(tools) as context:
            response, prefix = await prefix_at_service_selection(context)
            calls = list(tools.calls)
            assert calls and {call[0] for call in calls} == {"topology"}
            await response.body_iterator.aclose()
            # Give cancellation/finalizer tasks scheduling opportunities. This
            # is not a wall-clock sleep or an implicit graph resume.
            for _ in range(3):
                await asyncio.sleep(0)
            assert tools.calls == calls
            assert (await context.runtime.get(prefix.incident_id)).model_dump(mode="json") == prefix.model_dump(mode="json")
            assert not await context.task_store.events_after(prefix.incident_id, 0)
            original_pid = context.pid
        assert context.saver.conn.closed
        await fresh_read_only_restore(prefix, original_pid, terminal=False)
        assert tools.calls == calls
    asyncio.run(scenario())


def test_actual_checkpoint_connection_failure_never_emits_complete_and_preserves_read_only_prefix():
    """Close the actual private database connection, not a mocked saver write."""
    async def scenario():
        tools, remaining = SyntheticCrossServiceTools(), []
        async with postgres_runtime(tools) as context:
            response, prefix = await prefix_at_service_selection(context)
            original_pid = context.pid
            # Strict serializer may shallow-clone the saver; its connection is
            # nevertheless this same actual owned PostgreSQL connection.
            assert context.runtime.graph.checkpointer.conn is context.saver.conn
            await context.saver.conn.close()
            assert context.saver.conn.closed
            try:
                async for raw in response.body_iterator:
                    if (parsed := parse_frame(raw)) is not None:
                        remaining.append(parsed)
            finally:
                await response.body_iterator.aclose()
            assert remaining and remaining[-1][0] == "error"
            assert not any(event == "complete" for event, _, _ in remaining)
            assert not any(event == "incident" for event, _, _ in remaining)
            error = remaining[-1][1]
            assert error["code"] == "persistence_unavailable"
            assert error["incident_id"] == prefix.incident_id
            assert "connection" not in error["message"].lower()
            assert (await context.repository.get(prefix.incident_id)).model_dump(mode="json") == prefix.model_dump(mode="json")
            assert not await context.task_store.events_after(prefix.incident_id, 0)
            calls = list(tools.calls)
        await fresh_read_only_restore(prefix, original_pid, terminal=False)
        assert tools.calls == calls and not any(call[0] == "unexpected" for call in calls)
    asyncio.run(scenario())


class SimulationPreflightOnlyTools(DemoTelemetryTools):
    """Only the demo's in-memory dry-run is allowed; no actual dispatch."""

    def __init__(self):
        super().__init__()
        self.preflights = []
        self.dispatches = []

    async def rollback_release(self, **arguments):
        if arguments.get("dry_run") is not True:
            self.dispatches.append(arguments)
            raise AssertionError("Partial stream must not dispatch a remediation")
        self.preflights.append(arguments)
        return await super().rollback_release(**arguments)

    async def restart_deployment(self, **arguments):
        if arguments.get("dry_run") is not True:
            self.dispatches.append(arguments)
            raise AssertionError("Partial stream must not dispatch a remediation")
        self.preflights.append(arguments)
        return await super().restart_deployment(**arguments)


async def assert_no_database_approval_claims(repository, incident_id):
    async with repository.pool.connection() as connection:
        for table in ("approval_decisions", "idempotency_keys", "incident_operation_claims"):
            # Table names are fixed internal constants, never request data.
            cursor = await connection.execute(
                f"SELECT count(*) AS count FROM {table} WHERE incident_id = %s", (incident_id,),
            )
            assert (await cursor.fetchone())["count"] == 0


def test_real_postgres_proposal_without_actual_interrupt_cannot_claim_approve_or_reject():
    async def scenario():
        tools = SimulationPreflightOnlyTools()
        async with postgres_runtime(tools) as context:
            response = await create_incident_stream(
                IncidentQuestion(question="Why is payment-service slow today?"), context.request,
            )
            try:
                async for raw in response.body_iterator:
                    if (parsed := parse_frame(raw)) is None:
                        continue
                    event, payload, _ = parsed
                    assert event == "incident"
                    proposal = await exact_retained(context, payload)
                    if proposal.status == "waiting_approval":
                        break
                else:
                    raise AssertionError("No retained proposal was delivered")
                snapshot = await context.runtime.graph.aget_state(context.runtime._config(proposal.incident_id))
                assert snapshot.next == ("human_approval",)
                assert not any(task.interrupts for task in snapshot.tasks)
                assert not await context.runtime.is_waiting_for_approval(proposal.incident_id)
                assert tools.preflights and not tools.dispatches
                preflights_before_approval = list(tools.preflights)
                for decision in ("approve", "reject"):
                    approval = ApprovalRequest(
                        decision=decision, actor="isolated-test-operator", reason="Must reject the pre-interrupt stream window",
                        idempotency_key=f"{proposal.incident_id}-early-{decision}",
                    )
                    with pytest.raises(HTTPException) as rejected:
                        await decide_remediation(proposal.incident_id, approval, context.request, Response())
                    assert rejected.value.status_code == 409
                    assert "No decision was claimed" in rejected.value.detail
                    with pytest.raises(GuardrailViolation, match="not waiting"):
                        await context.runtime.resume_with_approval(proposal.incident_id, approval)
                    await assert_no_database_approval_claims(context.repository, proposal.incident_id)
                    assert tools.preflights == preflights_before_approval
                assert (await context.runtime.get(proposal.incident_id)).model_dump(mode="json") == proposal.model_dump(mode="json")
                assert not tools.dispatches
                original_pid = context.pid
            finally:
                await response.body_iterator.aclose()
            assert tools.preflights == preflights_before_approval
            assert not await context.task_store.events_after(proposal.incident_id, 0)
        await fresh_read_only_restore(proposal, original_pid, terminal=False)
        tools_free = ToolFreeRecovery()
        async with postgres_runtime(tools_free) as restored:
            assert not await restored.runtime.is_waiting_for_approval(proposal.incident_id)
            await assert_no_database_approval_claims(restored.repository, proposal.incident_id)
            assert not tools_free.calls
        assert not tools.dispatches
    asyncio.run(scenario())
