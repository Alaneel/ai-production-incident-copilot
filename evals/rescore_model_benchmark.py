"""Recompute scoring from retained outputs. Never constructs a model client."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from retrieval_metrics import METRICS_VERSION
from run_model_benchmark import VARIANTS, aggregate


def rescore(records: list[dict]) -> list[dict]:
    if not records:
        raise ValueError("No retained model outputs to rescore.")
    identity = {(record["model"], record["benchmark_revision"]) for record in records}
    if len(identity) != 1:
        raise ValueError("Cannot combine different models or benchmark revisions.")
    seen = set()
    for record in records:
        key = (record["variant"], record["case"]["id"])
        if key in seen or record["variant"] not in VARIANTS:
            raise ValueError("Duplicate case or unknown variant in retained outputs.")
        seen.add(key)
    return [aggregate(variant=variant,
                      records=[record for record in records if record["variant"] == variant],
                      input_price=None, output_price=None)
            for variant in VARIANTS if any(record["variant"] == variant for record in records)]


def main() -> None:
    root = Path(__file__).parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=root / "results/model_benchmark_cases.jsonl")
    parser.add_argument("--output", type=Path, default=root / "results/model_benchmark_rescored.json")
    args = parser.parse_args()
    if args.output.resolve() in {args.input.resolve(), (root / "results/model_benchmark.json").resolve()}:
        parser.error("Rescoring must not overwrite retained outputs or the historical aggregate.")
    raw = args.input.read_bytes()
    records = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    variants = rescore(records)
    result = {
        "kind": "offline_rescore_not_new_model_run",
        "rescored_at": datetime.now(UTC).isoformat(),
        "metrics_version": METRICS_VERSION,
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "model": records[0]["model"],
        "source_benchmark_revision": records[0]["benchmark_revision"],
        "retained_outputs": len(records),
        "model_calls": 0,
        "new_api_cost_usd": 0,
        "latency_note": "Original retained request latencies; no new diagnostic latency measured.",
        "cost_note": "Historical costs are not repriced. See the original aggregate for its explicit price basis.",
        "variants": variants,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    for variant in variants:
        print(f"{variant['variant']}: cases={variant['cases']} Recall@5={variant['retrieval_recall_at_5']:.4f} NDCG@5={variant['retrieval_ndcg_at_5']:.4f}")
    print(f"Offline rescore saved to {args.output}; model calls=0")


if __name__ == "__main__":
    main()
