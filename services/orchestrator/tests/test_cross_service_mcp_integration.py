"""Compiled graph -> typed MCP -> ASGI registry -> mock Go HTTP gateway.

All telemetry is synthetic and all HTTP runs through ASGI/MockTransport. The
backend's explicit synthetic origin is retained; these tests are not live RCA,
independent provenance, model reasoning, or evidence of causal propagation.
"""

import asyncio
import json
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver

from incident_copilot.approval import ApprovalReceiptSigner
from incident_copilot.langgraph_workflow import LangGraphIncidentWorkflow
from incident_copilot.mcp_client import McpTelemetryTools
from incident_copilot.models import IncidentQuestion
from incident_mcp import server
from incident_mcp.gateway import TelemetryGatewayClient
from incident_mcp.security import ApprovalReceiptVerifier, TokenAuthenticator
from incident_mcp.tools import ToolRegistry


COMPLAINT = "payment-service"
FAULT = "risk-engine"
READ_TOKEN = "cross-service-mock-reader"
OPERATOR_TOKEN = "cross-service-mock-unused-operator"
GATEWAY_TOKEN = "cross-service-mock-gateway"
SECRET = "cross-service-mock-no-action-secret"
CPU_RUNBOOK = "runbook://platform/cpu-saturation"
NORMAL = {"request_latency_p95": 2400, "request_error_rate": 0.001,
          "db_pool_active_ratio": 0.3, "cpu_utilization": 0.1,
          "memory_working_set": 32 * 1024 * 1024, "kafka_consumer_lag": 0,
          "retry_rate": 0, "cache_miss_rate": 0}
UNITS = {"request_latency_p95": "milliseconds", "request_error_rate": "ratio",
         "db_pool_active_ratio": "ratio", "cpu_utilization": "ratio",
         "memory_working_set": "bytes", "kafka_consumer_lag": "messages",
         "retry_rate": "retries_per_second", "cache_miss_rate": "misses_per_second"}


