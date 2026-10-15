"""Compiled graph -> typed MCP -> ASGI -> MockTransport Kubernetes recovery.

Every backend observation is a recording fixture. The Prometheus origin label
exercises the trust boundary; it is NOT independent live provenance or evidence
that an action causally restored a real service. No network or paid model runs.
"""

import asyncio
import json
import os
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.conninfo import conninfo_to_dict

from incident_copilot import action_applicability, langgraph_workflow, mcp_client
from incident_copilot.approval import ApprovalReceiptSigner
from incident_copilot.guardrails import GuardrailViolation
from incident_copilot.langgraph_workflow import LangGraphIncidentWorkflow
from incident_copilot.mcp_client import McpTelemetryTools
from incident_copilot.models import ApprovalRequest, IncidentQuestion
from incident_copilot.store import PostgresIncidentRepository
from incident_mcp import kubernetes, server
from incident_mcp.kubernetes import KubernetesRemediator
from incident_mcp.security import ApprovalReceiptVerifier, TokenAuthenticator
from incident_mcp.target_context import target_context_digest
from incident_mcp.tools import ToolRegistry


SERVICE = "risk-engine"
NAMESPACE = "prod-sg"
SECRET = "mock-mcp-recovery-test-only-secret"
READ_TOKEN = "mock-recovery-reader"
OPERATOR_TOKEN = "mock-recovery-operator"
OPERATION_KEY = "mock-recovery-original-operation"


class RecordingGateway:
    """Deterministic metric fixture with explicit backend-asserted origin."""

    def __init__(self):
        self.origin = "prometheus_api"
        self.acknowledgement = None
        self.executed = False
        self.calls = []

    async def query_metrics(self, *, service, metric, start, end, step_seconds):
        self.calls.append({"service": service, "metric": metric, "start": start,
                           "end": end, "step_seconds": step_seconds})
        values = {"request_latency_p95": 2400, "request_error_rate": 0.01,
                  "db_pool_active_ratio": 0.3, "cpu_utilization": 0.95,
                  "memory_working_set": 32 * 1024 * 1024, "kafka_consumer_lag": 0,
                  "retry_rate": 0, "cache_miss_rate": 0}
        units = {"request_latency_p95": "milliseconds", "request_error_rate": "ratio",
                 "db_pool_active_ratio": "ratio", "cpu_utilization": "ratio",
                 "memory_working_set": "bytes", "kafka_consumer_lag": "messages",
                 "retry_rate": "retries_per_second", "cache_miss_rate": "misses_per_second"}
        if self.executed:
            values.update(request_latency_p95=150, cpu_utilization=0.1)
        times = ([self.acknowledgement + timedelta(seconds=seconds) for seconds in (5, 20, 35)]
                 if self.acknowledgement is not None
                 else [end - timedelta(seconds=seconds) for seconds in (60, 45, 30, 15, 0)])
        result = {"service": service, "metric": metric, "unit": units[metric],
                  "points": [{"timestamp": time.isoformat(), "value": values[metric]}
                             for time in times if start <= time <= end]}
        if self.origin is not None:
            result["data_origin"] = self.origin
        return result

    async def search_logs(self, *, service, end, **kwargs):
        return {"service": service, "events": [{"timestamp": end.isoformat(), "level": "ERROR",
                "message": "CPU throttling under saturated risk-engine worker pool", "fields": {}}]}

    async def search_traces(self, *, service, end, **kwargs):
        return {"service": service, "traces": [{"trace_id": "1" * 32, "root_service_name": service,
                "root_trace_name": "POST /v1/work", "started_at": end.isoformat(), "duration_ms": 2400}]}

    async def dependencies(self, service):
        return {"service": service, "edges": [], "topology_kind": "static_fixture_catalog"}

    async def deployments(self, service):
        return {"service": service, "deployments": []}


