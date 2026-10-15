"""Actual graph with adversarial protocol fixtures, not adversarial LLM evaluation."""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from run_workflow_benchmark import ROOT, SnapshotTools, fixtures, save_report
from incident_copilot.approval import ApprovalReceiptSigner
from incident_copilot.langgraph_workflow import LangGraphIncidentWorkflow
from incident_copilot.models import ApprovalRequest, Hypothesis, IncidentQuestion
from incident_mcp.knowledge import KnowledgeIndex
from langgraph.checkpoint.memory import InMemorySaver


SCENARIOS = {
    "valid_signal": "approval",
    "valid_low_confidence": "approval",
    "healthy_unrelated_metric": "blocked",
    "generic_latency_deployment": "blocked",
    "metrics_only": "blocked",
    "missing_id": "blocked",
    "duplicate_citation": "blocked",
    "unresolved_conflict": "blocked",
    "foreign_service": "blocked",
    "event_timing_only": "blocked",
    "missing_rank_one": "blocked",
    "ambiguous_rank_one": "blocked",
    # Measure the known mismatch and unrecognized wording separately.
    "wrong_text_correct_signals": "semantic_gap_probe",
    "unrecognized_cause_wording": "blocked",
    "negated_cause": "blocked",
    "multiple_cause_families": "blocked",
    "stale_pool_samples": "blocked",
    "future_pool_samples": "blocked",
    "untimed_pool_samples": "blocked",
    "duplicate_pool_samples": "blocked",
    "gapped_pool_samples": "blocked",
}


class ChallengeTools(SnapshotTools):
    def __init__(self, **kwargs):
        self.scenario = kwargs.pop("scenario", None)
        super().__init__(**kwargs)
        self.dry_run_attempts = 0

    async def query_metrics(self, service, metrics):
        items = await super().query_metrics(service, metrics)
        for item in items:
            if item.query != "db_pool_active_ratio":
                continue
            now = datetime.now(UTC)
            offsets = {"stale_pool_samples": (600, 585), "future_pool_samples": (-60, -45),
                       "duplicate_pool_samples": (15, 15), "gapped_pool_samples": (250, 15)}
            if self.scenario in offsets:
                item.attributes["series"] = [{"timestamp": (now - timedelta(seconds=offset)).isoformat(), "value": 1}
                                             for offset in offsets[self.scenario]]
            elif self.scenario == "untimed_pool_samples":
                item.attributes["series"] = [{"value": 1}, {"value": 1}]
        return items

    async def search_incident_knowledge(self, service, query):
        items = await super().search_incident_knowledge(service, query)
        foreign = self.evidence("metric", "Foreign pool signal", "Synthetic foreign signal",
            query="db_pool_active_ratio", attributes={"series": [{"value": 1}, {"value": 1}]})
        foreign.service = "ledger-db"
        timing = self.evidence("deployment_event", "Synthetic event timing", "Event interval overlaps samples; not causal proof",
            attributes={"data_origin": "synthetic", "causation_verified": False})
        return [*items, foreign, timing]

    async def rollback_release(self, **arguments):
        self.dry_run_attempts += arguments.get("dry_run") is True
        return await super().rollback_release(**arguments)

    async def restart_deployment(self, **arguments):
        self.dry_run_attempts += arguments.get("dry_run") is True
        return await super().restart_deployment(**arguments)


class ChallengeGenerator:
    """Fixed bad/good hypotheses using observed IDs; no hidden cause labels."""
    def __init__(self, scenario):
        self.scenario, self.calls = scenario, 0

    async def generate(self, *, evidence, **kwargs):
        self.calls += 1
        def select(kind, query=None, foreign=False):
            return next(item.id for item in evidence if item.kind.value == kind
                        and (query is None or item.query == query)
                        and (item.service == "ledger-db") == foreign)
        pool = select("metric", "db_pool_active_ratio")
        log = select("log")
        latency = select("metric", "request_latency_p95")
        ids, conflicts, rank = [pool, log], [], 1
        if self.scenario == "healthy_unrelated_metric":
            ids = [select("metric", "cpu_utilization"), log]
        elif self.scenario == "generic_latency_deployment":
            ids = [latency, select("deployment")]
        elif self.scenario == "metrics_only":
            ids = [pool, latency]
        elif self.scenario == "missing_id":
            ids = [pool, "ev-not-retained"]
        elif self.scenario == "duplicate_citation":
            ids = [pool, pool]
        elif self.scenario == "unresolved_conflict":
            conflicts = [latency]
        elif self.scenario == "foreign_service":
            ids = [select("metric", "db_pool_active_ratio", foreign=True), log]
        elif self.scenario == "event_timing_only":
            ids = [latency, select("deployment_event")]
        elif self.scenario == "missing_rank_one":
            rank = 2
        cause = {
            "wrong_text_correct_signals": "CPU saturation",
            "unrecognized_cause_wording": "Processing threads monopolize compute cycles",
            "negated_cause": "No pool exhaustion is observed",
            "multiple_cause_families": "Pool exhaustion plus CPU saturation",
        }.get(self.scenario, "Provisional pool exhaustion")
        hypothesis = Hypothesis(rank=rank, cause=cause, confidence=0.01 if self.scenario == "valid_low_confidence" else 1.0,
            evidence_ids=ids, conflicting_evidence_ids=conflicts)
        hypotheses = [hypothesis]
        if self.scenario == "ambiguous_rank_one":
            hypotheses.append(hypothesis.model_copy(update={"id": "hyp-duplicate-rank"}))
        return hypotheses, {"generator": "fixed_adversarial_protocol_fixture", "model_calls": 0}