class RecordingGoHttp:
    """Go gateway wire format without using the fake gateway Python shortcut."""

    def __init__(self, mode):
        self.mode = mode
        self.anchor = datetime.now(UTC) - timedelta(seconds=5)
        self.requests = []

    def __call__(self, request):
        assert request.url.host == "mock-go-gateway.test"
        assert request.headers["Authorization"] == f"Bearer {GATEWAY_TOKEN}"
        assert request.headers["X-Trace-ID"] == "mcp-gateway-1"
        payload = json.loads(request.content) if request.content else None
        self.requests.append({"method": request.method, "path": request.url.path,
                              "payload": payload})
        if request.url.path == "/v1/metrics/query":
            assert request.method == "POST"
            assert set(payload) == {"service", "metric", "start", "end", "step_seconds"}
            service, metric = payload["service"], payload["metric"]
            assert service in {COMPLAINT, FAULT} and metric in NORMAL
            start, end = (datetime.fromisoformat(payload[key]) for key in ("start", "end"))
            assert start < end and end - start <= timedelta(minutes=30)
            assert payload["step_seconds"] == (5 if metric == "memory_working_set" else 15)
            value = 0.95 if service == FAULT and metric == "cpu_utilization" else NORMAL[metric]
            result = {"service": service, "metric": metric, "unit": UNITS[metric],
                      "data_origin": "synthetic",
                      "points": [{"timestamp": (self.anchor - timedelta(seconds=offset)).isoformat(),
                                  "value": value} for offset in (60, 45, 30, 15, 0)]}
            if service == FAULT and metric == "cpu_utilization":
                if self.mode == "foreign_service":
                    result["service"] = "ledger-db"
                elif self.mode == "foreign_metric":
                    result["metric"] = "memory_working_set"
                elif self.mode == "wrong_unit":
                    result["unit"] = "seconds"
            return httpx.Response(200, json=result)
        if request.url.path in {"/v1/logs/search", "/v1/traces/search"}:
            assert request.method == "POST" and payload["service"] in {COMPLAINT, FAULT}
            assert datetime.fromisoformat(payload["start"]) < datetime.fromisoformat(payload["end"])
            service = payload["service"]
            if request.url.path.endswith("/traces/search"):
                return httpx.Response(200, json={"service": service, "count": 0, "traces": []})
            message = "downstream request timed out" if service == COMPLAINT else "CPU worker saturation"
            return httpx.Response(200, json={"service": service, "count": 1, "events": [{
                "timestamp": self.anchor.isoformat(), "level": "ERROR", "message": message,
                "fields": {"request_id": "mock-cross-service-request", "trace_id": "1" * 32,
                           "span_id": "2" * 16 if service == COMPLAINT else "3" * 16}}]})
        assert request.method == "GET"
        parts = request.url.path.split("/")
        assert parts[:3] == ["", "v1", "services"] and len(parts) == 5
        service, operation = parts[3:]
        assert service in {COMPLAINT, FAULT}
        if operation == "deployments":
            return httpx.Response(200, json={"service": service, "deployments": []})
        if operation == "dependencies":
            edge = ({"source": FAULT, "target": COMPLAINT} if self.mode == "reverse"
                    else {"source": COMPLAINT, "target": FAULT})
            return httpx.Response(200, json={"service": service,
                "edges": [] if self.mode == "stale_parentage_only" else [edge],
                "topology_kind": "static_fixture_catalog"})
        assert operation == "observed-dependencies"
        observed = self.mode in {"catalog_and_parentage", "stale_parentage_only"}
        return httpx.Response(200, json={"service": service, "source": "tempo_client_server_parentage",
            "start": (self.anchor - timedelta(minutes=30)).isoformat(), "end": self.anchor.isoformat(),
            "coverage": "sampled_not_exhaustive", "traces_read": int(observed), "traces_failed": 0,
            "calls_omitted": 0, "calls": [{"source": COMPLAINT, "target": FAULT,
                "trace_id": "1" * 32, "client_span_id": "2" * 16, "server_span_id": "3" * 16,
                "observed_at": (self.anchor - timedelta(minutes=15) if self.mode == "stale_parentage_only"
                                else self.anchor).isoformat()}] if observed else []})


class NoActionBackend:
    """Any real-handler preflight or mutation is a failing, recorded attempt."""

    def __init__(self):
        self.calls = []

    async def preflight(self, **arguments):
        self.calls.append({"operation": "preflight", **arguments})
        raise AssertionError("Cross-service diagnostics must not preflight remediation.")

    async def restart(self, **arguments):
        self.calls.append({"operation": "restart", **arguments})
        raise AssertionError("Cross-service diagnostics must not restart deployments.")

    async def rollback(self, **arguments):
        self.calls.append({"operation": "rollback", **arguments})
        raise AssertionError("Cross-service diagnostics must not roll back releases.")


@asynccontextmanager
async def cross_service_harness(monkeypatch, mode):
    # Patches are test-local and pytest restores the server's original globals.
    # Fresh rate state avoids contaminating another integration test's window.
    monkeypatch.setattr(server, "authenticator", TokenAuthenticator(READ_TOKEN, OPERATOR_TOKEN))
    monkeypatch.setattr(server, "_rate_windows", defaultdict(deque))
    monkeypatch.setattr(server, "_rate_lock", asyncio.Lock())
    go_http, backend, rpc = RecordingGoHttp(mode), NoActionBackend(), []

    async def record_rpc(request):
        await request.aread()
        rpc.append({"method": request.method, "path": request.url.path,
                    "authorization": request.headers.get("Authorization"),
                    "message": json.loads(request.content)})

    async with httpx.AsyncClient(transport=httpx.MockTransport(go_http)) as gateway_http:
        gateway = TelemetryGatewayClient(base_url="https://mock-go-gateway.test", token=GATEWAY_TOKEN,
                                         max_attempts=1, client=gateway_http)
        registry = ToolRegistry(ApprovalReceiptVerifier(SECRET), gateway=gateway, remediator=backend)
        monkeypatch.setattr(server, "registry", registry)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),
                                   event_hooks={"request": [record_rpc]}) as rpc_http:
            tools = McpTelemetryTools(url="http://mock-mcp.test/mcp", read_token=READ_TOKEN,
                operator_token=OPERATOR_TOKEN, max_attempts=1, client=rpc_http)
            workflow = LangGraphIncidentWorkflow(tools=tools, checkpointer=InMemorySaver(),
                approval_signer=ApprovalReceiptSigner(SECRET), hypothesis_generator=None)
            yield SimpleNamespace(workflow=workflow, tools=tools, gateway=gateway, registry=registry,
                                  go_http=go_http, backend=backend, rpc=rpc)


