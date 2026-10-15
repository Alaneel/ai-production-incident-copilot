import asyncio
import json
import unittest
from pathlib import Path
from datetime import UTC, datetime, timedelta
from dataclasses import replace
from unittest.mock import AsyncMock, patch

import httpx
from langgraph.checkpoint.memory import InMemorySaver

from incident_copilot.approval import ApprovalReceiptSigner
from incident_copilot.langgraph_workflow import LangGraphIncidentWorkflow
from incident_copilot.signal_thresholds import METRIC_UNITS
from incident_copilot.mcp_client import McpTelemetryTools, McpToolError
from incident_copilot.models import (
    ApprovalDecision,
    ApprovalRequest,
    IncidentQuestion,
    IncidentStatus,
)
from incident_mcp.server import _rate_windows, app as mcp_app, registry

ROOT = Path(__file__).resolve().parents[3]
SCENARIOS = json.loads(
    (ROOT / "fault-lab" / "scenarios.json").read_text(encoding="utf-8")
)
EXPECTED_RUNBOOKS = {
    "db-pool-exhaustion": "runbook://payments/db-pool-saturation",
    "downstream-timeout": "runbook://platform/downstream-timeouts",
    "cpu-spike": "runbook://platform/cpu-saturation",
    "memory-leak": "runbook://platform/memory-growth",
    "deployment-regression": "runbook://delivery/deployment-regression",
    "kafka-consumer-lag": "runbook://streaming/consumer-lag",
    "retry-storm": "runbook://resilience/retry-storm",
    "cache-penetration": "runbook://caching/cache-penetration",
}


class ScenarioGateway:
    """Faithful per-scenario observations behind the real MCP transport."""
    def __init__(self, mode):
        self.mode = mode

    async def query_metrics(self, *, service, metric, end, **kwargs):
        baseline = {"request_latency_p95": 2400, "request_error_rate": 0.001,
                    "db_pool_active_ratio": 0.4, "cpu_utilization": 0.1,
                    "memory_working_set": 32 * 1024 * 1024, "kafka_consumer_lag": 0,
                    "retry_rate": 0, "cache_miss_rate": 0}
        signals = {"db-pool-exhaustion": ("db_pool_active_ratio", 1),
                   "cpu-spike": ("cpu_utilization", 0.95),
                   "deployment-regression": ("request_error_rate", 0.3),
                   "kafka-consumer-lag": ("kafka_consumer_lag", 300),
                   "retry-storm": ("retry_rate", 8), "cache-penetration": ("cache_miss_rate", 8)}
        if self.mode in signals:
            name, value = signals[self.mode]
            baseline[name] = value
        values = [baseline[metric]] * 5
        if self.mode == "memory-leak" and metric == "memory_working_set":
            values = [(32 + 4 * i) * 1024 * 1024 for i in range(5)]
        return {"service": service, "metric": metric, "unit": METRIC_UNITS[metric],
                "points": [{"timestamp": (end - timedelta(seconds=(4-i)*15)).isoformat(), "value": value}
                           for i, value in enumerate(values)]}

    async def search_logs(self, *, end, **kwargs):
        message = {"db-pool-exhaustion": "acquisition timed out",
                   "downstream-timeout": "downstream request timed out",
                   "deployment-regression": "invalid ledger response shape"}.get(self.mode, "request completed")
        return {"events": [{"timestamp": end.isoformat(), "level": "error", "message": message}]}

    async def search_traces(self, **kwargs):
        return {"traces": []}

    async def dependencies(self, service):
        return {"service": service, "edges": []}

    async def deployments(self, service):
        return {"service": service, "deployments": [{"revision": "142", "previous_revision": "141",
                                                      "deployed_at": datetime.now(UTC).isoformat()}]}


