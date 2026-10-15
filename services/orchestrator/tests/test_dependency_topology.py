import asyncio
import os
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver

from incident_copilot.approval import ApprovalReceiptSigner
from incident_copilot.dependency_topology import ServiceTopology, legacy_topology, parse_topology
from incident_copilot.langgraph_workflow import LangGraphIncidentWorkflow
from incident_copilot.mcp_client import McpTelemetryTools, McpToolError
from incident_copilot.models import IncidentQuestion, IncidentState
from incident_copilot.tools import DemoTelemetryTools


def catalog(service="payment-service"):
    return {"service": service, "source": "service-catalog-via-telemetry-gateway",
            "topology_kind": "static_fixture_catalog", "edges": [
                {"source": "checkout-api", "target": service},
                {"source": service, "target": "ledger-service"},
                {"source": "ledger-service", "target": service},
            ]}


def test_direction_is_catalog_relation_not_causal_proof():
    topology = parse_topology("payment-service", catalog())
    assert topology.dependencies == ["checkout-api", "ledger-service"]
    assert topology.relation("checkout-api") == "upstream_catalog_dependency"
    assert topology.relation("ledger-service") == "bidirectional_catalog_dependency"
    assert topology.relation("unknown-service") == "discovered_dependency"
    one_way = catalog()
    one_way["edges"].pop()
    assert parse_topology("payment-service", one_way).relation("ledger-service") == "downstream_catalog_dependency"


def test_invalid_unrelated_duplicate_self_edges_and_metrics_are_not_retained():
    raw = catalog()
    raw["edges"] += [raw["edges"][0], {"source": "other-a", "target": "other-b"},
                     {"source": "payment-service", "target": "payment-service"},
                     {"source": "payment-service", "target": "secret URL / token"},
                     None, {"source": 5, "target": "payment-service"}]
    raw["edges"][0]["p95_ms"] = 9999
    topology = parse_topology("payment-service", raw)
    assert len(topology.edges) == 3
    assert "9999" not in topology.model_dump_json()
    assert "secret" not in topology.model_dump_json()


@pytest.mark.parametrize("output", [{"service": "wrong-service", "edges": []},
                                    {"service": "payment-service", "edges": {}},
                                    {"service": "payment-service", "edges": [{}] * 65}])
def test_bad_container_fails_closed(output):
    with pytest.raises(ValueError):
        parse_topology("payment-service", output)


def test_unrecognized_provenance_and_name_only_adapters_remain_unknown():
    raw = catalog()
    raw.update(source="private upstream details", topology_kind="live_verified")
    topology = parse_topology("payment-service", raw)
    assert topology.source == topology.topology_kind == "unknown"
    legacy = legacy_topology("payment-service", ["checkout-api", "checkout-api", "payment-service", "../bad"])
    assert legacy.dependencies == ["checkout-api"]
    assert legacy.edges == []
    assert legacy.relation("checkout-api") == "discovered_dependency"


def test_mcp_adapter_uses_one_call_and_redacts_invalid_response():
    async def scenario():
        tools = object.__new__(McpTelemetryTools)
        tools._call_tool = AsyncMock(return_value=catalog())
        assert await tools.get_service_dependencies("payment-service") == ["checkout-api", "ledger-service"]
        tools._call_tool.assert_awaited_once_with("get_service_dependencies", {"service": "payment-service"})
        tools._call_tool.return_value = {"service": "private-other-service", "edges": []}
        with pytest.raises(McpToolError, match="Invalid dependency topology returned"):
            await tools.get_service_topology("payment-service")
    asyncio.run(scenario())


def test_workflow_persists_topology_without_extra_queries_or_causal_upgrade():
    async def scenario():
        tools = DemoTelemetryTools()
        tools.get_service_topology = AsyncMock(return_value=parse_topology("payment-service", catalog()))
        tools.get_service_dependencies = AsyncMock(side_effect=AssertionError("duplicate topology query"))
        workflow = LangGraphIncidentWorkflow(tools=tools, checkpointer=InMemorySaver(),
                                             approval_signer=ApprovalReceiptSigner("test-only-secret"))
        state = await workflow.start(IncidentQuestion(question="Why is payment-service slow today?"))
        tools.get_service_topology.assert_awaited_once()
        tools.get_service_dependencies.assert_not_awaited()
        assert [impact["relation"] for impact in state.service_impacts] == ["upstream_catalog_dependency", "bidirectional_catalog_dependency"]
        assert all(not impact["causal_direction_verified"] for impact in state.service_impacts)
        assert all(impact["topology_kind"] == "static_fixture_catalog" for impact in state.service_impacts)
        restored = await workflow.get(state.incident_id)
        assert restored.service_topology == state.service_topology
        assert IncidentState.model_validate_json(state.model_dump_json()).service_topology == state.service_topology
        event = next(e for e in state.trace if e.event == "tool_result" and e.payload.get("tool") == "get_service_dependencies")
        assert event.payload["topology"]["source"] == "service-catalog-via-telemetry-gateway"
        assert event.payload["causal_direction_verified"] is False
    asyncio.run(scenario())