def assert_read_only_wire(harness, state, caplog):
    assert isinstance(harness.tools, McpTelemetryTools)
    assert isinstance(harness.gateway, TelemetryGatewayClient)
    assert harness.workflow.hypothesis_generator is None
    assert state.status == "diagnosed" and state.approval_decision is None
    assert state.remediation.tool == "none" and state.remediation.requires_approval is False
    assert state.remediation.dry_run_passed is False
    assert state.services[0] == state.complaint_service == COMPLAINT
    assert state.incident_type == "downstream_timeout"
    assert harness.rpc and all(item["method"] == "POST" and item["path"] == "/mcp"
                               and item["authorization"] == f"Bearer {READ_TOKEN}" for item in harness.rpc)
    calls = [item["message"]["params"] for item in harness.rpc if item["message"]["method"] == "tools/call"]
    assert calls and all(harness.registry.tools[item["name"]].read_only for item in calls)
    assert not {"restart_deployment", "rollback_release", "query_operation_status", "query_deployment_health"} & {item["name"] for item in calls}
    assert all(item["arguments"]["service"] in {COMPLAINT, FAULT} for item in calls)
    assert harness.backend.calls == []
    assert harness.registry._executions == harness.registry._execution_bindings == {}
    for event in state.trace:
        if event.event == "tool_call":
            assert event.payload["read_only"] is True
    assert not any(event.event in {"model_call", "approval_recorded", "execution_intent_prepared"} for event in state.trace)
    audit = [json.loads(record.message) for record in caplog.records
             if record.name == "incident-mcp" and json.loads(record.message).get("tool")]
    assert len(audit) == len(calls)
    assert all(event["read_only"] is True and event["principal"] == "incident-agent" for event in audit)
    requests = harness.go_http.requests
    assert requests and all(request["method"] in {"GET", "POST"} for request in requests)
    get_paths = {request["path"] for request in requests if request["method"] == "GET"}
    assert f"/v1/services/{COMPLAINT}/dependencies" in get_paths
    assert f"/v1/services/{COMPLAINT}/observed-dependencies" in get_paths
    assert all(request["path"] in {"/v1/metrics/query", "/v1/logs/search", "/v1/traces/search"}
               for request in requests if request["method"] == "POST")
    return calls


