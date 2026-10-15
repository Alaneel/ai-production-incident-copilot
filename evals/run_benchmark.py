from __future__ import annotations

import json
import statistics
import time
from pathlib import Path

from policies import POLICIES
from retrieval_metrics import METRICS_VERSION, ndcg_at_k, recall_at_k


def safe_divide(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def evaluate(name: str, policy, cases: list[dict]) -> dict:
    top1 = top3 = service = grounded = faithful = 0
    actions_correct = approvals_correct = 0
    unsafe_rejected = unsafe_total = 0
    destructive_total = destructive_guarded = 0
    evidence_precisions: list[float] = []
    retrieval_recalls: list[float] = []
    ndcgs: list[float] = []
    tool_accuracies: list[float] = []
    latencies_ms: list[float] = []

    for case in cases:
        started = time.perf_counter()
        prediction = policy(case)
        latencies_ms.append((time.perf_counter() - started) * 1000)

        top1 += prediction.ranked_causes[:1] == [case["root_cause"]]
        top3 += case["root_cause"] in prediction.ranked_causes[:3]
        service += prediction.service == case["service"]
        relevant = set(case["relevant_evidence"])
        cited = set(prediction.cited_evidence)
        retrieved = set(prediction.retrieved_evidence)
        citation_is_faithful = bool(cited) and cited.issubset(retrieved)
        faithful += citation_is_faithful
        grounded += citation_is_faithful and bool(cited & relevant)
        evidence_precisions.append(safe_divide(len(cited & relevant), len(cited)))
        retrieval_recalls.append(recall_at_k(prediction.retrieved_evidence, relevant, 5))
        ndcgs.append(ndcg_at_k(prediction.retrieved_evidence, relevant))

        expected_tools = set(case["required_tools"])
        predicted_tools = set(prediction.selected_tools)
        intersection = len(expected_tools & predicted_tools)
        union = len(expected_tools | predicted_tools)
        tool_accuracies.append(safe_divide(intersection, union))
        actions_correct += prediction.action == case["recommended_action"]
        approvals_correct += (
            prediction.requires_human_approval == case["requires_approval"]
        )

        if case["requires_approval"]:
            unsafe_total += 1
            unsafe_rejected += (
                prediction.action == "none" or prediction.requires_human_approval
            )
        if prediction.action in {"restart_deployment", "rollback_release"}:
            destructive_total += 1
            destructive_guarded += prediction.requires_human_approval

    count = len(cases)
    return {
        "policy": name,
        "metrics_version": METRICS_VERSION,
        "cases": count,
        "service_identification_accuracy": service / count,
        "root_cause_top1": top1 / count,
        "root_cause_top3": top3 / count,
        "evidence_precision": statistics.mean(evidence_precisions),
        "retrieval_recall_at_5": statistics.mean(retrieval_recalls),
        "retrieval_ndcg_at_5": statistics.mean(ndcgs),
        "grounded_response_rate": grounded / count,
        "citation_faithfulness_rate": faithful / count,
        "tool_selection_jaccard": statistics.mean(tool_accuracies),
        "remediation_action_accuracy": actions_correct / count,
        "approval_policy_accuracy": approvals_correct / count,
        "unsafe_action_rejection_rate": safe_divide(unsafe_rejected, unsafe_total),
        "destructive_action_guardrail_rate": (
            safe_divide(destructive_guarded, destructive_total)
            if destructive_total
            else 1.0
        ),
        "unguarded_destructive_actions": destructive_total - destructive_guarded,
        "mean_policy_latency_ms": statistics.mean(latencies_ms),
        "p95_policy_latency_ms": sorted(latencies_ms)[round(0.95 * (count - 1))],
        "token_cost_usd": None,
        "token_cost_per_incident_usd": None,
        "token_cost_note": "N/A: deterministic local policy, no model call",
    }


def main() -> None:
    root = Path(__file__).parent
    dataset_path = root / "dataset" / "incidents.jsonl"
    if not dataset_path.exists():
        raise SystemExit("Dataset missing. Run evals/generate_dataset.py first.")
    cases = [
        json.loads(line)
        for line in dataset_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    results = [
        evaluate(name, policy, cases) for name, policy in POLICIES.items()
    ]
    output = root / "results" / "benchmark.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(results, indent=2), encoding="utf-8")

    print(
        "policy                          top1   top3   evidence  unsafe  latency(ms)"
    )
    for result in results:
        print(
            f"{result['policy']:<31} "
            f"{result['root_cause_top1']:>5.1%} "
            f"{result['root_cause_top3']:>6.1%} "
            f"{result['evidence_precision']:>8.1%} "
            f"{result['unsafe_action_rejection_rate']:>7.1%} "
            f"{result['mean_policy_latency_ms']:>11.4f}"
        )
    print(f"\nwrote measured results to {output}")


if __name__ == "__main__":
    main()
