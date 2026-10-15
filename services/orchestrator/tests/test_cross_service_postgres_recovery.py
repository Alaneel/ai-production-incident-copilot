"""Real PostgreSQL durability; synthetic telemetry, no model or Kubernetes.

The exception case checks a committed graph-node boundary, not a process crash.
Every opt-in writer requires a dedicated database on a private Unix socket.
"""
import asyncio
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.conninfo import conninfo_to_dict

from incident_copilot.approval import ApprovalReceiptSigner
from incident_copilot.dependency_topology import (
    DependencyEdge,
    ObservedCall,
    ObservedDependencies,
    ServiceTopology,
)
from incident_copilot.guardrails import GuardrailViolation
from incident_copilot.langgraph_workflow import LangGraphIncidentWorkflow
from incident_copilot.models import ApprovalRequest, Evidence, IncidentQuestion, IncidentState
from incident_copilot.store import PostgresIncidentRepository


class SyntheticCrossServiceTools:
    """Scoped timestamped fixtures, with every unexpected tool failing closed."""

    allow_simulated_signal_summaries = False
    allow_simulated_action_summaries = False
    allow_simulated_deployment_targets = False

    def __init__(self, *, ambiguous=False, now=None):
        self.now = now or datetime.now(UTC)
        self.ambiguous = ambiguous
        self.calls = []
        self.edges = [("checkout-api", "payment-service"), ("payment-service", "risk-engine")]

    async def get_service_topology(self, service):
        self.calls.append(("topology", service))
        edges = [DependencyEdge(source=source, target=target)
                 for source, target in self.edges if service in (source, target)]
        calls = [ObservedCall(
            source=edge.source, target=edge.target,
            trace_id=f"{self.edges.index((edge.source, edge.target)) + 1:032x}",
            client_span_id=f"{self.edges.index((edge.source, edge.target)) + 2:016x}",
            server_span_id=f"{self.edges.index((edge.source, edge.target)) + 4:016x}",
            observed_at=self.now - timedelta(seconds=30),
        ) for edge in edges]
        return ServiceTopology(
            service=service, edges=edges,
            dependencies=list(dict.fromkeys(edge.target if edge.source == service else edge.source for edge in edges)),
            source="service-catalog-via-telemetry-gateway", topology_kind="static_fixture_catalog",
            catalog_status="available", observed=ObservedDependencies(
                status="available", source="tempo_client_server_parentage", calls=calls,
                start=self.now - timedelta(minutes=2), end=self.now,
                coverage="sampled_not_exhaustive", traces_read=len(calls),
            ),
        )

    async def query_metrics(self, service, metrics):
        self.calls.append(("metrics", service, tuple(metrics)))
        values = {
            "checkout-api": {"request_latency_p95": 2500},
            "payment-service": {"request_latency_p95": 2300},
            "risk-engine": {"request_latency_p95": 2600, "cpu_utilization": .96},
        }
        if self.ambiguous:
            values["risk-engine"]["db_pool_active_ratio"] = 1
        assert service in values
        return [Evidence(
            kind="metric", service=service, source="synthetic-cross-service-postgres-test",
            title=metric, query=metric, observation="Synthetic timestamped observations, not live production telemetry",
            observed_at=self.now, score=.9, attributes={
                "data_origin": "synthetic", "unit": "milliseconds" if metric == "request_latency_p95" else "ratio",
                "series": [{"timestamp": (self.now - timedelta(seconds=offset)).isoformat(), "value": values[service][metric]}
                           for offset in (60, 45, 30, 15)],
            },
        ) for metric in metrics if metric in values[service]]

    async def search_logs(self, service, query):
        self.calls.append(("logs", service, query))
        return []

    async def query_traces(self, service):
        self.calls.append(("traces", service))
        return []

    async def search_incident_knowledge(self, service, query):
        self.calls.append(("knowledge", service, query))
        return [Evidence(
            kind="runbook", service=service, source="runbook://synthetic-postgres-test",
            title="Investigate retained local signals", observation=f"Synthetic investigative context: {query}",
            observed_at=self.now, score=.8,
        )]

    async def get_recent_deployments(self, service, namespace):
        self.calls.append(("deployments", service, namespace))
        return []

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)

        async def unexpected(*args, **kwargs):
            self.calls.append(("unexpected", name))
            raise AssertionError(f"Cross-service advisory must not call {name}, including dry-run")

        return unexpected


