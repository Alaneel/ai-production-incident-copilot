"""Real LangGraph + model reasoning on snapshots; paid execution is opt-in."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import statistics
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from dotenv import dotenv_values

from run_workflow_benchmark import ROOT, fixtures, run_case
from incident_copilot.reasoning import ModelGenerationError, OpenAIResponsesHypothesisGenerator, REASONING_PROMPT_REVISION
from incident_mcp.knowledge import KnowledgeIndex

CONTRACT = "snapshot-langgraph-model-v1"


def plan(limit: int, max_output_tokens: int = 4096) -> tuple[dict, list[dict]]:
    if not 1 <= limit <= 150:
        raise ValueError("limit must be between 1 and 150")
    if not isinstance(max_output_tokens, int) or isinstance(max_output_tokens, bool) or max_output_tokens <= 0:
        raise ValueError("max_output_tokens must be a positive integer")
    raw = (ROOT / "evals/dataset/incidents.jsonl").read_bytes()
    groups = defaultdict(list)
    for case in fixtures([json.loads(line) for line in raw.decode().splitlines() if line.strip()]):
        groups[case["expected_cause"] or case["id"].split("-")[0]].append(case)
    # Round-robin families avoids a small smoke containing only DB pool cases.
    selected = [items[index] for index in range(15) for items in groups.values()][:limit]
    sources = [Path(__file__), ROOT / "evals/run_workflow_benchmark.py",
               *sorted((ROOT / "services/orchestrator/incident_copilot").glob("*.py")),
               *sorted((ROOT / "services/mcp-server/incident_mcp").glob("*.py")),
               ROOT / "services/mcp-server/incident_mcp/knowledge_corpus.json"]
    return {
        "contract": CONTRACT,
        "prompt_revision": REASONING_PROMPT_REVISION,
        "cases": len(selected),
        "case_ids": [case["id"] for case in selected],
        "family_counts": dict(Counter(case["expected_cause"] or case["id"].split("-")[0] for case in selected)),
        "maximum_api_requests": len(selected),
        "requests_per_case_maximum": 1,
        "max_output_tokens_per_request": max_output_tokens,
        "maximum_requested_output_tokens": len(selected) * max_output_tokens,
        "retries": 0,
        "dataset_sha256": hashlib.sha256(raw).hexdigest(),
        "fixtures_sha256": hashlib.sha256(json.dumps(selected, sort_keys=True).encode()).hexdigest(),
        "source_sha256": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in sources},
        "estimated_cost_usd": None,
        "limitations": ["Synthetic snapshots, not live MCP or PostgreSQL",
                        "Rule classification is not model root-cause accuracy",
                        "Model receives rule-derived incident_type, never scorer ground truth",
                        "Semantic cause and citation entailment require separate review",
                        "Output cap includes non-visible tokens; input tokens and USD billing are not capped",
                        "Configured provider must honor the Responses max_output_tokens contract",
                        "Continuation requires a finalized summary; ambiguous in-flight requests are not replayed"],
    }, selected


class MeasuredGenerator:
    def __init__(self, delegate):
        self.delegate = delegate
        self.calls = 0
        self.usage = []

    async def generate(self, **arguments):
        if self.calls:
            raise RuntimeError("Benchmark permits one generation per case")
        self.calls += 1
        try:
            hypotheses, metadata = await self.delegate.generate(**arguments)
        except ModelGenerationError as error:
            if error.usage_metadata is not None:
                self.usage.append(error.usage_metadata)
            raise
        self.usage.append(metadata)
        return hypotheses, metadata


async def evaluate_case(case, index, generator):
    measured = MeasuredGenerator(generator)
    try:
        record = await run_case(case, index, hypothesis_generator=measured)
        checks = {
            "checkpoint_roundtrip": record["checkpoint_roundtrip"],
            "no_execution": record["execution_attempts"] == 0,
            "never_approved": record["state"]["approval_decision"] != "approve",
            "paused_proposal_rejected": record["initial_status"] != "waiting_approval" or record["final_status"] == "rejected",
        }
        record.update({"ok": all(checks.values()), "checks": checks,
                       "model_requests_attempted": measured.calls, "model_usage": measured.usage,
                       "model_root_cause_top1": None, "semantic_faithfulness": None})
        return record
    except Exception as error:
        # Never retain exception strings, provider response bodies or endpoint URLs.
        return {"case_id": case["id"], "ok": False, "error_type": type(error).__name__,
                "model_requests_attempted": measured.calls, "model_usage": measured.usage,
                "model_root_cause_top1": None, "semantic_faithfulness": None}


def load_continuation(path, manifest):
    previous = json.loads(path.read_text())
    for key in ("contract", "prompt_revision", "dataset_sha256", "fixtures_sha256", "source_sha256", "case_ids", "cases",
                "max_output_tokens_per_request", "maximum_requested_output_tokens"):
        if previous.get(key) != manifest.get(key):
            raise ValueError("Continuation source does not match current benchmark revision/data")
    for key in ("configured_model", "provider_endpoint_sha256"):
        if not previous.get(key):
            raise ValueError("Continuation requires retained model/provider identity")
        if key in manifest and previous[key] != manifest[key]:
            raise ValueError("Continuation model/provider configuration changed")
    source = Path(previous["cases_artifact"])
    raw = source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != previous["cases_sha256"]:
        raise ValueError("Continuation case artifact fingerprint mismatch")
    records = [json.loads(line) for line in raw.decode().splitlines() if line.strip()]
    if [record["case_id"] for record in records] != manifest["case_ids"][:len(records)]:
        raise ValueError("Continuation records must be a unique planned prefix")
    if previous["cases_attempted"] != len(records):
        raise ValueError("Continuation summary is incomplete")
    if any(not isinstance(record.get("ok"), bool)
           or not isinstance(record.get("model_requests_attempted"), int)
           or isinstance(record.get("model_requests_attempted"), bool)
           or record["model_requests_attempted"] not in (0, 1) for record in records):
        raise ValueError("Continuation exceeded per-case request budget")
    return records, {"summary": str(path.resolve()), "summary_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                     "cases_artifact": str(source.resolve()), "cases_sha256": previous["cases_sha256"]}


def reserve_continuation(path):
    # One child run per finalized parent. Retain this reservation after failure:
    # absence of a new summary is an ambiguous request outcome, not retry permission.
    path = path.resolve()
    reservation = path.with_suffix(path.suffix + ".continued")
    with reservation.open("x") as stream:
        stream.write(json.dumps({"reserved_at": datetime.now(UTC).isoformat(), "reservation_id": uuid4().hex}) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


async def execute(manifest, cases, generator, destination, *, retained=None, continuation=None):
    destination.mkdir(parents=True, exist_ok=True)
    prefix = "graph_model_" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex[:8]
    case_path = destination / (prefix + "_cases.jsonl")
    summary_path = destination / (prefix + ".json")
    index = KnowledgeIndex.load_default()
    records = list(retained or [])
    retained_count = len(records)
    # Exclusive creation and flush after each case retain completed work on failure.
    with case_path.open("x") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
        for case in cases[retained_count:]:
            record = await evaluate_case(case, index, generator)
            records.append(record)
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
            if not record["ok"]:
                break  # Never silently retry or spend on the rest of a failing batch.
    completed = [record for record in records if record["ok"]]
    passed = len(completed) == len(cases) and all(
        record["checkpoint_roundtrip"] and record["execution_attempts"] == 0
        and (record["initial_status"] != "waiting_approval" or record["final_status"] == "rejected")
        for record in completed
    )
    summary = {**manifest, "ok": passed, "run_at": datetime.now(UTC).isoformat(),
               "cases_attempted": len(records), "cases_completed": len(completed),
               "continued_cases": retained_count, "continuation": continuation,
               "model_requests_this_invocation": sum(record["model_requests_attempted"] for record in records[retained_count:]),
               "model_requests_attempted": sum(record["model_requests_attempted"] for record in records),
               "model_root_cause_top1": None, "semantic_faithfulness": None,
               "actual_cost_usd": None, "cases_artifact": str(case_path),
               "mean_successful_snapshot_workflow_latency_ms": (
                   statistics.mean(record["latency_ms"] for record in completed) if completed else None),
               "total_tokens": measured_total_tokens(records),
               "cases_sha256": hashlib.sha256(case_path.read_bytes()).hexdigest(),
               "failed_case_ids": [record["case_id"] for record in records if not record["ok"]]}
    with summary_path.open("x") as stream:
        stream.write(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    return summary_path, summary


def measured_total_tokens(records):
    usages = [usage for record in records for usage in record["model_usage"]]
    if len(usages) != sum(record["model_requests_attempted"] for record in records):
        return None  # A failed/ambiguous request might still be billable.
    tokens = [usage.get("total_tokens") for usage in usages]
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in tokens):
        return None
    return sum(tokens)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--confirm-api-cost", action="store_true")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--max-output-tokens", type=int, default=4096,
                        help="Per-request output cap including reasoning tokens; not an input or USD budget")
    parser.add_argument("--continue-from", type=Path, help="Continue unattempted cases from a finalized, matching summary; never retry failed cases")
    args = parser.parse_args()
    try:
        manifest, cases = plan(args.limit, args.max_output_tokens)
    except ValueError as error:
        parser.error(str(error))
    if args.plan:
        if args.continue_from:
            retained, _ = load_continuation(args.continue_from, manifest)
            manifest["remaining_maximum_api_requests"] = len(cases) - len(retained)
            manifest["remaining_maximum_requested_output_tokens"] = (len(cases) - len(retained)) * args.max_output_tokens
            manifest["retained_cases"] = len(retained)
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return  # No .env read, model client or network in planning mode.
    config = {**os.environ, **dotenv_values(ROOT / ".env.local")}
    if not config.get("OPENAI_API_KEY") or not config.get("OPENAI_MODEL"):
        parser.error("Missing OPENAI_API_KEY or OPENAI_MODEL")
    manifest["configured_model"] = config["OPENAI_MODEL"]
    manifest["provider_endpoint_sha256"] = hashlib.sha256(
        (config.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/").encode()).hexdigest()
    retained, continuation = (load_continuation(args.continue_from, manifest)
                              if args.continue_from else ([], None))
    if args.continue_from:
        reserve_continuation(args.continue_from)
    generator = OpenAIResponsesHypothesisGenerator(
        api_key=config["OPENAI_API_KEY"], model=config["OPENAI_MODEL"],
        base_url=config.get("OPENAI_BASE_URL") or "https://api.openai.com/v1",
        max_attempts=1, timeout_seconds=55, max_output_tokens=args.max_output_tokens,
    )

    async def run():
        try:
            return await execute(manifest, cases, generator, ROOT / "evals/results",
                                 retained=retained, continuation=continuation)
        finally:
            await generator.close()

    path, summary = asyncio.run(run())
    print(json.dumps({"ok": summary["ok"], "report": str(path),
                      "cases_completed": summary["cases_completed"],
                      "model_requests_attempted": summary["model_requests_attempted"]}))
    if not summary["ok"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
