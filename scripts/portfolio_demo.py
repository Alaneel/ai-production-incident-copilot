"""Run ten synthetic incidents through the real compiled workflow, offline."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# This standalone offline entry point must not inherit a shell's SDK tracing.
# Set these before importing LangGraph/LangSmith, including background exporters.
for setting in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING", "LANGCHAIN_TRACING_V2", "LANGSMITH_OTEL_ENABLED"):
    os.environ[setting] = "false"
from langsmith import tracing_context  # noqa: E402
from evals.run_workflow_benchmark import fixtures, run_case  # noqa: E402
from incident_mcp.knowledge import KnowledgeIndex  # noqa: E402
from evals.release_provenance import runtime_provenance  # noqa: E402


async def demonstrate() -> dict:
    dataset = [json.loads(line) for line in (ROOT / "evals/dataset/incidents.jsonl").read_text().splitlines() if line.strip()]
    selected = []
    seen = set()
    for case in fixtures(dataset):
        family = case["expected_cause"] or case["id"].split("-", 1)[0]
        if family not in seen:
            seen.add(family)
            selected.append(case)
    if len(selected) != 10:
        raise ValueError("Expected eight fault families plus healthy and no-data controls")
    index = KnowledgeIndex.load_default()
    rows = []
    for case in selected:
        result = await run_case(case, index)
        passed = (
            result["predicted_cause"] == result["expected_cause"]
            and result["predicted_action"] == case["expected_action"]
            and result["primary_service"] == result["expected_service"]
            and result["checkpoint_roundtrip"]
            and result["execution_attempts"] == 0
            and (result["initial_status"] != "waiting_approval" or result["final_status"] == "rejected")
        )
        rows.append({
            "case": case["expected_cause"] or case["id"].split("-", 1)[0],
            "service": result["primary_service"],
            "diagnosis": result["predicted_cause"],
            "proposal": result["predicted_action"],
            "status_before_decision": result["initial_status"],
            "final_status": result["final_status"],
            "checkpoint_roundtrip": result["checkpoint_roundtrip"],
            "execution_attempts": result["execution_attempts"],
            "passed": passed,
        })
    return {
        "scope": "ten synthetic snapshots; compiled LangGraph; in-memory checkpoints; explicit rejection",
        "source_fingerprints": {
            relative: hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
            for relative in (
                "scripts/portfolio_demo.py", "evals/run_workflow_benchmark.py",
                "evals/dataset/incidents.jsonl",
                "services/orchestrator/incident_copilot/langgraph_workflow.py",
                "services/mcp-server/incident_mcp/knowledge_corpus.json",
            )
        },
        "runtime_provenance": runtime_provenance(),
        "cases": len(rows), "passed": sum(row["passed"] for row in rows),
        "model_calls": 0, "execution_attempts": sum(row["execution_attempts"] for row in rows),
        "limitations": ["No network transport or live observability", "No PostgreSQL durability", "No model-quality or causal-remediation measurement"],
        "results": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Optional local JSON output; existing files are never overwritten")
    arguments = parser.parse_args()
    with tracing_context(enabled=False):
        result = asyncio.run(demonstrate())
    payload = json.dumps(result, indent=2) + "\n"
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        with arguments.output.open("x", encoding="utf-8") as handle:
            handle.write(payload)
    print(payload, end="")
    return 0 if result["passed"] == result["cases"] and result["execution_attempts"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