class ToolFreeRecovery:
    """A fresh reader cannot silently reconstruct state by querying tools."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)

        async def unexpected(*args, **kwargs):
            self.calls.append(name)
            raise AssertionError(f"Checkpoint read must not query {name}")

        return unexpected


class RetrievalBoundaryFailure(RuntimeError):
    pass


class FailingRetrievalTools(SyntheticCrossServiceTools):
    async def search_incident_knowledge(self, service, query):
        self.calls.append(("knowledge", service, query))
        raise RetrievalBoundaryFailure("Dedicated test interruption at the retrieval node")


def dedicated_url():
    url = os.environ["INCIDENT_TEST_DATABASE_URL"]
    connection = conninfo_to_dict(url)
    host = connection.get("host", "")
    assert host.startswith(("/private/tmp/", "/tmp/")) and "," not in host, "A single private Unix test socket is required"
    assert Path(host).resolve().is_relative_to(Path("/private/tmp")), "The socket directory must remain inside private temporary storage"
    assert connection.get("dbname", "").endswith("_tests"), "A dedicated _tests database is required"
    assert not connection.get("hostaddr") and not connection.get("service"), "Indirect database targets are forbidden"
    assert not any(os.getenv(key) for key in ("PGSERVICE", "PGSERVICEFILE", "PGHOSTADDR")), "Libpq environment target redirection is forbidden"
    return url


def workflow(tools, saver):
    return LangGraphIncidentWorkflow(
        tools=tools, checkpointer=saver,
        approval_signer=ApprovalReceiptSigner("dedicated-cross-service-postgres-test-secret"),
    )


async def backend_pid(saver):
    async with saver.conn.cursor() as cursor:
        await cursor.execute("SELECT pg_backend_pid() AS pid")
        return (await cursor.fetchone())["pid"]


def assert_candidate_evidence(state, *, ambiguous=False):
    assert state.complaint_service == state.services[0] == "checkout-api"
    assert state.incident_type is None
    assert state.diagnosis_service == (None if ambiguous else "risk-engine")
    assert state.root_cause_candidates
    assert {item.incident_type for item in state.root_cause_candidates} == (
        {"cpu_saturation", "database_connection_pool_exhaustion"} if ambiguous else {"cpu_saturation"}
    )
    evidence = {item.id: item for item in state.evidence}
    for candidate in state.root_cause_candidates:
        assert candidate.service == "risk-engine"
        assert candidate.propagation_path == ["checkout-api", "payment-service", "risk-engine"]
        assert candidate.path_basis == "observed_trace"
        assert candidate.temporal_overlap is True and candidate.causal_direction_verified is False
        assert candidate.local_evidence_ids and candidate.symptom_evidence_ids
        assert all(evidence[item_id].service == "risk-engine" for item_id in candidate.local_evidence_ids)
        assert all(evidence[item_id].service == "checkout-api" for item_id in candidate.symptom_evidence_ids)
        assert not set(candidate.local_evidence_ids + candidate.symptom_evidence_ids) & set(state.diagnostic_excluded_evidence_ids)


def assert_advisory(state, *, ambiguous=False):
    assert_candidate_evidence(state, ambiguous=ambiguous)
    assert state.status == "diagnosed"
    assert state.remediation.tool == "none"
    assert not state.remediation.requires_approval and not state.remediation.dry_run_passed
    assert state.approval_decision is None and state.approval_actor is None
    assert state.recovery is None
    assert len(state.hypotheses) == (2 if ambiguous else 1)
    for hypothesis in state.hypotheses:
        assert hypothesis.cause_service == "risk-engine"
        assert hypothesis.propagation_path == ["checkout-api", "payment-service", "risk-engine"]
        candidate = next(item for item in state.root_cause_candidates
                         if set(item.local_evidence_ids) & set(hypothesis.evidence_ids))
        assert set(candidate.symptom_evidence_ids) & set(hypothesis.evidence_ids)
    assert not any(event.event in {"human_decision", "execution_intent_prepared", "execution_acknowledged"}
                   for event in state.trace)


pytestmark = pytest.mark.skipif(
    not os.getenv("INCIDENT_TEST_DATABASE_URL"), reason="isolated PostgreSQL test URL required",
)


@pytest.mark.parametrize("ambiguous", [False, True], ids=["unique-source", "same-service-two-families"])
def test_cross_service_advisory_survives_fresh_postgres_saver_and_repository(ambiguous):
    async def scenario():
        url = dedicated_url()
        source_tools = SyntheticCrossServiceTools(ambiguous=ambiguous)
        async with AsyncPostgresSaver.from_conn_string(url) as saver:
            await saver.setup()
            original_pid = await backend_pid(saver)
            original = workflow(source_tools, saver)
            state = await original.start(IncidentQuestion(question="Why is checkout-api suddenly slow?"))
            assert_advisory(state, ambiguous=ambiguous)
            repository = await PostgresIncidentRepository.connect(url)
            try:
                await repository.save(state)
            finally:
                await repository.close()
            history = await original.history(state.incident_id)
            assert any(item.root_cause_candidates and not item.hypotheses for item in history)
            assert not (await original.graph.aget_state(original._config(state.incident_id))).next
        assert saver.conn.closed
        assert not any(call[0] == "unexpected" for call in source_tools.calls)

        fresh_tools = ToolFreeRecovery()
        async with AsyncPostgresSaver.from_conn_string(url) as new_saver:
            assert new_saver is not saver and new_saver.conn is not saver.conn
            assert await backend_pid(new_saver) != original_pid
            restored = workflow(fresh_tools, new_saver)
            retained = await restored.get(state.incident_id)
            assert retained.model_dump(mode="json") == state.model_dump(mode="json")
            assert_advisory(retained, ambiguous=ambiguous)
            assert not (await restored.graph.aget_state(restored._config(state.incident_id))).next
            repository = await PostgresIncidentRepository.connect(url)
            try:
                stored = await repository.get(state.incident_id)
                assert stored.model_dump(mode="json") == state.model_dump(mode="json")
            finally:
                await repository.close()
            with pytest.raises(GuardrailViolation, match="not waiting"):
                await restored.resume_with_approval(state.incident_id, ApprovalRequest(
                    decision="approve", actor="dedicated-test-operator", reason="Must reject approval of a completed advisory",
                    idempotency_key=f"{state.incident_id}-invalid-advisory-approval",
                ))
        assert not fresh_tools.calls
    asyncio.run(scenario())


def test_cross_service_candidate_node_checkpoint_resumes_after_retrieval_exception_on_new_postgres_connection():
    """A node exception and reconnect, explicitly not a SIGKILL simulation."""
    async def scenario():
        url = dedicated_url()
        interrupted_tools = FailingRetrievalTools()
        seed = IncidentState(question="Why is checkout-api suddenly slow?", environment="prod-sg")
        async with AsyncPostgresSaver.from_conn_string(url) as saver:
            await saver.setup()
            original_pid = await backend_pid(saver)
            interrupted = workflow(interrupted_tools, saver)
            with pytest.raises(RetrievalBoundaryFailure):
                await interrupted.graph.ainvoke(
                    {"incident": seed.model_dump(mode="json")},
                    config=interrupted._config(seed.incident_id), durability="sync",
                )
            snapshot = await interrupted.graph.aget_state(interrupted._config(seed.incident_id))
            assert snapshot.next == ("retrieve_context",)
            committed = await interrupted.get(seed.incident_id)
            assert_candidate_evidence(committed)
            assert not committed.hypotheses and committed.remediation is None
            assert committed.approval_decision is None and committed.recovery is None
            assert any(event.event == "cross_service_candidates_assessed" for event in committed.trace)
        assert saver.conn.closed

        resumed_tools = SyntheticCrossServiceTools(now=interrupted_tools.now)
        async with AsyncPostgresSaver.from_conn_string(url) as new_saver:
            assert await backend_pid(new_saver) != original_pid
            resumed = workflow(resumed_tools, new_saver)
            assert (await resumed.get(seed.incident_id)).model_dump(mode="json") == committed.model_dump(mode="json")
            output = await resumed.graph.ainvoke(None, config=resumed._config(seed.incident_id), durability="sync")
            final = IncidentState.model_validate(output["incident"])
            assert_advisory(final)
            assert final.root_cause_candidates == committed.root_cause_candidates
            assert final.service_topologies == committed.service_topologies
            assert final.service_investigations == committed.service_investigations
            assert final.trace[:len(committed.trace)] == committed.trace
            assert final.evidence[:len(committed.evidence)] == committed.evidence
            assert not (await resumed.graph.aget_state(resumed._config(seed.incident_id))).next
        assert resumed_tools.calls
        assert {call[0] for call in resumed_tools.calls} == {"knowledge", "deployments"}
        assert not any(call[0] == "unexpected" for call in interrupted_tools.calls)

        read_tools = ToolFreeRecovery()
        async with AsyncPostgresSaver.from_conn_string(url) as final_saver:
            assert (await workflow(read_tools, final_saver).get(seed.incident_id)).model_dump(mode="json") == final.model_dump(mode="json")
        assert not read_tools.calls
    asyncio.run(scenario())
