from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "mcp-server"))

from run_model_benchmark import (  # noqa: E402
    VARIANTS,
    _benchmark_inputs,
    aggregate,
)


def recover_fifo_request_latencies(
    completion_latencies_ms: list[float],
    *,
    concurrency: int,
) -> list[float]:
    """Remove FIFO semaphore queue time from common-origin completion times."""
    if concurrency < 1:
        raise ValueError("concurrency must be positive")
    completion_events = sorted(completion_latencies_ms)
    recovered: list[float] = []
    for index, completed_at in enumerate(completion_latencies_ms):
        started_at = (
            0.0
            if index < concurrency
            else completion_events[index - concurrency]
        )
        request_latency = completed_at - started_at
        if request_latency < 0:
            raise ValueError("completion ordering is inconsistent with FIFO")
        recovered.append(request_latency)
    return recovered


def normalize_batch(
    *,
    records_by_id: dict[str, dict[str, Any]],
    case_ids: list[str],
    concurrency: int,
) -> None:
    completion_latencies = [
        float(records_by_id[case_id]["usage"]["latency_ms"])
        for case_id in case_ids
    ]
    request_latencies = recover_fifo_request_latencies(
        completion_latencies,
        concurrency=concurrency,
    )
    for case_id, request_latency in zip(case_ids, request_latencies):
        usage = records_by_id[case_id]["usage"]
        usage["queued_batch_latency_ms"] = usage["latency_ms"]
        usage["latency_ms"] = request_latency
        usage["latency_measurement"] = "fifo_semaphore_reconstruction_v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Normalize the 2026-08-08 credentialed benchmark's legacy "
            "queue-inclusive latency without making API calls."
        )
    )
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument(
        "--initial-single-prompt-smoke-size",
        type=int,
        default=3,
    )
    parser.add_argument("--input-usd-per-million", type=float, required=True)
    parser.add_argument("--output-usd-per-million", type=float, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cases_path = ROOT / "evals" / "results" / "model_benchmark_cases.jsonl"
    output_path = ROOT / "evals" / "results" / "model_benchmark.json"
    records = [
        json.loads(line)
        for line in cases_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    normalized = [
        record.get("usage", {}).get("latency_measurement")
        == "fifo_semaphore_reconstruction_v1"
        for record in records
    ]
    if records and all(normalized):
        print("checkpoint latencies are already normalized")
        return
    if any(normalized):
        raise SystemExit("Refusing to normalize a mixed legacy/current file.")
    benchmark_args = argparse.Namespace(limit=150, variants=None)
    (
        all_cases,
        cases,
        _,
        dataset_sha256,
        corpus_sha256,
        benchmark_revision,
    ) = _benchmark_inputs(benchmark_args)
    if len(records) != len(cases) * len(VARIANTS):
        raise SystemExit("Expected a complete 450-record benchmark checkpoint.")

    case_ids = [case["id"] for case in cases]
    records_by_variant = {
        variant: {
            record["case"]["id"]: record
            for record in records
            if record["variant"] == variant
        }
        for variant in VARIANTS
    }
    if any(set(group) != set(case_ids) for group in records_by_variant.values()):
        raise SystemExit("Checkpoint case IDs do not match the evaluation set.")

    smoke_size = args.initial_single_prompt_smoke_size
    if not 1 <= smoke_size < len(cases):
        raise SystemExit("Smoke size must split the single-prompt case set.")
    single_prompt = records_by_variant["single_prompt"]
    normalize_batch(
        records_by_id=single_prompt,
        case_ids=case_ids[:smoke_size],
        concurrency=args.concurrency,
    )
    normalize_batch(
        records_by_id=single_prompt,
        case_ids=case_ids[smoke_size:],
        concurrency=args.concurrency,
    )
    for variant in ("rag", "validated_agent"):
        normalize_batch(
            records_by_id=records_by_variant[variant],
            case_ids=case_ids,
            concurrency=args.concurrency,
        )

    for record in records:
        record["benchmark_revision"] = benchmark_revision
    temporary_cases = cases_path.with_suffix(".jsonl.tmp")
    temporary_cases.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    temporary_cases.replace(cases_path)

    previous = json.loads(output_path.read_text(encoding="utf-8"))
    summaries = [
        aggregate(
            variant=variant,
            records=list(records_by_variant[variant].values()),
            input_price=args.input_usd_per_million,
            output_price=args.output_usd_per_million,
        )
        for variant in VARIANTS
    ]
    output = {
        **previous,
        "benchmark_revision": benchmark_revision,
        "dataset_cases_available": len(all_cases),
        "variants": summaries,
        "dataset_sha256": dataset_sha256,
        "corpus_sha256": corpus_sha256,
        "latency_normalized_at": datetime.now(UTC).isoformat(),
        "latency_provenance": {
            "method": "fifo_semaphore_reconstruction_v1",
            "concurrency": args.concurrency,
            "batches": {
                "single_prompt": [smoke_size, len(cases) - smoke_size],
                "rag": [len(cases)],
                "validated_agent": [len(cases)],
            },
            "raw_field": "usage.queued_batch_latency_ms",
            "normalized_field": "usage.latency_ms",
            "note": (
                "Request elapsed time is reconstructed from measured common-origin "
                "completion times and the runner's FIFO semaphore schedule."
            ),
        },
    }
    output_path.write_text(json.dumps(output, indent=2), encoding="utf-8")

    for summary in summaries:
        latencies = [
            record["usage"]["latency_ms"]
            for record in records_by_variant[summary["variant"]].values()
        ]
        print(
            f"{summary['variant']:<18} "
            f"mean={statistics.mean(latencies):.0f}ms "
            f"p95={summary['p95_diagnostic_latency_ms']:.0f}ms"
        )


if __name__ == "__main__":
    main()