class McpLangGraphIntegrationTests(unittest.TestCase):
    def test_metric_times_are_bounded_sorted_and_deduplicated(self) -> None:
        async def scenario():
            now = datetime.now(UTC)
            first = (now - timedelta(seconds=20)).isoformat()
            second = (now - timedelta(seconds=10)).isoformat()
            conflicting = (now - timedelta(seconds=5)).isoformat()
            raw = [
                {"timestamp": second, "value": 0.94},
                {"timestamp": first, "value": 0.3},
                {"timestamp": second, "value": 0.94},
                {"timestamp": conflicting, "value": 0.8},
                {"timestamp": conflicting, "value": 0.99},
                {"timestamp": (now + timedelta(minutes=1)).isoformat(), "value": 999},
                {"timestamp": now.replace(tzinfo=None).isoformat(), "value": 999},
                {"timestamp": "broken", "value": 999},
                {"timestamp": (now - timedelta(minutes=40)).isoformat(), "value": 999},
                {"value": 999},
            ]
            async with httpx.AsyncClient() as client:
                tools = McpTelemetryTools(url="http://mcp.test/mcp", read_token="read",
                                          operator_token="operator", client=client)
                tools._call_tool = AsyncMock(return_value={"service": "payment-service", "metric": "cpu_utilization", "series": raw})
                item, = await tools.query_metrics("payment-service", ("cpu_utilization",))
            self.assertEqual(item.attributes["series"], [
                {"timestamp": first, "value": 0.3},
                {"timestamp": second, "value": 0.94},
            ])
            self.assertEqual(item.attributes["discarded_samples"], 8)
            self.assertEqual(item.observed_at, datetime.fromisoformat(second))
            self.assertNotIn("999", item.observation)

        asyncio.run(scenario())

    def test_memory_samples_must_be_inside_its_shorter_query_window(self) -> None:
        async def scenario():
            now = datetime.now(UTC)
            raw = [{"timestamp": (now - timedelta(minutes=minutes)).isoformat(), "value": value}
                   for minutes, value in ((10, 999), (1, 128))]
            async with httpx.AsyncClient() as client:
                tools = McpTelemetryTools(url="http://mcp.test/mcp", read_token="read",
                                          operator_token="operator", client=client)
                tools._call_tool = AsyncMock(return_value={"service": "payment-service", "metric": "memory_working_set", "series": raw})
                item, = await tools.query_metrics("payment-service", ("memory_working_set",))
            self.assertEqual(len(item.attributes["series"]), 1)
            self.assertEqual(item.attributes["series"][0]["value"], 128)
            self.assertEqual(item.attributes["discarded_samples"], 1)

        asyncio.run(scenario())

    def test_metric_boundary_discards_bad_samples_and_serializes_finite_values(self) -> None:
        async def scenario() -> None:
            earlier = (datetime.now(UTC) - timedelta(seconds=10)).isoformat()
            later = (datetime.now(UTC) - timedelta(seconds=5)).isoformat()
            async with httpx.AsyncClient() as client:
                tools = McpTelemetryTools(
                    url="http://mcp.test/mcp", read_token="read",
                    operator_token="operator", client=client,
                )
                tools._call_tool = AsyncMock(return_value={
                    "service": "payment-service", "metric": "cpu_utilization",
                    "series": [
                        {"timestamp": earlier, "value": "0.3"},
                        {}, {"value": None}, {"value": "broken"},
                        {"value": "NaN"}, {"value": "Infinity"},
                        {"value": True}, "bad point",
                        {"timestamp": later, "value": "0.94"},
                    ], "unit": "ratio",
                })
                item, = await tools.query_metrics("payment-service", ("cpu_utilization",))
                self.assertEqual(item.attributes["series"], [
                    {"timestamp": earlier, "value": 0.3},
                    {"timestamp": later, "value": 0.94},
                ])
                self.assertEqual(item.attributes["discarded_samples"], 7)
                self.assertIn("0.94", item.observation)
                json.dumps(item.model_dump(mode="json"), allow_nan=False)

        asyncio.run(scenario())

    def test_missing_metric_data_is_not_reported_as_zero_or_an_anomaly(self) -> None:
        async def scenario() -> None:
            async with httpx.AsyncClient() as client:
                tools = McpTelemetryTools(
                    url="http://mcp.test/mcp", read_token="read",
                    operator_token="operator", client=client,
                )
                for series in ([], [{"value": "NaN"}, {}]):
                    tools._call_tool = AsyncMock(return_value={"service": "payment-service", "metric": "memory_working_set", "series": series})
                    item, = await tools.query_metrics(
                        "payment-service", ("memory_working_set",)
                    )
                    self.assertEqual(item.attributes["series"], [])
                    self.assertEqual(item.score, 0)
                    self.assertIn("No valid samples", item.observation)
                    self.assertNotIn("peaked at 0", item.observation)
                tools._call_tool = AsyncMock(return_value={"service": "payment-service", "metric": "cpu_utilization", "series": {"value": 1}})
                with self.assertRaises(McpToolError):
                    await tools.query_metrics("payment-service", ("cpu_utilization",))

        asyncio.run(scenario())

    def test_memory_query_preserves_growth_at_scrape_resolution(self) -> None:
        async def scenario() -> None:
            requests = []

            async def handler(request: httpx.Request) -> httpx.Response:
                message = json.loads(request.content)
                if message["method"] == "tools/call":
                    args = message["params"]["arguments"]
                    requests.append(args)
                    return httpx.Response(200, json={
                        "jsonrpc": "2.0", "id": message["id"],
                        "result": {"structuredContent": {
                            "service": args["service"], "metric": args["metric"],
                            "series": [{
                                "timestamp": (datetime.fromisoformat(args["end"])
                                              - timedelta(seconds=(2-index)*5)).isoformat(),
                                "value": value * 1024 * 1024,
                            } for index, value in enumerate((128, 160, 192))],
                            "unit": "bytes",
                        }},
                    })
                if "id" not in message:
                    return httpx.Response(202)
                return httpx.Response(200, json={
                    "jsonrpc": "2.0", "id": message["id"], "result": {},
                })

            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                tools = McpTelemetryTools(
                    url="http://mcp.test/mcp", read_token="read",
                    operator_token="operator", client=client,
                )
                evidence = await tools.query_metrics(
                    "payment-service", ("memory_working_set", "cpu_utilization")
                )
            by_metric = {args["metric"]: args for args in requests}
            memory = by_metric["memory_working_set"]
            cpu = by_metric["cpu_utilization"]
            self.assertEqual(memory["step_seconds"], 5)
            self.assertEqual(cpu["step_seconds"], 15)
            for args, seconds in ((memory, 300), (cpu, 1800)):
                self.assertEqual(
                    (datetime.fromisoformat(args["end"])
                     - datetime.fromisoformat(args["start"])).total_seconds(),
                    seconds,
                )
            self.assertEqual(len(evidence[0].attributes["series"]), 3)

        asyncio.run(scenario())

    def test_graph_uses_mcp_and_executes_only_after_signed_approval(self) -> None:
        async def scenario() -> None:
            transport = httpx.ASGITransport(app=mcp_app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://mcp.test",
            ) as client:
                tools = McpTelemetryTools(
                    url="http://mcp.test/mcp",
                    read_token="local-read-token",
                    operator_token="local-operator-token",
                    client=client,
                )
                workflow = LangGraphIncidentWorkflow(
                    tools=tools,
                    checkpointer=InMemorySaver(),
                    approval_signer=ApprovalReceiptSigner(
                        "local-approval-secret-change-me"
                    ),
                )
                # This test supplies an observed-deployment contract fixture,
                # not an actual Kubernetes discovery acceptance. The ordinary
                # MCP synthetic catalog must not enable this rollback path.
                original_deployments = tools.get_recent_deployments
                async def observed_contract(service, namespace="prod-sg"):
                    evidence = await original_deployments(service, namespace=namespace)
                    for item in evidence:
                        item.attributes.update(data_origin="kubernetes_api", namespace="prod-sg")
                    return evidence
                tools.get_recent_deployments = observed_contract
                original_metric = registry.tools["query_metrics"]
                async def current_synthetic_metric(arguments, principal):
                    result = await original_metric.handler(arguments, principal)
                    result["series"] = [{**point, "timestamp":
                        (arguments.end - timedelta(seconds=(len(result["series"]) - 1 - index) * 15)).isoformat()}
                        for index, point in enumerate(result["series"])]
                    return result
                # Positive approval contract uses explicitly current synthetic
                # metrics; the default old fixture must not bypass freshness.
                with patch.dict(registry.tools, {"query_metrics": replace(original_metric, handler=current_synthetic_metric)}), \
                        self.assertLogs("incident-mcp", level="INFO") as captured:
                    paused = await workflow.start(
                        IncidentQuestion(
                            question="Why did payment-service latency spike?"
                        )
                    )
                    self.assertEqual(paused.status, IncidentStatus.WAITING_APPROVAL)
                    self.assertGreaterEqual(len(paused.evidence), 4)
                    self.assertEqual(
                        {item.kind.value for item in paused.evidence},
                        {
                            "metric",
                            "log",
                            "trace",
                            "runbook",
                            "past_incident",
                            "technical_doc",
                            "deployment",
                        },
                    )

                    pending = await workflow.resume_with_approval(
                        paused.incident_id,
                        ApprovalRequest(
                            decision=ApprovalDecision.APPROVE,
                            actor="oncall@example.com",
                            reason="Correlated evidence reviewed.",
                            idempotency_key="mcp-execution-0001",
                        ),
                    )
                self.assertEqual(pending.status, IncidentStatus.REMEDIATION_PENDING)
                self.assertEqual(pending.recovery["status"], "pending")
                self.assertTrue(pending.recovery["simulation"])
                audit_events = [
                    json.loads(record.getMessage()) for record in captured.records
                ]
                tool_events = [event for event in audit_events if event["tool"]]
                self.assertTrue(
                    all(event["outcome"] == "success" for event in tool_events)
                )
                rollback_events = [
                    event
                    for event in tool_events
                    if event["tool"] == "rollback_release"
                ]
                self.assertEqual(len(rollback_events), 2)
                self.assertTrue(
                    all(event["read_only"] is False for event in rollback_events)
                )

        asyncio.run(scenario())

    def test_all_fault_families_use_real_mcp_schemas_and_knowledge(
        self,
    ) -> None:
        async def scenario() -> None:
            transport = httpx.ASGITransport(app=mcp_app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://mcp.test",
            ) as client:
                tools = McpTelemetryTools(
                    url="http://mcp.test/mcp",
                    read_token="local-read-token",
                    operator_token="local-operator-token",
                    client=client,
                )
                for name, source in SCENARIOS.items():
                    with self.subTest(scenario=name):
                        _rate_windows.clear()
                        workflow = LangGraphIncidentWorkflow(
                            tools=tools,
                            checkpointer=InMemorySaver(),
                            approval_signer=ApprovalReceiptSigner(
                                "local-approval-secret-change-me"
                            ),
                        )
                        with patch.object(registry, "gateway", ScenarioGateway(name)):
                            state = await workflow.start(
                                # This baseline's gateway exposes one fault
                                # everywhere; select its tested service openly,
                                # not by silently using the last mention.
                                IncidentQuestion(question=f"Investigate {source['service']}: {source['question']}")
                            )
                        self.assertEqual(
                            state.services[0],
                            source["service"],
                        )
                        self.assertIn(
                            EXPECTED_RUNBOOKS[name],
                            {item.source for item in state.evidence},
                        )
                        self.assertTrue(
                            any(item.kind.value == "metric" for item in state.evidence)
                        )

        asyncio.run(scenario())

    def test_restart_path_uses_bound_mcp_approval(self) -> None:
        async def scenario() -> None:
            transport = httpx.ASGITransport(app=mcp_app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://mcp.test",
            ) as client:
                tools = McpTelemetryTools(
                    url="http://mcp.test/mcp",
                    read_token="local-read-token",
                    operator_token="local-operator-token",
                    client=client,
                )
                workflow = LangGraphIncidentWorkflow(
                    tools=tools,
                    checkpointer=InMemorySaver(),
                    approval_signer=ApprovalReceiptSigner(
                        "local-approval-secret-change-me"
                    ),
                )
                with patch.object(registry, "gateway", ScenarioGateway("cpu-spike")):
                    paused = await workflow.start(
                        IncidentQuestion(question="为什么 risk-engine CPU 和延迟同时升高？")
                    )
                self.assertEqual(
                    paused.remediation.tool,
                    "restart_deployment",
                )
                pending = await workflow.resume_with_approval(
                    paused.incident_id,
                    ApprovalRequest(
                        decision=ApprovalDecision.APPROVE,
                        actor="oncall@example.com",
                        reason="Restart dry-run and evidence reviewed.",
                        idempotency_key="mcp-restart-approval-0001",
                    ),
                )
                self.assertEqual(pending.status, IncidentStatus.REMEDIATION_PENDING)
                self.assertEqual(pending.recovery["status"], "pending")
                self.assertTrue(pending.recovery["simulation"])

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
