import asyncio
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

from incident_mcp.security import (
    ApprovalReceiptVerifier,
    AuthorizationError,
    Principal,
)
from incident_mcp.tools import ToolRegistry


class ToolRegistryTests(unittest.TestCase):
    def test_live_log_identity_fields_survive_mcp_projection(self) -> None:
        async def scenario():
            end = datetime.now(UTC)
            self.registry.gateway = AsyncMock()
            self.registry.gateway.search_logs.return_value = {"events": [{
                "timestamp": end.isoformat(), "level": "INFO", "message": "request completed",
                "fields": {"trace_id": "1" * 32, "span_id": "2" * 16, "request_id": "client-123"},
            }]}
            result = await self.registry.call("search_logs", {
                "service": "payment-service", "query": "request completed",
                "start": (end - timedelta(minutes=1)).isoformat(), "end": end.isoformat(),
            }, self.reader)
            event, = result["events"]
            self.assertEqual(event["trace_id"], "1" * 32)
            self.assertEqual(event["span_id"], "2" * 16)
            self.assertEqual(event["request_id"], "client-123")
        asyncio.run(scenario())

    def setUp(self) -> None:
        self.verifier = ApprovalReceiptVerifier("test-secret")
        self.registry = ToolRegistry(self.verifier)
        self.reader = Principal(
            "agent", frozenset({"telemetry:read", "runbook:read"})
        )
        self.operator = Principal(
            "operator",
            frozenset({"telemetry:read", "runbook:read", "production:execute"}),
        )

    def test_all_expected_tools_are_exposed(self) -> None:
        names = {tool["name"] for tool in self.registry.list_definitions()}
        self.assertEqual(
            names,
            {
                "query_metrics",
                "query_operation_status",
                "query_deployment_health",
                "search_logs",
                "query_traces",
                "get_service_dependencies",
                "search_runbooks",
                "search_incident_knowledge",
                "get_recent_deployments",
                "restart_deployment",
                "rollback_release",
            },
        )

    def test_dry_run_does_not_require_operator_scope(self) -> None:
        async def scenario() -> None:
            output = await self.registry.call(
                "rollback_release",
                {
                    "service": "payment-service",
                    "namespace": "prod-sg",
                    "revision": "141",
                    "dry_run": True,
                    "idempotency_key": "dry-run-0001",
                },
                self.reader,
            )
            self.assertFalse(output["executed"])

        asyncio.run(scenario())

    def test_federated_knowledge_search_returns_citable_types(self) -> None:
        async def scenario() -> None:
            output = await self.registry.call(
                "search_incident_knowledge",
                {
                    "service": "payment-service",
                    "query": "database pool acquisition timeout v142",
                    "content_types": [
                        "runbook",
                        "past_incident",
                        "technical_doc",
                    ],
                    "limit": 8,
                },
                self.reader,
            )
            types = {item["document_type"] for item in output["results"]}
            self.assertEqual(
                types,
                {"runbook", "past_incident", "technical_doc"},
            )
            self.assertTrue(
                all(item["uri"] and item["content_hash"] for item in output["results"])
            )

        asyncio.run(scenario())

    def test_execution_requires_signed_receipt(self) -> None:
        async def scenario() -> None:
            with self.assertRaises(AuthorizationError):
                await self.registry.call(
                    "rollback_release",
                    {
                        "service": "payment-service",
                        "namespace": "prod-sg",
                        "revision": "141",
                        "dry_run": False,
                        "idempotency_key": "execute-0001",
                    },
                    self.operator,
                )

        asyncio.run(scenario())

    def test_query_window_is_bounded(self) -> None:
        async def scenario() -> None:
            now = datetime.now(UTC)
            with self.assertRaises(ValueError):
                await self.registry.call(
                    "query_metrics",
                    {
                        "service": "payment-service",
                        "metric": "request_latency_p95",
                        "start": (now - timedelta(days=2)).isoformat(),
                        "end": now.isoformat(),
                        "step_seconds": 60,
                    },
                    self.reader,
                )

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
