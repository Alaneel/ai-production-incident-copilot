"""Backend provenance must survive MCP without inferring liveness from transport."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from incident_mcp.security import ApprovalReceiptVerifier, Principal
from incident_mcp.tools import ToolRegistry


ARGUMENTS = {"service": "payment-service", "metric": "request_latency_p95",
             "start": "2026-10-07T12:00:00Z", "end": "2026-10-07T12:05:00Z"}
READER = Principal("reader", frozenset({"telemetry:read"}))


@pytest.mark.parametrize("origin", ["prometheus_api", "synthetic", None])
def test_metric_gateway_provenance_is_forwarded_and_missing_is_unknown(origin):
    async def scenario():
        result = {"service": "payment-service", "metric": "request_latency_p95", "points": [], "unit": "milliseconds"}
        if origin is not None:
            result["data_origin"] = origin
        gateway = AsyncMock()
        gateway.query_metrics.return_value = result
        registry = ToolRegistry(ApprovalReceiptVerifier("metric-test-only-secret"), gateway=gateway)
        response = await registry.call("query_metrics", ARGUMENTS, READER)
        assert response["data_origin"] == (origin if origin is not None else "unknown")
        assert response["source"] == "prometheus-via-telemetry-gateway"
        gateway.query_metrics.assert_awaited_once()
    asyncio.run(scenario())


def test_local_metric_fixture_is_explicitly_synthetic():
    async def scenario():
        registry = ToolRegistry(ApprovalReceiptVerifier("metric-test-only-secret"))
        result = await registry.call("query_metrics", ARGUMENTS, READER)
        assert result["data_origin"] == "synthetic" and result["series"]
    asyncio.run(scenario())


@pytest.mark.parametrize("field,value", [("service", "private-foreign-service"), ("service", None),
                                         ("metric", "cpu_utilization"), ("metric", None)])
def test_gateway_metric_identity_is_required_and_must_match(field, value):
    async def scenario():
        result = {"service": "payment-service", "metric": "request_latency_p95", "points": [], "unit": "milliseconds"}
        if value is None:
            result.pop(field)
        else:
            result[field] = value
        gateway = AsyncMock()
        gateway.query_metrics.return_value = result
        registry = ToolRegistry(ApprovalReceiptVerifier("metric-test-only-secret"), gateway=gateway)
        with pytest.raises(ValueError, match="response scope") as error:
            await registry.call("query_metrics", ARGUMENTS, READER)
        assert "private-foreign-service" not in str(error.value)
    asyncio.run(scenario())