def test_old_incident_without_topology_is_readable():
    state = IncidentState(question="previous incident", environment="prod-sg", services=["payment-service"])
    assert state.service_topology is None


def test_real_mcp_transport_preserves_fixture_provenance_and_unknown_gateway():
    from incident_mcp.server import _rate_windows, app, registry

    async def scenario():
        _rate_windows.clear()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://mcp.test") as client:
            tools = McpTelemetryTools(url="http://mcp.test/mcp", read_token="local-read-token",
                                      operator_token="local-operator-token", client=client)
            with patch.object(registry, "gateway", None):
                topology = await tools.get_service_topology("payment-service")
            assert topology.topology_kind == "static_fixture_catalog"
            assert topology.source == "service-catalog"
            assert topology.relation("checkout-api") == "upstream_catalog_dependency"
            assert topology.relation("ledger-db") == "downstream_catalog_dependency"
            gateway = type("Gateway", (), {})()
            response = catalog()
            response.pop("topology_kind")
            gateway.dependencies = AsyncMock(return_value=response)
            with patch.object(registry, "gateway", gateway):
                unknown = await tools.get_service_topology("payment-service")
            assert unknown.topology_kind == "unknown"
            assert unknown.source == "service-catalog-via-telemetry-gateway"
    asyncio.run(scenario())


@pytest.mark.skipif(not os.getenv("INCIDENT_TEST_DATABASE_URL"), reason="isolated PostgreSQL test URL required")
def test_topology_survives_fresh_postgres_connections():
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from incident_copilot.store import PostgresIncidentRepository

    async def scenario():
        url = os.environ["INCIDENT_TEST_DATABASE_URL"]
        tools = DemoTelemetryTools()
        raw = catalog()
        now = datetime.now(UTC)
        raw["observed"] = {"status": "available", "data": {
            "service": "payment-service", "source": "tempo_client_server_parentage",
            "start": (now - timedelta(minutes=30)).isoformat(), "end": now.isoformat(),
            "coverage": "sampled_not_exhaustive", "traces_read": 1, "traces_failed": 0, "calls_omitted": 0,
            "calls": [{"source": "payment-service", "target": "ledger-service", "trace_id": "1" * 32,
                       "client_span_id": "2" * 16, "server_span_id": "3" * 16,
                       "observed_at": (now - timedelta(minutes=1)).isoformat()}],
        }}
        primary_topology = parse_topology("payment-service", raw)
        async def topology_for_service(service):
            if service == "payment-service":
                return primary_topology
            return ServiceTopology(service=service, source="service-catalog", catalog_status="available")
        tools.get_service_topology = AsyncMock(side_effect=topology_for_service)
        async with AsyncPostgresSaver.from_conn_string(url) as saver:
            await saver.setup()
            workflow = LangGraphIncidentWorkflow(tools=tools, checkpointer=saver,
                                                 approval_signer=ApprovalReceiptSigner("test-only-secret"))
            state = await workflow.start(IncidentQuestion(question="Why is payment-service slow today?"))
            assert [call.args[0] for call in tools.get_service_topology.await_args_list] == ["payment-service", "ledger-service"]
            diagnostic_query_count = tools.get_service_topology.await_count
            repository = await PostgresIncidentRepository.connect(url)
            try:
                await repository.save(state)
            finally:
                await repository.close()
        async with AsyncPostgresSaver.from_conn_string(url) as saver:
            workflow = LangGraphIncidentWorkflow(tools=tools, checkpointer=saver,
                                                 approval_signer=ApprovalReceiptSigner("test-only-secret"))
            checkpoint = await workflow.get(state.incident_id)
            repository = await PostgresIncidentRepository.connect(url)
            try:
                stored = await repository.get(state.incident_id)
            finally:
                await repository.close()
        assert checkpoint.service_topology == stored.service_topology == state.service_topology
        assert stored.service_topology.observed.calls[0].trace_id == "1" * 32
        assert stored.service_topology.relation("ledger-service") == "downstream_observed_call"
        assert checkpoint.service_impacts == stored.service_impacts == state.service_impacts
        assert all(not item["causal_direction_verified"] for item in stored.service_impacts)
        assert tools.get_service_topology.await_count == diagnostic_query_count
    asyncio.run(scenario())
