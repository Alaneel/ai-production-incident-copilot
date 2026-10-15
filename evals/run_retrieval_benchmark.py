from __future__ import annotations

import hashlib
import json
import statistics
import sys
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "mcp-server"))

from incident_mcp.knowledge import KnowledgeIndex  # noqa: E402
from retrieval_metrics import METRICS_VERSION, ndcg_at_k, recall_at_k  # noqa: E402

RELEVANT_URIS = {
    "database_connection_pool_exhaustion": {
        "runbook://payments/db-pool-saturation",
        "incident://INC-2026-041/payment-pool-exhaustion",
        "doc://platform/database-pool-capacity",
    },
    "downstream_timeout": {
        "runbook://platform/downstream-timeouts",
        "incident://INC-2026-027/risk-timeout",
    },
    "cpu_saturation": {
        "runbook://platform/cpu-saturation",
        "incident://INC-2026-019/risk-cpu-regression",
    },
    "memory_leak": {
        "runbook://platform/memory-growth",
        "incident://INC-2026-012/catalog-memory-leak",
    },
    "deployment_regression": {
        "runbook://delivery/deployment-regression",
        "incident://INC-2026-036/checkout-regression",
    },
    "kafka_consumer_lag": {
        "runbook://streaming/consumer-lag",
        "incident://INC-2026-023/ledger-hot-partition",
    },
    "retry_storm": {
        "runbook://resilience/retry-storm",
        "incident://INC-2026-033/inventory-retry-storm",
    },
    "cache_penetration": {
        "runbook://caching/cache-penetration",
        "incident://INC-2026-039/catalog-cache-penetration",
    },
}


def safe_divide(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def evaluate_cases(index: KnowledgeIndex, cases: list[dict]) -> dict:
    measurements: list[dict] = []
    by_fault: dict[str, list[dict]] = defaultdict(list)
    for case in cases:
        query = " ".join(
            [
                case["question"],
                *[item.replace("_", " ") for item in case["relevant_evidence"]],
            ]
        )
        started = time.perf_counter()
        results = index.search(
            service=case["service"],
            query=query,
            content_types={"runbook", "past_incident", "technical_doc"},
            limit=5,
        )
        latency_ms = (time.perf_counter() - started) * 1_000
        retrieved = [item["uri"] for item in results]
        relevant = RELEVANT_URIS[case["root_cause"]]
        measurement = {
            "case_id": case["id"],
            "fault": case["root_cause"],
            "recall_at_5": recall_at_k(retrieved, relevant, 5),
            "ndcg_at_5": ndcg_at_k(retrieved, relevant, 5),
            "latency_ms": latency_ms,
        }
        measurements.append(measurement)
        by_fault[case["root_cause"]].append(measurement)

    latencies = [item["latency_ms"] for item in measurements]
    return {
        "benchmark": "bm25-incident-knowledge-v1",
        "measured_at": datetime.now(UTC).isoformat(),
        "cases": len(cases),
        "k": 5,
        "recall_at_5": statistics.mean(
            item["recall_at_5"] for item in measurements
        ),
        "ndcg_at_5": statistics.mean(
            item["ndcg_at_5"] for item in measurements
        ),
        "mean_latency_ms": statistics.mean(latencies),
        "p95_latency_ms": sorted(latencies)[round(0.95 * (len(latencies) - 1))],
        "by_fault": {
            fault: {
                "cases": len(items),
                "recall_at_5": statistics.mean(
                    item["recall_at_5"] for item in items
                ),
                "ndcg_at_5": statistics.mean(
                    item["ndcg_at_5"] for item in items
                ),
            }
            for fault, items in sorted(by_fault.items())
        },
        "query_contract": (
            "service + incident question + retrieved telemetry evidence labels; "
            "the root-cause label is not included in the query"
        ),
        "token_cost_usd": None,
        "token_cost_note": "N/A: deterministic local retrieval, no model call",
    }


def main() -> None:
    dataset_path = ROOT / "evals" / "dataset" / "incidents.jsonl"
    corpus_path = (
        ROOT
        / "services"
        / "mcp-server"
        / "incident_mcp"
        / "knowledge_corpus.json"
    )
    cases = [
        json.loads(line)
        for line in dataset_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    result = evaluate_cases(KnowledgeIndex.load_default(), cases)
    result["metrics_version"] = METRICS_VERSION
    result["dataset_sha256"] = hashlib.sha256(dataset_path.read_bytes()).hexdigest()
    result["corpus_sha256"] = hashlib.sha256(corpus_path.read_bytes()).hexdigest()
    output = ROOT / "evals" / "results" / "retrieval_benchmark.json"
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(
        f"{result['cases']} cases | "
        f"Recall@5={result['recall_at_5']:.1%} | "
        f"NDCG@5={result['ndcg_at_5']:.1%} | "
        f"mean={result['mean_latency_ms']:.3f}ms"
    )
    print(f"wrote measured results to {output}")


if __name__ == "__main__":
    main()
