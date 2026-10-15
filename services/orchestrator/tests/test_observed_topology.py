import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver

from incident_copilot.approval import ApprovalReceiptSigner
from incident_copilot.dependency_topology import parse_observed, parse_topology
from incident_copilot.langgraph_workflow import LangGraphIncidentWorkflow
from incident_copilot.mcp_client import McpTelemetryTools
from incident_copilot.models import IncidentQuestion
from incident_copilot.tools import DemoTelemetryTools
from incident_mcp.gateway import TelemetryGatewayClient
from incident_mcp.server import _rate_windows, app, registry


def observed():
    now = datetime.now(UTC)
    return {"status": "available", "data": {
        "service": "payment-service", "source": "tempo_client_server_parentage",
        "start": (now - timedelta(minutes=30)).isoformat(), "end": now.isoformat(),
        "coverage": "sampled_not_exhaustive", "traces_read": 1, "traces_failed": 0, "calls_omitted": 0,
        "calls": [{"source": "payment-service", "target": "observed-ledger",
                   "trace_id": "1" * 32, "client_span_id": "2" * 16, "server_span_id": "3" * 16,
                   "observed_at": (now - timedelta(minutes=1)).isoformat(), "private_note": "secret"}],
    }}


def catalog():
    return {"service": "payment-service", "source": "service-catalog-via-telemetry-gateway",
            "topology_kind": "static_fixture_catalog", "edges": [{"source": "catalog-only", "target": "payment-service"}],
            "observed": observed()}


def test_observed_services_prioritized_without_upgrading_catalog_or_causality():
    topology = parse_topology("payment-service", catalog())
    assert topology.dependencies == ["observed-ledger", "catalog-only"]
    assert topology.relation("observed-ledger") == "downstream_observed_call"
    assert topology.relation("catalog-only") == "upstream_catalog_dependency"
    assert topology.topology_kind == "static_fixture_catalog"
    assert topology.observed.calls[0].trace_id == "1" * 32
    assert "secret" not in topology.model_dump_json()


@pytest.mark.parametrize("change", ["wrong-service", "source", "window", "future", "coverage", "counts", "oversized", "cross-trace-count"])
def test_invalid_observation_envelopes_cannot_create_real_edges(change):
    raw = observed()
    data = raw["data"]
    if change == "wrong-service":
        data["service"] = "wrong-service"
    if change == "source":
        data["source"] = "private URL"
    if change == "window":
        data["start"] = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    if change == "future":
        data["end"] = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    if change == "coverage":
        data["coverage"] = "exhaustive"
    if change == "counts":
        data["traces_read"] = True
    if change == "oversized":
        data["calls"] *= 65
    if change == "cross-trace-count":
        extra = deepcopy(data["calls"][0])
        extra["trace_id"] = "4" * 32
        data["calls"].append(extra)
    normalized = parse_observed("payment-service", raw)
    assert normalized.status == "unavailable"
    assert normalized.calls == []


def test_duplicate_or_invalid_calls_are_discarded_not_resolved_by_first_winner():
    raw = observed()
    raw["data"]["calls"] += [deepcopy(raw["data"]["calls"][0]), {
        **raw["data"]["calls"][0], "client_span_id": "0" * 16,
    }]
    result = parse_observed("payment-service", raw)
    assert result.calls == []
    assert result.calls_discarded == 3


def test_empty_and_partial_results_do_not_become_exhaustive_or_healthy():
    raw = observed()
    raw["data"].update(calls=[], traces_read=0, traces_failed=1, coverage="partial_sample_not_exhaustive")
    result = parse_observed("payment-service", raw)
    assert result.status == "available" and result.calls == []
    assert result.coverage == "partial_sample_not_exhaustive"
    assert result.traces_failed == 1
    assert parse_observed("payment-service", None).status == "not_requested"


def test_mcp_gateway_workflow_path_preserves_observed_evidence_and_read_only_calls():
    async def scenario():
        requests = []
        envelope = observed()
        async def backend(request):
            requests.append((request.method, request.url.path))
            assert request.headers["authorization"] == "Bearer test-gateway-token"
            if request.url.path.endswith("/observed-dependencies"):
                return httpx.Response(200, json=envelope["data"])
            return httpx.Response(200, json={"service": "payment-service", "edges": catalog()["edges"], "topology_kind": "static_fixture_catalog"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(backend)) as backend_client:
            gateway = TelemetryGatewayClient(base_url="http://gateway.test", token="test-gateway-token", client=backend_client)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://mcp.test") as mcp_client:
                _rate_windows.clear()
                adapter = McpTelemetryTools(url="http://mcp.test/mcp", read_token="local-read-token", operator_token="local-operator-token", client=mcp_client)
                with patch.object(registry, "gateway", gateway):
                    topology = await adapter.get_service_topology("payment-service")
                assert topology.observed.calls[0].target == "observed-ledger"
                assert len(requests) == 2 and all(method == "GET" for method, _ in requests)
                assert "secret" not in topology.model_dump_json()
                tools = DemoTelemetryTools()
                tools.get_service_topology = AsyncMock(return_value=topology)
                workflow = LangGraphIncidentWorkflow(tools=tools, checkpointer=InMemorySaver(), approval_signer=ApprovalReceiptSigner("test-only-secret"))
                state = await workflow.start(IncidentQuestion(question="Why is payment-service slow today?"))
                assert state.services == ["payment-service", "observed-ledger", "catalog-only"]
                assert state.service_impacts[0]["relation"] == "downstream_observed_call"
                assert state.service_impacts[0]["observed_call_count"] == 1
                assert all(not impact["causal_direction_verified"] for impact in state.service_impacts)
                retained = await workflow.get(state.incident_id)
                assert retained.service_topology == state.service_topology
                assert any(event.payload.get("topology", {}).get("observed", {}).get("source") == "tempo_client_server_parentage" for event in retained.trace)
    asyncio.run(scenario())


@pytest.mark.parametrize("failed", ["observed", "catalog"])
def test_independent_backend_failures_keep_successful_source_with_explicit_status(failed):
    async def scenario():
        async def backend(request):
            is_observed = request.url.path.endswith("/observed-dependencies")
            if is_observed == (failed == "observed"):
                return httpx.Response(502, json={"error": "private provider diagnostics"})
            if is_observed:
                return httpx.Response(200, json=observed()["data"])
            return httpx.Response(200, json={"service": "payment-service", "edges": catalog()["edges"]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(backend)) as backend_client:
            gateway = TelemetryGatewayClient(base_url="http://gateway.test", token="test-token", max_attempts=1, client=backend_client)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://mcp.test") as client:
                _rate_windows.clear()
                adapter = McpTelemetryTools(url="http://mcp.test/mcp", read_token="local-read-token", operator_token="local-operator-token", client=client)
                with patch.object(registry, "gateway", gateway):
                    topology = await adapter.get_service_topology("payment-service")
                if failed == "observed":
                    assert topology.observed.status == "unavailable"
                    assert topology.catalog_status == "available"
                    assert topology.dependencies == ["catalog-only"]
                else:
                    assert topology.observed.status == "available"
                    assert topology.catalog_status == "unavailable"
                    assert topology.dependencies == ["observed-ledger"]
                assert "private" not in topology.model_dump_json()
    asyncio.run(scenario())
