import asyncio
import unittest
from datetime import UTC, datetime, timedelta

import httpx

from incident_mcp.gateway import TelemetryGatewayClient
from incident_mcp.security import ApprovalReceiptVerifier, Principal
from incident_mcp.tools import ToolRegistry


class TelemetryGatewayIntegrationTests(unittest.TestCase):
    def test_gateway_retries_an_empty_success_response(self) -> None:
        async def scenario() -> None:
            attempts = 0

            async def handler(request: httpx.Request) -> httpx.Response:
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    return httpx.Response(200, content=b"")
                return httpx.Response(
                    200,
                    json={
                        "service": "risk-engine",
                        "metric": "cpu_utilization",
                        "unit": "ratio",
                        "points": [],
                    },
                )

            client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            gateway = TelemetryGatewayClient(
                base_url="http://gateway.test",
                token="gateway-token",
                client=client,
            )
            now = datetime.now(UTC)
            result = await gateway.query_metrics(
                service="risk-engine",
                metric="cpu_utilization",
                start=now - timedelta(minutes=30),
                end=now,
                step_seconds=60,
            )
            self.assertEqual(result["metric"], "cpu_utilization")
            self.assertEqual(attempts, 2)
            await client.aclose()

        asyncio.run(scenario())

    def test_mcp_metric_tool_normalizes_gateway_response(self) -> None:
        async def scenario() -> None:
            async def handler(request: httpx.Request) -> httpx.Response:
                self.assertEqual(
                    request.headers["authorization"],
                    "Bearer gateway-token",
                )
                self.assertEqual(request.url.path, "/v1/metrics/query")
                return httpx.Response(
                    200,
                    json={
                        "service": "payment-service",
                        "metric": "request_latency_p95",
                        "unit": "milliseconds",
                        "points": [
                            {
                                "timestamp": "2026-07-23T08:00:00Z",
                                "value": 2840,
                            }
                        ],
                    },
                )

            client = httpx.AsyncClient(
                transport=httpx.MockTransport(handler),
                base_url="http://gateway.test",
            )
            gateway = TelemetryGatewayClient(
                base_url="http://gateway.test",
                token="gateway-token",
                client=client,
            )
            registry = ToolRegistry(
                ApprovalReceiptVerifier("test-secret"),
                gateway=gateway,
            )
            now = datetime.now(UTC)
            output = await registry.call(
                "query_metrics",
                {
                    "service": "payment-service",
                    "metric": "request_latency_p95",
                    "start": (now - timedelta(minutes=30)).isoformat(),
                    "end": now.isoformat(),
                    "step_seconds": 60,
                },
                Principal("agent", frozenset({"telemetry:read"})),
            )
            self.assertEqual(output["series"][0]["value"], 2840)
            self.assertEqual(
                output["source"],
                "prometheus-via-telemetry-gateway",
            )
            await client.aclose()

        asyncio.run(scenario())

    def test_mcp_trace_tool_normalizes_tempo_gateway_response(self) -> None:
        async def scenario() -> None:
            async def handler(request: httpx.Request) -> httpx.Response:
                self.assertEqual(request.url.path, "/v1/traces/search")
                return httpx.Response(
                    200,
                    json={
                        "count": 1,
                        "traces": [
                            {
                                "trace_id": "4f3a",
                                "root_service_name": "checkout-api",
                                "root_trace_name": "POST /v1/checkout",
                                "started_at": "2026-07-23T08:00:00Z",
                                "duration_ms": 2840.5,
                            }
                        ],
                    },
                )

            client = httpx.AsyncClient(
                transport=httpx.MockTransport(handler),
                base_url="http://gateway.test",
            )
            gateway = TelemetryGatewayClient(
                base_url="http://gateway.test",
                token="gateway-token",
                client=client,
            )
            registry = ToolRegistry(
                ApprovalReceiptVerifier("test-secret"),
                gateway=gateway,
            )
            now = datetime.now(UTC)
            output = await registry.call(
                "query_traces",
                {
                    "service": "payment-service",
                    "start": (now - timedelta(minutes=30)).isoformat(),
                    "end": now.isoformat(),
                    "limit": 20,
                },
                Principal("agent", frozenset({"telemetry:read"})),
            )
            self.assertEqual(output["traces"][0]["trace_id"], "4f3a")
            self.assertEqual(
                output["source"],
                "tempo-via-telemetry-gateway",
            )
            await client.aclose()

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