class RecordingKubernetes:
    def __init__(self, gateway):
        self.gateway = gateway
        self.requests = []
        self.current = {
            "metadata": {"name": SERVICE, "namespace": NAMESPACE, "uid": "mock-recovery-deployment",
                         "resourceVersion": "40", "generation": 10},
            "spec": {"replicas": 1, "template": {"metadata": {"annotations": {}},
                     "spec": {"containers": [{"name": "risk", "image": "risk:test-v142"}]}}},
            "status": {"observedGeneration": 10, "replicas": 1, "updatedReplicas": 1,
                       "readyReplicas": 1, "availableReplicas": 1},
        }

    def __call__(self, request):
        self.requests.append(request)
        assert request.url.path == f"/apis/apps/v1/namespaces/{NAMESPACE}/deployments/{SERVICE}"
        assert request.method in {"GET", "PATCH"}
        if request.method == "GET":
            return httpx.Response(200, json=self.current)
        patch = json.loads(request.content)
        assert patch["metadata"]["resourceVersion"] == self.current["metadata"]["resourceVersion"]
        candidate = deepcopy(self.current)
        candidate["spec"]["template"]["metadata"]["annotations"].update(patch["spec"]["template"]["metadata"]["annotations"])
        candidate["metadata"].update(resourceVersion="41", generation=11)
        if request.url.params.get("dryRun") != "All":
            candidate["status"].update(updatedReplicas=0, readyReplicas=0, availableReplicas=0, unavailableReplicas=1)
            self.current = candidate
            self.gateway.executed = True
        return httpx.Response(200, json=candidate)

    def mark_rollout_ready(self):
        self.current["status"].update(observedGeneration=11, replicas=1,
                                     updatedReplicas=1, readyReplicas=1, availableReplicas=1)
        # Match real Kubernetes int32 omitempty, not a fake-required zero field.
        self.current["status"].pop("unavailableReplicas", None)

    @property
    def persistent_patches(self):
        return [request for request in self.requests if request.method == "PATCH"
                and request.url.params.get("dryRun") != "All"]


@asynccontextmanager
async def recovery_harness(monkeypatch):
    clock = [datetime.now(UTC)]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            # Advance the internal recording clock, never sleep or change real
            # time. This keeps immediate observation windows strictly nonempty.
            clock[0] += timedelta(milliseconds=1)
            return clock[0].astimezone(tz) if tz else clock[0].replace(tzinfo=None)

    for module in (langgraph_workflow, mcp_client, action_applicability, kubernetes):
        monkeypatch.setattr(module, "datetime", Clock)
    monkeypatch.setattr(server, "authenticator", TokenAuthenticator(READ_TOKEN, OPERATOR_TOKEN))
    monkeypatch.setattr(server, "_rate_windows", defaultdict(deque))
    monkeypatch.setattr(server, "_rate_lock", asyncio.Lock())
    gateway = RecordingGateway()
    api = RecordingKubernetes(gateway)
    rpc = []

    async def record_rpc(request):
        await request.aread()
        rpc.append(json.loads(request.content))

    async with httpx.AsyncClient(base_url="https://kubernetes.test", transport=httpx.MockTransport(api)) as kube_http:
        backend = KubernetesRemediator(api_url="https://kubernetes.test", allowed_namespaces={NAMESPACE}, client=kube_http)
        def fresh_registry():
            return ToolRegistry(ApprovalReceiptVerifier(SECRET), gateway=gateway, remediator=backend)
        monkeypatch.setattr(server, "registry", fresh_registry())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),
                                   event_hooks={"request": [record_rpc]}) as rpc_http:
            saver = InMemorySaver()
            def factory(checkpointer=None):
                tools = McpTelemetryTools(url="http://mcp.test/mcp", read_token=READ_TOKEN,
                    operator_token=OPERATOR_TOKEN, client=rpc_http, max_attempts=1)
                return LangGraphIncidentWorkflow(tools=tools, checkpointer=checkpointer if checkpointer is not None else saver,
                    approval_signer=ApprovalReceiptSigner(SECRET))
            yield SimpleNamespace(factory=factory, fresh_registry=fresh_registry, clock=clock,
                                  gateway=gateway, api=api, backend=backend, rpc=rpc)


