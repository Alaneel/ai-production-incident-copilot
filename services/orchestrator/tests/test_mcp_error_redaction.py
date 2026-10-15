import asyncio
from unittest.mock import AsyncMock

import httpx

from incident_copilot.mcp_client import McpTelemetryTools, McpToolError


def test_mcp_server_error_content_does_not_propagate_to_exception():
    async def scenario():
        async with httpx.AsyncClient() as client:
            adapter = McpTelemetryTools(url="http://mcp.invalid/mcp", read_token="test-read", operator_token="test-operator", client=client)
            adapter._initialize = AsyncMock()
            adapter._request = AsyncMock(return_value={"error": {
                "code": -32602, "message": "private-service-diagnostic", "data": {"receipt": "private-approval-receipt"},
            }})
            try:
                await adapter._call_tool("query_metrics", {})
            except McpToolError as error:
                assert "private" not in str(error)
            else:
                raise AssertionError("Expected rejection")
    asyncio.run(scenario())


def test_mcp_initialization_error_is_redacted():
    async def scenario():
        async with httpx.AsyncClient() as client:
            adapter = McpTelemetryTools(url="http://mcp.invalid/mcp", read_token="test-read", operator_token="test-operator", client=client)
            adapter._request = AsyncMock(return_value={"error": {"message": "private-credential-echo"}})
            try:
                await adapter._initialize("test-read")
            except McpToolError as error:
                assert "private" not in str(error)
                assert not adapter._initialized_tokens
            else:
                raise AssertionError("Expected rejection")
    asyncio.run(scenario())