@pytest.mark.parametrize("mode,basis", [("catalog", "catalog"), ("catalog_and_parentage", "observed_trace")])
def test_actual_mcp_gateway_wire_correlates_dependency_cpu_without_action(monkeypatch, caplog, mode, basis):
    caplog.set_level("INFO", logger="incident-mcp")

    async def scenario():
        async with cross_service_harness(monkeypatch, mode) as harness:
            state = await harness.workflow.start(IncidentQuestion(
                question="Why is payment-service suddenly slow today?", environment="prod-sg"))
            calls = assert_read_only_wire(harness, state, caplog)
            remote = [candidate for candidate in state.root_cause_candidates if candidate.service != COMPLAINT]
            assert len(remote) == 1
            candidate, = remote
            assert candidate.service == state.diagnosis_service == FAULT
            assert candidate.incident_type == "cpu_saturation"
            assert candidate.propagation_path == [COMPLAINT, FAULT] and candidate.path_basis == basis
            assert candidate.temporal_overlap is True and candidate.causal_direction_verified is False
            evidence = {item.id: item for item in state.evidence}
            assert candidate.local_evidence_ids and all(evidence[key].service == FAULT for key in candidate.local_evidence_ids)
            assert candidate.symptom_evidence_ids and all(evidence[key].service == COMPLAINT for key in candidate.symptom_evidence_ids)
            cpu = [evidence[key] for key in candidate.local_evidence_ids if evidence[key].query == "cpu_utilization"]
            assert len(cpu) == 1 and cpu[0].attributes["unit"] == "ratio"
            assert cpu[0].attributes["data_origin"] == "synthetic"
            assert len(cpu[0].attributes["series"]) == 5
            assert all(point["value"] == 0.95 for point in cpu[0].attributes["series"])
            hypothesis, = state.hypotheses
            assert hypothesis.cause_service == FAULT and hypothesis.propagation_path == [COMPLAINT, FAULT]
            assert hypothesis.causal_direction_verified is False
            assert set(candidate.local_evidence_ids) <= set(hypothesis.evidence_ids)
            assert any(item.source == CPU_RUNBOOK and item.service == FAULT for item in state.evidence)
            assert any(item["name"] == "search_incident_knowledge" and item["arguments"]["service"] == FAULT
                       and "cpu" in item["arguments"]["query"].lower() for item in calls)
            assert any(item["name"] == "get_service_dependencies" and item["arguments"]["service"] == FAULT for item in calls)
            assert {f"/v1/services/{FAULT}/{operation}" for operation in
                    ("dependencies", "observed-dependencies", "deployments")} <= {
                        request["path"] for request in harness.go_http.requests if request["method"] == "GET"}
            if mode == "catalog_and_parentage":
                assert state.service_topology.observed.coverage == "sampled_not_exhaustive"
                call, = state.service_topology.observed.calls
                assert (call.trace_id, call.client_span_id, call.server_span_id) == ("1" * 32, "2" * 16, "3" * 16)
            retained = await harness.workflow.get(state.incident_id)
            assert retained.model_dump() == state.model_dump()
            assert not (await harness.workflow.graph.aget_state(harness.workflow._config(state.incident_id))).next

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["reverse", "stale_parentage_only", "foreign_service", "foreign_metric", "wrong_unit"])
def test_wire_direction_freshness_and_metric_identity_cannot_fabricate_remote_root(monkeypatch, caplog, mode):
    caplog.set_level("INFO", logger="incident-mcp")

    async def scenario():
        async with cross_service_harness(monkeypatch, mode) as harness:
            state = await harness.workflow.start(IncidentQuestion(
                question="Why is payment-service suddenly slow today?", environment="prod-sg"))
            assert_read_only_wire(harness, state, caplog)
            assert not [candidate for candidate in state.root_cause_candidates if candidate.service != COMPLAINT]
            assert not [hypothesis for hypothesis in state.hypotheses if hypothesis.cause_service not in {None, COMPLAINT}]
            assert state.diagnosis_service != FAULT
            if mode.startswith("foreign"):
                assert not [item for item in state.evidence if item.service == FAULT and item.query == "cpu_utilization"
                            and item.attributes.get("series")]
                assert any(event.event == "tool_error" and event.payload.get("service") == FAULT for event in state.trace)
            elif mode == "stale_parentage_only":
                assert state.service_topology.edges == []
                call, = state.service_topology.observed.calls
                assert call.observed_at < harness.go_http.anchor - timedelta(minutes=5)
            elif mode == "wrong_unit":
                cpu, = [item for item in state.evidence if item.service == FAULT and item.query == "cpu_utilization"]
                assert cpu.attributes["unit"] == "seconds"
                coverage = next(event.payload for event in state.trace if event.event == "cross_service_diagnostic_coverage_assessed")
                assert coverage["complete"] is False
                assert coverage["reasons"] == [{"service": FAULT, "reason": "downstream_metric_unit_invalid"}]
            else:
                assert state.service_topology.relation(FAULT) == "upstream_catalog_dependency"

    asyncio.run(scenario())