async def start_and_approve(harness, workflow=None):
    workflow = workflow or harness.factory()
    paused = await workflow.start(IncidentQuestion(question="Investigate risk-engine CPU saturation"))
    assert paused.status == "waiting_approval" and paused.services[0] == SERVICE
    assert paused.remediation.tool == "restart_deployment"
    assert paused.remediation.preflight_mode == "kubernetes-server-dry-run"
    context = paused.remediation.arguments["target_context"]
    assert context["deployment_uid"] == "mock-recovery-deployment" and context["resource_version"] == "40"
    assert harness.api.current["spec"]["template"]["metadata"]["annotations"] == {}
    assert len(harness.api.persistent_patches) == 0
    assert sum(request.url.params.get("dryRun") == "All" for request in harness.api.requests) == 1
    approval_checkpoint = await workflow.graph.aget_state(workflow._config(paused.incident_id))
    assert approval_checkpoint.interrupts[0].value["arguments"]["target_context"] == context
    pending = await workflow.resume_with_approval(paused.incident_id, ApprovalRequest(
        decision="approve", actor="mock-recovery-oncall", reason="Mock transport integration, not a real production action",
        idempotency_key=OPERATION_KEY))
    assert pending.status == "remediation_pending" and pending.recovery["status"] == "pending"
    assert pending.recovery["simulation"] is False
    assert pending.recovery["applied_generation"] == 11
    assert pending.recovery["applied_template_sha256"] == harness.backend._digest(harness.api.current["spec"]["template"])
    assert len(harness.api.persistent_patches) == 1
    snapshot = await workflow.graph.aget_state(workflow._config(paused.incident_id))
    assert snapshot.next == ("wait_for_recovery",)
    assert snapshot.interrupts[0].value["kind"] == "recovery_check"
    assert not any(event.event == "workflow_completed" for event in pending.trace)
    dispatch = next(message["params"]["arguments"] for message in harness.rpc
                    if message["method"] == "tools/call" and message["params"]["name"] == "restart_deployment"
                    and message["params"]["arguments"]["dry_run"] is False)
    receipt = ApprovalReceiptVerifier(SECRET).verify(dispatch["approval_receipt"], tool="restart_deployment",
        service=SERVICE, namespace=NAMESPACE, target=NAMESPACE, target_context_sha256=target_context_digest(context))
    assert receipt["version"] == 3 and receipt["actor"] == "mock-recovery-oncall"
    assert dispatch["target_context"] == context and dispatch["idempotency_key"] == OPERATION_KEY
    intent = next(event.payload for event in pending.trace if event.event == "execution_intent_prepared")
    assert intent["arguments"] == paused.remediation.arguments and intent["idempotency_key"] == OPERATION_KEY
    assert "apr_" not in pending.model_dump_json()
    return workflow, pending


def prepare_observations(harness, pending):
    acknowledgement = datetime.fromisoformat(pending.recovery["execution_acknowledged_at"])
    harness.gateway.acknowledgement = acknowledgement
    harness.clock[0] = acknowledgement + timedelta(seconds=45)
    harness.api.mark_rollout_ready()