async def run_challenge(case: dict, scenario: str, index: KnowledgeIndex) -> dict:
    tools = ChallengeTools(service=case["service"], metrics=case["metrics"], logs=["request completed"], index=index,
                           scenario=scenario)
    generator = ChallengeGenerator(scenario)
    workflow = LangGraphIncidentWorkflow(tools=tools, checkpointer=InMemorySaver(), hypothesis_generator=generator,
        approval_signer=ApprovalReceiptSigner("adversarial-benchmark-only-secret"))
    state = await workflow.start(IncidentQuestion(question=case["question"], environment="benchmark"))
    initial = state.model_copy(deep=True)
    if state.status.value == "waiting_approval":
        state = await workflow.resume_with_approval(state.incident_id, ApprovalRequest(decision="reject",
            actor="offline-benchmark-operator", reason="No benchmark mutation allowed",
            idempotency_key=f"challenge-{case['id']}-{scenario}"))
    restored = await workflow.get(state.incident_id)
    expectation = SCENARIOS[scenario]
    blocked = initial.remediation.tool == "none" and initial.status.value in {"diagnosed", "insufficient_evidence"}
    checks = {"one_fixture_generation": generator.calls == 1,
              "checkpoint_roundtrip": restored == state, "no_execution": tools.execution_attempts == 0,
              "no_approval": state.approval_decision != "approve"}
    if expectation == "blocked":
        checks.update(blocked_before_dry_run=blocked and tools.dry_run_attempts == 0,
                      no_approval_interrupt=not any(item.event == "workflow_interrupted" for item in initial.trace))
    elif expectation == "approval":
        checks.update(positive_control_reaches_approval=initial.status.value == "waiting_approval" and tools.dry_run_attempts == 1,
                      paused_action_rejected=state.status.value == "rejected")
    return {"case_id": f"{case['id']}:{scenario}", "scenario": scenario, "expectation": expectation,
            "ok": all(checks.values()), "checks": checks, "initial_status": initial.status.value,
            "final_status": state.status.value, "dry_run_attempts": tools.dry_run_attempts,
            "execution_attempts": tools.execution_attempts,
            "semantic_mismatch_rejected": blocked and tools.dry_run_attempts == 0
                if expectation == "semantic_gap_probe" else None,
            "cause_family_check": next((item.payload.get("cause_family_check") for item in initial.trace
                if item.event == "action_signal_applicability_checked"), None),
            "semantic_entailment_verified": False, "model_calls": 0,
            "diagnosed_state": initial.model_dump(mode="json"), "state": restored.model_dump(mode="json")}


async def main():
    raw = (ROOT / "evals/dataset/incidents.jsonl").read_bytes()
    selected = [item for item in fixtures([json.loads(line) for line in raw.decode().splitlines() if line.strip()])
                if item["expected_cause"] == "database_connection_pool_exhaustion"][:5]
    records = [await run_challenge(case, scenario, KnowledgeIndex.load_default())
               for case in selected for scenario in SCENARIOS]
    negative = [item for item in records if item["expectation"] == "blocked"]
    controls = [item for item in records if item["expectation"] == "approval"]
    probes = [item for item in records if item["expectation"] == "semantic_gap_probe"]
    unresolved = [item for item in records if item["scenario"] == "unrecognized_cause_wording"]
    sources = [Path(__file__), ROOT / "evals/run_workflow_benchmark.py",
               *sorted((ROOT / "services/orchestrator/incident_copilot").glob("*.py")),
               *sorted((ROOT / "services/mcp-server/incident_mcp").glob("*.py"))]
    output = {"kind": "adversarial_citation_actual_graph_no_model", "measured_at": datetime.now(UTC).isoformat(),
        "cases": len(records), "negative_cases": len(negative), "positive_controls": len(controls),
        "semantic_gap_probes": len(probes), "contract_failures": [item["case_id"] for item in records if not item["ok"]],
        "unrecognized_wording_probes": len(unresolved),
        "unrecognized_wording_still_proposes_action": sum(item["initial_status"] == "waiting_approval" for item in unresolved),
        "blocked_before_dry_run": sum(item["checks"]["blocked_before_dry_run"] for item in negative),
        "positive_controls_paused_then_rejected": sum(item["ok"] for item in controls),
        "semantic_gap_probes_not_rejected": sum(not item["semantic_mismatch_rejected"] for item in probes),
        "execution_attempts": sum(item["execution_attempts"] for item in records), "model_calls": 0,
        "model_prompt_injection_resistance": None, "semantic_faithfulness": None,
        "dataset_sha256": hashlib.sha256(raw).hexdigest(),
        "fixtures_sha256": hashlib.sha256(json.dumps(selected, sort_keys=True).encode()).hexdigest(),
        "source_fingerprints": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources},
        "limitations": ["21 hand-authored protocol templates over five canonical pool snapshots, not 105 independent attacks",
            "No LLM, real MCP transport, live telemetry or PostgreSQL restart",
            "Known-family phrase contradictions and unrecognized wording are measured separately; this is not general semantic validation",
            "Only workflow citation/action guard behavior is measured; no model robustness or causal efficacy claim"]}
    run_id = "action_guard_" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex[:8]
    paths = save_report(records, output, ROOT / "evals/results", run_id=run_id)
    print(json.dumps({key: value for key, value in {**output, "artifacts": [str(path.relative_to(ROOT)) for path in paths]}.items()
                      if key != "source_fingerprints"}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
