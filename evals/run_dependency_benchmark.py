"""Synthetic dependency observations through the real graph; not causal RCA evaluation."""
import asyncio
import hashlib
import json
import statistics
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from run_workflow_benchmark import ROOT, SnapshotTools
from langgraph.checkpoint.memory import InMemorySaver
from incident_copilot.approval import ApprovalReceiptSigner
from incident_copilot.langgraph_workflow import LangGraphIncidentWorkflow
from incident_copilot.mcp_client import McpToolError
from incident_copilot.models import ApprovalRequest, Evidence, IncidentQuestion
from incident_mcp.knowledge import KnowledgeIndex


def fixtures():
    scenarios = [
        ("overlap", [2500] * 5, 0, None, "observed_anomaly", True),
        ("outside", [2500] * 5, -600, None, "observed_anomaly", False),
        ("empty", [], 0, None, "unconfirmed", None),
        ("stale", [2500] * 5, -3600, None, "unconfirmed", None),
        ("future", [2500] * 5, 120, None, "unconfirmed", None),
        ("normal", [100] * 5, 0, None, "unconfirmed", None),
        ("single_spike", [100] * 4 + [2500], 0, None, "unconfirmed", None),
        ("query_error", [], 0, "transport", "query_failed", None),
        ("timeout", [], 0, "timeout", "query_failed", None),
    ]
    return [{"id": f"{service}-{name}", "service": service, "dependency": dependency,
             "question": f"Why is {service} slow today?",
             "observations": {"values": values, "offset_seconds": offset, "error": error},
             "expected_status": status, "expected_overlap": overlap}
            for service, dependency in [("payment-service", "checkout-api"), ("ledger-service", "payment-service")]
            for name, values, offset, error, status, overlap in scenarios]


class DependencyTools(SnapshotTools):
    def __init__(self, *, service, dependency, observations, index):
        super().__init__(service=service, metrics={"db_pool_active_ratio": [1] * 5},
                         logs=["acquisition timed out"], index=index)
        self.dependency, self.observations = dependency, observations
        self.now = datetime.now(UTC)

    async def get_service_dependencies(self, service):
        return [self.dependency]

    async def query_metrics(self, service, metrics):
        if service == self.service:
            return await super().query_metrics(service, metrics)
        assert service == self.dependency
        if self.observations["error"] == "transport":
            raise McpToolError("synthetic private transport diagnostic")
        if self.observations["error"] == "timeout":
            raise TimeoutError("synthetic private timeout diagnostic")
        values = self.observations["values"]
        if not values:
            return []
        now = self.now + timedelta(seconds=self.observations["offset_seconds"])
        return [Evidence(kind="metric", service=service, source="dependency-snapshot",
                         title="latency observations", observation="retained dependency latency samples",
                         query="request_latency_p95", score=0.9, observed_at=now,
                         attributes={"series": [{"timestamp": (now - timedelta(seconds=(4-i)*15)).isoformat(), "value": value}
                                                for i, value in enumerate(values)]})]


async def run_case(case, index):
    # Ground-truth statuses remain in the scorer, not the tools or graph input.
    tools = DependencyTools(service=case["service"], dependency=case["dependency"],
                            observations=case["observations"], index=index)
    workflow = LangGraphIncidentWorkflow(tools=tools, checkpointer=InMemorySaver(),
                                         approval_signer=ApprovalReceiptSigner("dependency-benchmark-only-secret"))
    started = time.perf_counter()
    state = await workflow.start(IncidentQuestion(question=case["question"], environment="benchmark"))
    cause = state.incident_type
    if state.status == "waiting_approval":
        state = await workflow.resume_with_approval(state.incident_id, ApprovalRequest(
            decision="reject", actor="benchmark-operator", reason="Never execute benchmark mutations",
            idempotency_key=f"dependency-{case['id']}"))
    retained = await workflow.get(state.incident_id)
    impact = retained.service_impacts[0]
    return {"case_id": case["id"], "scenario": case["observations"],
            "expected_status": case["expected_status"], "actual_status": impact["status"],
            "expected_overlap": case["expected_overlap"], "actual_overlap": impact["overlaps_primary_window"],
            "primary_classification_preserved": cause == "database_connection_pool_exhaustion",
            "checkpoint_roundtrip": retained.model_dump() == state.model_dump(),
            "no_causal_direction_claim": impact["causal_direction_verified"] is False,
            "execution_attempts": tools.execution_attempts,
            "latency_ms": (time.perf_counter() - started) * 1000,
            "state": retained.model_dump(mode="json")}


async def main():
    cases = fixtures()
    index = KnowledgeIndex.load_default()
    records = [await run_case(case, index) for case in cases]
    failures = [item["case_id"] for item in records if
                item["actual_status"] != item["expected_status"] or item["actual_overlap"] != item["expected_overlap"]
                or not item["primary_classification_preserved"] or not item["checkpoint_roundtrip"]
                or not item["no_causal_direction_claim"] or item["execution_attempts"]]
    result = {
        "kind": "synthetic_dependency_actual_langgraph_no_model", "cases": len(records),
        "measured_at": datetime.now(UTC).isoformat(),
        "fixtures_sha256": hashlib.sha256(json.dumps(cases, sort_keys=True).encode()).hexdigest(),
        "workflow_sha256": hashlib.sha256((ROOT / "services/orchestrator/incident_copilot/langgraph_workflow.py").read_bytes()).hexdigest(),
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "thresholds_sha256": hashlib.sha256((ROOT / "services/orchestrator/incident_copilot/signal_thresholds.py").read_bytes()).hexdigest(),
        "impact_status_accuracy": statistics.mean(item["actual_status"] == item["expected_status"] for item in records),
        "window_overlap_accuracy": statistics.mean(item["actual_overlap"] == item["expected_overlap"] for item in records),
        "primary_classification_preservation_rate": statistics.mean(item["primary_classification_preserved"] for item in records),
        "checkpoint_roundtrip_rate": statistics.mean(item["checkpoint_roundtrip"] for item in records),
        "mean_snapshot_latency_ms": statistics.mean(item["latency_ms"] for item in records),
        "execution_attempts": sum(item["execution_attempts"] for item in records),
        "model_calls": 0, "new_api_cost_usd": 0, "failures": failures,
        "limitations": ["18 synthetic cases: nine observation patterns on two service pairs, not independent production incidents",
                        "No causal direction, root-cause accuracy, live MCP, model reasoning, or PostgreSQL recovery evaluated",
                        "Timeout is a provider-raised fixture, not a measured ten-second cancellation test"],
    }
    destination = ROOT / "evals/results"
    (destination / "dependency_benchmark.json").write_text(json.dumps(result, indent=2) + "\n")
    (destination / "dependency_benchmark_cases.jsonl").write_text("".join(json.dumps(item) + "\n" for item in records))
    print(json.dumps(result, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