def test_compiled_recovery_restores_checkpoint_uses_fresh_mcp_registry_and_never_repatches(monkeypatch, caplog):
    caplog.set_level("INFO", logger="incident-mcp")
    async def scenario():
        async with recovery_harness(monkeypatch) as harness:
            _, pending = await start_and_approve(harness)
            prepare_observations(harness, pending)
            registry = harness.fresh_registry()
            monkeypatch.setattr(server, "registry", registry)
            assert registry._executions == registry._execution_bindings == {}
            restored = harness.factory()
            assert (await restored.get(pending.incident_id)).model_dump() == pending.model_dump()
            before = len(harness.api.requests)
            rpc_before = len(harness.rpc)
            resolved = await restored.recheck_recovery(pending.incident_id)
            assert resolved.status == "resolved" and resolved.recovery["status"] == "verified"
            assert resolved.recovery["reason_codes"] == ["rollout_and_sli_recovery_observed_not_causal_proof"]
            assert harness.api.requests[before:] and all(request.method == "GET" for request in harness.api.requests[before:])
            assert len(harness.api.persistent_patches) == 1
            assert registry._executions == registry._execution_bindings == {}
            actual_tools = [message["params"]["name"] for message in harness.rpc[rpc_before:] if message["method"] == "tools/call"]
            assert sorted(actual_tools) == ["query_deployment_health", "query_metrics", "query_metrics"]
            audit = [json.loads(record.message) for record in caplog.records if record.name == "incident-mcp"]
            health_calls = [event for event in audit if event["tool"] == "query_deployment_health"]
            assert len(health_calls) == 2
            assert all(event["read_only"] is True and event["principal"] == "incident-operator"
                       and event["outcome"] == "success" for event in health_calls)
            recovery_ids = set(resolved.recovery["metric_evidence_ids"])
            metrics = [item for item in resolved.evidence if item.id in recovery_ids]
            assert len(metrics) == 2 and {item.query for item in metrics} == {"request_latency_p95", "request_error_rate"}
            assert all(item.attributes["data_origin"] == "prometheus_api" and item.attributes["discarded_samples"] == 0 for item in metrics)
            acknowledged = datetime.fromisoformat(resolved.recovery["execution_acknowledged_at"])
            assert all(len(item.attributes["series"]) == 3 and all(
                datetime.fromisoformat(point["timestamp"]) > acknowledged for point in item.attributes["series"]) for item in metrics)
            assert all(call["start"] == acknowledged for call in harness.gateway.calls[-2:])
            assert not (await restored.graph.aget_state(restored._config(pending.incident_id))).next
            with pytest.raises(GuardrailViolation, match="waiting"):
                await harness.factory().recheck_recovery(pending.incident_id)
            assert len(harness.api.persistent_patches) == 1
            assert "apr_" not in resolved.model_dump_json()
    asyncio.run(scenario())


@pytest.mark.parametrize("failure,reason", [
    ("missing_origin", "metric_observation_untrusted"),
    ("synthetic_origin", "metric_observation_untrusted"),
    ("replacement_uid", "observation_unavailable"),
    ("generation_drift", "deployment_applied_target_mismatch"),
    ("old_replicas_retained", "deployment_rollout_incomplete"),
])
def test_cross_layer_negative_observations_remain_pending_without_redispatch(monkeypatch, failure, reason):
    async def scenario():
        async with recovery_harness(monkeypatch) as harness:
            _, pending = await start_and_approve(harness)
            prepare_observations(harness, pending)
            if failure == "missing_origin":
                harness.gateway.origin = None
            elif failure == "synthetic_origin":
                harness.gateway.origin = "synthetic"
            elif failure == "replacement_uid":
                harness.api.current["metadata"]["uid"] = "replacement-deployment"
            elif failure == "generation_drift":
                harness.api.current["metadata"]["generation"] = 12
                harness.api.current["status"]["observedGeneration"] = 12
            elif failure == "old_replicas_retained":
                harness.api.current["status"]["replicas"] = 2
            monkeypatch.setattr(server, "registry", harness.fresh_registry())
            restored = harness.factory()
            assert (await restored.get(pending.incident_id)).model_dump() == pending.model_dump()
            before = len(harness.api.requests)
            still_pending = await restored.recheck_recovery(pending.incident_id)
            assert still_pending.status == "remediation_pending" and still_pending.recovery["status"] == "pending"
            assert reason in still_pending.recovery["reason_codes"]
            assert (await restored.graph.aget_state(restored._config(pending.incident_id))).next == ("wait_for_recovery",)
            assert len(harness.api.persistent_patches) == 1
            assert all(request.method == "GET" for request in harness.api.requests[before:])
            if failure in {"missing_origin", "synthetic_origin"}:
                metrics = [item for item in still_pending.evidence if item.query in {"request_latency_p95", "request_error_rate"}]
                assert {item.attributes["data_origin"] for item in metrics[-2:]} == {"unknown" if failure == "missing_origin" else "synthetic"}
            assert "apr_" not in still_pending.model_dump_json()
    asyncio.run(scenario())


