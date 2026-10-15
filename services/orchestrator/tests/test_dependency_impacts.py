import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

from langgraph.checkpoint.memory import InMemorySaver

from incident_copilot.approval import ApprovalReceiptSigner
from incident_copilot.langgraph_workflow import LangGraphIncidentWorkflow
from incident_copilot.mcp_client import McpToolError
from incident_copilot.models import Evidence, IncidentQuestion
from incident_copilot.tools import DemoTelemetryTools


def metric(service, name, values, now):
    return Evidence(kind="metric", service=service, source="test", title=name,
                    observation="timestamped samples", observed_at=now, score=0.9, query=name,
                    attributes={"series": [{"timestamp": (now - timedelta(seconds=(len(values)-i)*15)).isoformat(), "value": value}
                                           for i, value in enumerate(values)]})


def test_dependency_anomalies_do_not_contaminate_primary_classification():
    async def scenario():
        now = datetime.now(UTC)
        tools = DemoTelemetryTools()
        tools.get_service_dependencies = AsyncMock(return_value=["checkout-api", "risk-engine", "ledger-db"])

        async def metrics(service, names):
            if service == "payment-service":
                return [metric(service, "db_pool_active_ratio", [1, 1, 1], now)]
            if service == "checkout-api":
                return [metric(service, "request_latency_p95", [2500, 2500, 2500], now)]
            if service == "risk-engine":
                return [metric(service, "request_error_rate", [0.3, 0.3], now - timedelta(hours=1))]
            raise McpToolError("private gateway detail")

        tools.query_metrics = AsyncMock(side_effect=metrics)
        workflow = LangGraphIncidentWorkflow(tools=tools, checkpointer=InMemorySaver(),
                                             approval_signer=ApprovalReceiptSigner("test-only-secret"))
        state = await workflow.start(IncidentQuestion(question="Why is payment-service slow today?"))
        assert state.incident_type == "database_connection_pool_exhaustion"
        impacts = {item["service"]: item for item in state.service_impacts}
        assert impacts["checkout-api"]["status"] == "observed_anomaly"
        assert impacts["checkout-api"]["overlaps_primary_window"] is True
        assert impacts["risk-engine"]["status"] == "unconfirmed"
        assert impacts["ledger-db"]["status"] == "query_failed"
        assert all(item["causal_direction_verified"] is False for item in state.service_impacts)
        assert "private gateway detail" not in state.model_dump_json()
        retained = await workflow.get(state.incident_id)
        assert retained.service_impacts == state.service_impacts
        assert any(event.event == "dependency_impacts_assessed" for event in retained.trace)
        assert tools.query_metrics.await_count == 4
    asyncio.run(scenario())


def test_dependency_queries_are_deduplicated_bounded_and_empty_data_not_healthy():
    async def scenario():
        tools = DemoTelemetryTools()
        metrics = tools.query_metrics
        active = 0
        peak = 0
        queried = []

        async def query(service, names):
            nonlocal active, peak
            if service == "payment-service":
                return await metrics(service, names)
            queried.append(service)
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            return []

        tools.get_service_dependencies = AsyncMock(return_value=["payment-service", "service-0", "service-0", *[f"service-{index}" for index in range(10)]])
        tools.query_metrics = query
        workflow = LangGraphIncidentWorkflow(tools=tools, checkpointer=InMemorySaver(),
                                             approval_signer=ApprovalReceiptSigner("test-only-secret"))
        state = await workflow.start(IncidentQuestion(question="Why is payment-service slow today?"))
        assert len(queried) == len(set(queried)) == 8
        assert peak <= 4
        assert all(item["status"] == "unconfirmed" for item in state.service_impacts)
        assessment = next(event for event in state.trace if event.event == "dependency_impacts_assessed")
        assert assessment.payload["omitted_services"] == ["service-8", "service-9"]
    asyncio.run(scenario())
