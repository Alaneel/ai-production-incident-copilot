import asyncio
from unittest.mock import AsyncMock, patch

import httpx

from incident_mcp import server
from incident_mcp.security import TokenAuthenticator
from incident_mcp.kubernetes import KubernetesRemediationError


def test_schema_error_response_omits_rejected_input_and_context():
    async def scenario():
        with patch.object(server, "authenticator", TokenAuthenticator("redaction-reader", "redaction-operator")):
            server._rate_windows.clear()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://mcp.test") as client:
                response = await client.post("/mcp", headers={"Authorization": "Bearer redaction-reader"}, json={
                    "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                        "name": "restart_deployment", "arguments": {
                            "service": "/private-sensitive-target", "namespace": "prod-sg",
                            "idempotency_key": "redaction-invalid-key", "dry_run": True,
                            "approval_receipt": "private-signed-receipt",
                        },
                    },
                })
                assert "private" not in response.text
                error = response.json()["error"]
                assert error["code"] == -32602
                assert error["data"]["issues"]
                assert all("input" not in issue and "ctx" not in issue for issue in error["data"]["issues"])
    asyncio.run(scenario())


def test_runtime_tool_error_does_not_echo_gateway_diagnostics(caplog):
    async def scenario():
        with patch.object(server, "authenticator", TokenAuthenticator("redaction-reader", "redaction-operator")), \
             patch.object(server.registry, "call", AsyncMock(side_effect=ValueError("private-gateway-diagnostic-with-token"))):
            server._rate_windows.clear()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://mcp.test") as client:
                response = await client.post("/mcp", headers={"Authorization": "Bearer redaction-reader"}, json={
                    "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "query_metrics", "arguments": {}},
                })
                assert response.json()["error"]["code"] == -32602
                assert "private" not in response.text
                assert "private" not in caplog.text
    asyncio.run(scenario())


def test_actual_preflight_failure_returns_controlled_mcp_error_without_simulation_fallback(caplog):
    async def scenario():
        backend = AsyncMock()
        backend.preflight.side_effect = KubernetesRemediationError("private-cluster-diagnostic-with-token")
        with patch.object(server, "authenticator", TokenAuthenticator("preflight-reader", "preflight-operator")), \
             patch.object(server.registry, "remediator", backend):
            server._rate_windows.clear()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://mcp.test") as client:
                response = await client.post("/mcp", headers={"Authorization": "Bearer preflight-reader"}, json={
                    "jsonrpc": "2.0", "id": "preflight-failure", "method": "tools/call", "params": {
                        "name": "restart_deployment", "arguments": {
                            "service": "payment-service", "namespace": "prod-sg", "dry_run": True,
                            "idempotency_key": "controlled-preflight-failure-key",
                        },
                    },
                })
                result = response.json()
                assert result["error"]["code"] == -32602
                assert "result" not in result and "accepted" not in response.text
                assert "private" not in response.text and "private" not in caplog.text
                backend.preflight.assert_awaited_once()
                backend.restart_deployment.assert_not_awaited()
                assert "controlled-preflight-failure-key" not in server.registry._executions
    asyncio.run(scenario())
