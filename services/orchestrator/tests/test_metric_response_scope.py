"""Provider output identity must be checked, never filled from request intent."""
import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest

from incident_copilot.mcp_client import McpTelemetryTools, McpToolError


@pytest.mark.parametrize("field,value", [
    ("service", "private-foreign-service"), ("service", None), ("service", True),
    ("service", ""), ("metric", "memory_working_set"), ("metric", None),
    ("metric", True), ("metric", ""),
])
def test_metric_scope_mismatch_or_missing_is_not_relabelled(field, value):
    async def run():
        output = {"service": "payment-service", "metric": "cpu_utilization", "series": [], "unit": "ratio"}
        if value is None:
            output.pop(field)
        else:
            output[field] = value
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(500))) as client:
            tools = McpTelemetryTools(url="http://mcp.test", read_token="test-read", operator_token="test-operator", client=client)
            tools._call_tool = AsyncMock(return_value=output)
            with pytest.raises(McpToolError, match="requested service and metric") as error:
                await tools.query_metrics("payment-service", ("cpu_utilization",))
            assert "private-foreign-service" not in str(error.value)
    asyncio.run(run())


def test_matching_scope_preserves_origin_and_empty_queries_are_safe():
    async def run():
        now = datetime.now(UTC)
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(500))) as client:
            tools = McpTelemetryTools(url="http://mcp.test", read_token="test-read", operator_token="test-operator", client=client)
            tools._call_tool = AsyncMock(return_value={"service": "payment-service", "metric": "cpu_utilization",
                "series": [{"timestamp": now.isoformat(), "value": .95}], "unit": "ratio", "data_origin": "synthetic"})
            item, = await tools.query_metrics("payment-service", ("cpu_utilization",), end=now)
            assert item.service == "payment-service" and item.query == "cpu_utilization"
            assert item.attributes["data_origin"] == "synthetic"
            assert await tools.query_metrics("payment-service", (), end=now) == []
    asyncio.run(run())
