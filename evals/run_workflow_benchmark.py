"""Actual LangGraph + BM25 execution over synthetic telemetry, without model calls."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import statistics
import sys
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "services/orchestrator"))
sys.path.insert(0, str(ROOT / "services/mcp-server"))

# Offline benchmark entry points must not inherit provider SDK exporters.
for setting in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING", "LANGCHAIN_TRACING_V2", "LANGSMITH_OTEL_ENABLED"):
    os.environ[setting] = "false"
from langsmith import tracing_context  # noqa: E402
from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from incident_copilot.approval import ApprovalReceiptSigner  # noqa: E402
from incident_copilot.langgraph_workflow import LangGraphIncidentWorkflow  # noqa: E402
from incident_copilot.models import ApprovalRequest, Evidence, IncidentQuestion  # noqa: E402
from incident_mcp.knowledge import KnowledgeIndex  # noqa: E402
from evals.release_provenance import runtime_provenance  # noqa: E402

# Independent synthetic observations, not a call into the classifier or PROFILES.
FAULT_SIGNALS = {
    "database_connection_pool_exhaustion": ("db_pool_active_ratio", 1.0, "acquisition timed out"),
    "downstream_timeout": ("request_latency_p95", 2400, "downstream request timed out"),
    "cpu_saturation": ("cpu_utilization", 0.95, "request completed"),
    "memory_leak": ("memory_working_set", None, "request completed"),
    "deployment_regression": ("request_error_rate", 0.3, "invalid ledger response shape"),
    "kafka_consumer_lag": ("kafka_consumer_lag", 300, "request completed"),
    "retry_storm": ("retry_rate", 8, "downstream dependency failed"),
    "cache_penetration": ("cache_miss_rate", 8, "request completed"),
}

# Lab operator's expected policy, scorer-only; not imported from agent profiles.
EXPECTED_ACTIONS = {
    "database_connection_pool_exhaustion": "rollback_release", "downstream_timeout": "none",
    "cpu_saturation": "restart_deployment", "memory_leak": "rollback_release",
    "deployment_regression": "rollback_release", "kafka_consumer_lag": "restart_deployment",
    "retry_storm": "none", "cache_penetration": "none",
}


def fixtures(dataset: list[dict]) -> list[dict]:
    selected = []
    counts: Counter = Counter()
    for case in dataset:
        fault = case["root_cause"]
        if fault not in FAULT_SIGNALS or counts[fault] >= 15:
            continue
        counts[fault] += 1
        values = {"request_latency_p95": 100, "request_error_rate": 0.001,
                  "db_pool_active_ratio": 0.4, "cpu_utilization": 0.1,
                  "memory_working_set": 32 * 1024 * 1024, "kafka_consumer_lag": 0,
                  "retry_rate": 0, "cache_miss_rate": 0}
        metric, value, message = FAULT_SIGNALS[fault]
        values[metric] = value
        if fault != "memory_leak":
            values["request_latency_p95"] = 2400 + case["fault_seed"] % 200
        series = {name: [sample] * 5 for name, sample in values.items()}
        if fault == "memory_leak":
            series[metric] = [(32 + 4 * index) * 1024 * 1024 for index in range(5)]
        selected.append({"id": case["id"], "question": case["question"], "service": case["service"],
                         "metrics": series, "logs": [message], "expected_cause": fault,
                         "expected_action": EXPECTED_ACTIONS[fault]})
    if counts != Counter({fault: 15 for fault in FAULT_SIGNALS}):
        raise ValueError("Need at least 15 cases per fault family.")
    normal = {"request_latency_p95": [100] * 5, "request_error_rate": [0.001] * 5,
              "db_pool_active_ratio": [0.4] * 5, "cpu_utilization": [0.1] * 5,
              "memory_working_set": [32 * 1024 * 1024] * 5,
              "kafka_consumer_lag": [0] * 5, "retry_rate": [0] * 5, "cache_miss_rate": [0] * 5}
    for kind in ("healthy", "no_data"):
        for index in range(15):
            observed = normal
            if index % 3 == 1:
                observed = {**normal, "cpu_utilization": [0.6] * 5, "kafka_consumer_lag": [50] * 5,
                            "retry_rate": [0.5] * 5, "cache_miss_rate": [0.5] * 5}
            elif index % 3 == 2:
                observed = {**normal, "cpu_utilization": [0.1] * 4 + [0.95],
                            "kafka_consumer_lag": [0] * 4 + [300], "retry_rate": [0] * 4 + [8],
                            "cache_miss_rate": [0] * 4 + [8], "db_pool_active_ratio": [0.4] * 4 + [1]}
            selected.append({"id": f"{kind}-{index:02d}", "question": "Why is payment-service slow today?",
                             "service": "payment-service", "metrics": observed if kind == "healthy" else {},
                             "logs": ["request completed"] if kind == "healthy" else [], "expected_cause": None,
                             "expected_action": "none"})
    return selected


class SnapshotTools:
    # Offline snapshots refuse all execution; this opt-in is not evidence data.
    allow_simulated_deployment_targets = True
    def __init__(self, *, service: str, metrics: dict, logs: list[str], index: KnowledgeIndex):
        self.service, self.metrics, self.logs, self.index = service, metrics, logs, index
        self.execution_attempts = 0

    def evidence(self, kind, title, observation, *, query=None, attributes=None, source="snapshot"):
        return Evidence(kind=kind, title=title, observation=observation, source=source,
                        service=self.service, observed_at=datetime.now(UTC), score=0.9,
                        query=query, attributes=attributes or {})

    async def query_metrics(self, service, metrics):
        assert service == self.service
        now = datetime.now(UTC)
        return [self.evidence("metric", name, f"{name} retained samples", query=name,
                             attributes={"series": [{"timestamp": (now - timedelta(seconds=(4-i)*15)).isoformat(), "value": value}
                                                    for i, value in enumerate(self.metrics[name])]})
                for name in metrics if name in self.metrics]

    async def search_logs(self, service, query):
        assert service == self.service
        return [self.evidence("log", "retained logs", " | ".join(self.logs), query=query,
                             attributes={"events": [{"message": message, "timestamp": datetime.now(UTC).isoformat()} for message in self.logs]})] if self.logs else []

    async def query_traces(self, service):
        return []

    async def get_service_dependencies(self, service):
        return []

    async def get_recent_deployments(self, service, namespace="prod-sg"):
        return [self.evidence("deployment", "snapshot release", "latest release metadata",
                              attributes={"revision": "42", "previous_revision": "41", "data_origin": "synthetic"})]

    async def search_incident_knowledge(self, service, query):
        return [self.evidence(item["document_type"], item["title"], item["excerpt"],
                              query=query, source=item["uri"], attributes=item)
                for item in self.index.search(service=service, query=query,
                                             content_types={"runbook", "past_incident", "technical_doc"}, limit=5)]

    async def rollback_release(self, **arguments):
        if arguments.get("dry_run") is not True:
            self.execution_attempts += 1
            raise RuntimeError("Benchmark forbids execution.")
        return {"accepted": True, "executed": False, "execution_backend": "snapshot-dry-run",
                "dry_run": True, "backend": "simulation", "preflight_mode": "simulation", "target_context": None}

    async def restart_deployment(self, **arguments):
        return await self.rollback_release(**arguments)


async def run_case(case: dict, index: KnowledgeIndex, *, hypothesis_generator=None) -> dict:
    # Labels remain in the scorer. Only observations enter the tools/workflow.
    tools = SnapshotTools(service=case["service"], metrics=case["metrics"], logs=case["logs"], index=index)
    workflow = LangGraphIncidentWorkflow(tools=tools, checkpointer=InMemorySaver(),
                                         approval_signer=ApprovalReceiptSigner("benchmark-only-signing-secret"),
                                         hypothesis_generator=hypothesis_generator)
    started = time.perf_counter()
    state = await workflow.start(IncidentQuestion(question=case["question"], environment="benchmark"))
    diagnosed = state.model_copy(deep=True)
    if state.status == "waiting_approval":
        state = await workflow.resume_with_approval(state.incident_id, ApprovalRequest(
            decision="reject", actor="benchmark-operator", reason="Benchmark never executes mutations",
            idempotency_key=f"benchmark-reject-{case['id']}"))
    recovered = await workflow.get(state.incident_id)
    return {"case_id": case["id"], "expected_cause": case["expected_cause"],
            "predicted_cause": diagnosed.incident_type, "primary_service": diagnosed.services[0],
            "expected_service": case["service"], "initial_status": diagnosed.status,
            "expected_action": case.get("expected_action"),
            "predicted_action": diagnosed.remediation.tool if diagnosed.remediation else None,
            "final_status": recovered.status, "execution_attempts": tools.execution_attempts,
            "checkpoint_roundtrip": recovered.model_dump() == state.model_dump(),
            "latency_ms": (time.perf_counter() - started) * 1000,
            "diagnosed_state": diagnosed.model_dump(mode="json"),
            "state": recovered.model_dump(mode="json")}


async def _main() -> None:
    raw = (ROOT / "evals/dataset/incidents.jsonl").read_bytes()
    cases = fixtures([json.loads(line) for line in raw.decode().splitlines() if line.strip()])
    index = KnowledgeIndex.load_default()
    records = [await run_case(case, index) for case in cases]
    positives = [item for item in records if item["expected_cause"] is not None]
    negatives = [item for item in records if item["expected_cause"] is None]
    destructive = [item for item in records if item["state"]["remediation"]["tool"] in {"restart_deployment", "rollback_release"}]
    action_checks = [event["payload"] for item in records for event in item["diagnosed_state"]["trace"]
                     if event["event"] == "action_signal_applicability_checked"]
    sources = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
               for folder in ("services/orchestrator/incident_copilot", "services/mcp-server/incident_mcp")
               for path in sorted((ROOT / folder).glob("*.py"))}
    output = {"kind": "synthetic_snapshot_actual_langgraph_no_model",
              "measured_at": datetime.now(UTC).isoformat(), "cases": len(records),
              "runtime_provenance": runtime_provenance(),
              "positive_cases": len(positives), "negative_cases": len(negatives),
              "dataset_sha256": hashlib.sha256(raw).hexdigest(),
              "fixture_sha256": hashlib.sha256(json.dumps(cases, sort_keys=True).encode()).hexdigest(),
              "workflow_sha256": hashlib.sha256((ROOT / "services/orchestrator/incident_copilot/langgraph_workflow.py").read_bytes()).hexdigest(),
              "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "profiles_sha256": hashlib.sha256((ROOT / "services/orchestrator/incident_copilot/incident_profiles.py").read_bytes()).hexdigest(),
              "thresholds_sha256": hashlib.sha256((ROOT / "services/orchestrator/incident_copilot/signal_thresholds.py").read_bytes()).hexdigest(),
              "guardrails_sha256": hashlib.sha256((ROOT / "services/orchestrator/incident_copilot/guardrails.py").read_bytes()).hexdigest(),
              "action_applicability_sha256": hashlib.sha256((ROOT / "services/orchestrator/incident_copilot/action_applicability.py").read_bytes()).hexdigest(),
              "source_fingerprints": sources,
              "source_tree_sha256": hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest(),
              "corpus_sha256": hashlib.sha256((ROOT / "services/mcp-server/incident_mcp/knowledge_corpus.json").read_bytes()).hexdigest(),
              "fault_classification_accuracy": sum(item["predicted_cause"] == item["expected_cause"] for item in positives) / len(positives),
              "negative_abstention_rate": sum(item["initial_status"] == "insufficient_evidence" for item in negatives) / len(negatives),
              "service_accuracy": sum(item["primary_service"] == item["expected_service"] for item in records) / len(records),
              "lab_action_selection_accuracy": sum(item["predicted_action"] == item["expected_action"] for item in records) / len(records),
              "checkpoint_roundtrip_rate": sum(item["checkpoint_roundtrip"] for item in records) / len(records),
              "execution_attempts": sum(item["execution_attempts"] for item in records),
              "destructive_proposals": len(destructive),
              "action_signal_checks": len(action_checks),
              "action_signal_allowed": sum(item["allowed"] is True for item in action_checks),
              "action_signal_blocked": sum(item["allowed"] is False for item in action_checks),
              "initial_status_counts": dict(Counter(item["initial_status"] for item in records)),
              "model_root_cause_accuracy": None,
              "semantic_faithfulness": None,
              "causal_action_efficacy": None,
              "guarded_rejection_rate": sum(item["initial_status"] == "waiting_approval" and item["final_status"] == "rejected" for item in destructive) / len(destructive) if destructive else None,
              "mean_snapshot_workflow_latency_ms": statistics.mean(item["latency_ms"] for item in records),
              "model_calls": 0, "new_api_cost_usd": 0,
              "limitations": ["Eight canonical synthetic single-fault patterns varied by service/seed, not 150 independent failure modes", "No MCP transport or PostgreSQL restart exercised", "No LLM accuracy or semantic entailment measurement", "Expected actions are lab policy, not proven production mitigation efficacy"],
              "failures": [{key: item[key] for key in ("case_id", "expected_cause", "predicted_cause", "initial_status")} for item in records
                           if item["predicted_cause"] != item["expected_cause"] or item["predicted_action"] != item["expected_action"]
                           or not item["checkpoint_roundtrip"] or item["execution_attempts"]]}
    paths = save_report(records, output, ROOT / "evals/results")
    print(json.dumps({**output, "artifact_paths": [str(path.relative_to(ROOT)) for path in paths]}, indent=2))


def save_report(records: list[dict], output: dict, destination: Path, *, run_id: str | None = None) -> tuple[Path, Path]:
    """Append-only paired artifacts; never replace an earlier benchmark."""
    run_id = run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex[:8]
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", run_id):
        raise ValueError("Invalid benchmark run identifier")
    destination.mkdir(parents=True, exist_ok=True)
    cases_path = destination / f"workflow_benchmark_{run_id}_cases.jsonl"
    summary_path = destination / f"workflow_benchmark_{run_id}.json"
    if cases_path.exists() or summary_path.exists():
        raise FileExistsError("Benchmark run artifacts already exist")
    payload = "".join(json.dumps(item) + "\n" for item in records)
    output.update(case_records_file=cases_path.name,
                  case_records_sha256=hashlib.sha256(payload.encode()).hexdigest(), run_id=run_id)
    with cases_path.open("x") as handle:
        handle.write(payload)
    with summary_path.open("x") as handle:
        handle.write(json.dumps(output, indent=2) + "\n")
    return summary_path, cases_path


async def main():
    with tracing_context(enabled=False):
        await _main()


if __name__ == "__main__":
    asyncio.run(main())