@pytest.mark.skipif(not os.getenv("INCIDENT_TEST_DATABASE_URL"), reason="isolated PostgreSQL test URL required")
def test_postgres_recovery_interrupt_survives_closed_saver_connections_without_mock_redispatch(monkeypatch):
    """Real PostgreSQL checkpoints/repository, still entirely mocked Kubernetes."""
    async def scenario():
        url = os.environ["INCIDENT_TEST_DATABASE_URL"]
        connection = conninfo_to_dict(url)
        # This opt-in test writes checkpoints. Refuse an unspecified/nonlocal
        # database, independently of whatever normal app URLs are configured.
        assert connection.get("host", "").startswith(("/private/tmp/", "/tmp/")), "A private Unix test socket is required"
        assert connection.get("dbname", "").endswith("_tests"), "A dedicated _tests database is required"
        async with recovery_harness(monkeypatch) as harness:
            async with AsyncPostgresSaver.from_conn_string(url) as saver:
                await saver.setup()
                workflow, pending = await start_and_approve(harness, harness.factory(saver))
                history = await workflow.history(pending.incident_id)
                prepared = next(state for state in history if state.current_step == "prepare_execution")
                assert any(event.event == "execution_intent_prepared" for event in prepared.trace)
                assert not any(event.step == "execute_or_simulate" for event in prepared.trace)
                repository = await PostgresIncidentRepository.connect(url)
                try:
                    await repository.save(pending)
                finally:
                    await repository.close()
            prepare_observations(harness, pending)
            fresh_registry = harness.fresh_registry()
            monkeypatch.setattr(server, "registry", fresh_registry)
            assert fresh_registry._executions == fresh_registry._execution_bindings == {}
            before = len(harness.api.requests)
            async with AsyncPostgresSaver.from_conn_string(url) as new_saver:
                restored = harness.factory(new_saver)
                assert (await restored.get(pending.incident_id)).model_dump() == pending.model_dump()
                snapshot = await restored.graph.aget_state(restored._config(pending.incident_id))
                assert snapshot.next == ("wait_for_recovery",)
                assert snapshot.interrupts[0].value["kind"] == "recovery_check"
                resolved = await restored.recheck_recovery(pending.incident_id)
                assert resolved.status == "resolved" and resolved.recovery["status"] == "verified"
                assert resolved.recovery["applied_generation"] == pending.recovery["applied_generation"]
                assert resolved.recovery["applied_template_sha256"] == pending.recovery["applied_template_sha256"]
                repository = await PostgresIncidentRepository.connect(url)
                try:
                    assert (await repository.get(pending.incident_id)).model_dump() == pending.model_dump()
                    await repository.save(resolved)
                finally:
                    await repository.close()
            assert len(harness.api.persistent_patches) == 1
            assert all(request.method == "GET" for request in harness.api.requests[before:])
            rpc_before = len(harness.rpc)
            async with AsyncPostgresSaver.from_conn_string(url) as final_saver:
                final = harness.factory(final_saver)
                assert (await final.get(pending.incident_id)).model_dump() == resolved.model_dump()
                assert not (await final.graph.aget_state(final._config(pending.incident_id))).next
            assert len(harness.rpc) == rpc_before  # Restoring finalized state is tool-free.
            assert "apr_" not in resolved.model_dump_json()
    asyncio.run(scenario())
