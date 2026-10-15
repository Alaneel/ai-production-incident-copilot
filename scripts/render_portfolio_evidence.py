"""Verify retained benchmark provenance, then render a bounded evidence graphic."""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, datetime
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evals.release_provenance import locked_runtime_versions, release_recipe_fingerprints  # noqa: E402
WORKFLOW = 'evals/results/workflow_benchmark_20261014T150511Z_55a6e188.json'
CROSS = 'evals/results/cross_service_benchmark_20261014T150505Z_390ff20a414f45cca5456e475ed6767f.json'


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verified(relative: str) -> tuple[dict, list[dict]]:
    path = ROOT / relative
    value = json.loads(path.read_text())
    sources = value["source_fingerprints"]
    if not sources:
        raise ValueError("Missing source fingerprints")
    if hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest() != value["source_tree_sha256"]:
        raise ValueError("Source-tree fingerprint mismatch")
    for source, expected in sources.items():
        candidate = (ROOT / source).resolve()
        if not candidate.is_relative_to(ROOT.resolve()) or not candidate.is_file() or sha(candidate) != expected:
            raise ValueError(f"Source changed or unavailable: {source}")
    if relative == WORKFLOW:
        artifacts = {
            "runner_sha256": "evals/run_workflow_benchmark.py",
            "dataset_sha256": "evals/dataset/incidents.jsonl",
            "corpus_sha256": "services/mcp-server/incident_mcp/knowledge_corpus.json",
        }
        for field, artifact in artifacts.items():
            if sha(ROOT / artifact) != value[field]:
                raise ValueError(f"Recorded input changed: {artifact}")
    else:
        required = {"evals/run_cross_service_benchmark.py",
                    "services/mcp-server/incident_mcp/knowledge_corpus.json"}
        if not required <= sources.keys() or value["source_fingerprints_after"] != sources or value["source_stable_during_run"] is not True:
            raise ValueError("Cross-service runner/corpus provenance is incomplete or unstable")
    runtime = value["runtime_provenance"]
    if runtime["release_recipe_sha256"] != release_recipe_fingerprints(root=ROOT):
        raise ValueError("Declared release image/recipe inputs changed; offline results do not execute these images")
    for field, artifact in {
        "runtime_lock_sha256": "requirements-runtime.lock",
        "build_lock_sha256": "requirements-build.lock",
        "provenance_helper_sha256": "evals/release_provenance.py",
    }.items():
        if sha(ROOT / artifact) != runtime[field]:
            raise ValueError(f"Recorded runtime input changed: {artifact}")
    environment = runtime["marker_environment"]
    required = {"implementation_name", "implementation_version", "os_name", "platform_machine", "platform_release",
                "platform_system", "platform_version", "platform_python_implementation", "python_full_version",
                "python_version", "sys_platform"}
    if (not isinstance(environment, dict) or set(environment) != required
            or any(not isinstance(value, str) for value in environment.values())
            or runtime["python"] != "3.13.16" or environment["python_full_version"] != "3.13.16"
            or environment["python_version"] != "3.13"
            or runtime["installed_runtime_versions"] != locked_runtime_versions(environment, root=ROOT)):
        raise ValueError("Recorded runtime versions/marker scope do not match the exact release lock")
    case_path = (path.parent / value["case_records_file"]).resolve()
    if not case_path.is_relative_to(path.parent.resolve()) or not case_path.is_file():
        raise ValueError("Case records must remain in the benchmark directory")
    if sha(case_path) != value["case_records_sha256"]:
        raise ValueError(f"Case record hash mismatch: {relative}")
    if value["failures"] or value["model_calls"] != 0:
        raise ValueError("Benchmark scope no longer matches the bounded graphic")
    with case_path.open() as handle:
        records = [json.loads(line) for line in handle if line.strip()]
    if len(records) != value["cases"]:
        raise ValueError("Recorded case count differs from its summary")
    return value, records


def main() -> None:
    (workflow, workflow_records), (cross, cross_records) = verified(WORKFLOW), verified(CROSS)
    if workflow["runtime_provenance"] != cross["runtime_provenance"]:
        raise ValueError("Paired runs used different recorded runtimes")
    if workflow["execution_attempts"] != 0 or cross["mutation_attempts"] != 0:
        raise ValueError("Unexpected mutation attempts")
    if workflow["cases"] != 150 or cross["cases"] != 32:
        raise ValueError("Unexpected retained benchmark size")
    if workflow["fault_classification_accuracy"] != 1 or workflow["negative_abstention_rate"] != 1 or workflow["checkpoint_roundtrip_rate"] != 1 or cross["all_contract_checks_pass_rate"] != 1:
        raise ValueError("Benchmark metrics changed; update the presentation explicitly")
    positive = sum(record["expected_cause"] is not None for record in workflow_records)
    negative = len(workflow_records) - positive
    destructive = [record for record in workflow_records
                   if record["state"]["remediation"]["tool"] in {"restart_deployment", "rollback_release"}]
    rejected = sum(record["initial_status"] == "waiting_approval" and record["final_status"] == "rejected"
                   for record in destructive)
    if (workflow["positive_cases"] != positive or workflow["negative_cases"] != negative
            or workflow["destructive_proposals"] != len(destructive)
            or not destructive or rejected != len(destructive)
            or workflow["guarded_rejection_rate"] != rejected / len(destructive)):
        raise ValueError("Fault/control/proposal counts or explicit rejection contract changed")
    if any(record["execution_attempts"] != 0 or record["checkpoint_roundtrip"] is not True
           or record["predicted_cause"] != record["expected_cause"]
           or record["predicted_action"] != record["expected_action"]
           or record["primary_service"] != record["expected_service"] for record in workflow_records):
        raise ValueError("Workflow case records contradict the rendered contracts")
    if any(record["ok"] is not True or record["mutation_attempts"] != 0 or record["model_calls"] != 0
           or not record["checks"] or not all(value is True for value in record["checks"].values()) for record in cross_records):
        raise ValueError("Cross-service case records contradict the rendered contracts")
    asset = ROOT / "docs/assets/evidence.svg"
    data = {
        "measured_at": [workflow["measured_at"], cross["measured_at"]],
        "source_scope_verified": True,
        "source_fingerprint_counts": [len(workflow["source_fingerprints"]), len(cross["source_fingerprints"])],
        "artifacts": [WORKFLOW, CROSS],
        "single_fault_snapshots": {"cases": workflow["cases"], "fault_cases": workflow["positive_cases"], "controls": workflow["negative_cases"], "failures": len(workflow["failures"]), "checkpoint_roundtrip_rate": workflow["checkpoint_roundtrip_rate"], "rejected_destructive_proposals": workflow["destructive_proposals"], "guarded_rejection_rate": workflow["guarded_rejection_rate"]},
        "cross_service_snapshots": {"cases": cross["cases"], "contract_pass_rate": cross["all_contract_checks_pass_rate"]},
        "model_calls": 0, "mutation_attempts": 0,
        "limitations": ["Synthetic snapshots, not independent real failure modes", "No model accuracy, semantic faithfulness or remediation efficacy", "No live MCP transport or PostgreSQL restart in these runs"],
    }
    measured_date = datetime.fromisoformat(workflow["measured_at"]).astimezone(UTC).date().isoformat()
    if measured_date != datetime.fromisoformat(cross["measured_at"]).astimezone(UTC).date().isoformat():
        raise ValueError("Presentation expects paired runs from the same UTC day")
    data["runtime_provenance_verified"] = True
    data["runtime_provenance"] = workflow["runtime_provenance"]
    cards = [
        (str(len(workflow_records)), "single-fault snapshots", f"{positive} fault cases + {negative} controls", "0 contract failures"),
        (str(len(cross_records)), "cross-service cases", "direction, timing and ambiguity", "all structural contracts passed"),
        (str(rejected), "destructive proposals rejected", "explicit rejection at the interrupt", "0 mutation attempts"),
    ]
    svg = ['<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="420" viewBox="0 0 1200 420" role="img" aria-labelledby="title desc">', '<title id="title">Retained synthetic workflow evidence</title>', f'<desc id="desc">{measured_date} UTC snapshots: {len(workflow_records)} single-fault cases, {len(cross_records)} cross-service cases, {rejected} rejected destructive proposals. No model calls or mutation attempts. Source and case-record hashes verified.</desc>', '<rect width="1200" height="420" rx="22" fill="#111a2b"/>', '<g font-family="Arial, sans-serif">', '<text x="40" y="48" font-size="25" fill="#edf2fa" font-weight="700">Synthetic workflow evidence</text>', f'<text x="40" y="78" font-size="16" fill="#b6c6db">{measured_date} UTC · source, lock, runtime and case-record provenance verified</text>']
    for i,(number,label,line,foot) in enumerate(cards):
        x = 40 + i * 380
        svg += [f'<rect x="{x}" y="106" width="360" height="210" rx="14" fill="#1b2941"/>',f'<text x="{x+22}" y="169" font-size="50" fill="#82d8cf" font-weight="700">{number}</text>',f'<text x="{x+22}" y="210" font-size="20" fill="#edf2fa">{escape(label)}</text>',f'<text x="{x+22}" y="244" font-size="16" fill="#b6c6db">{escape(line)}</text>',f'<text x="{x+22}" y="285" font-size="16" fill="#b6c6db">{escape(foot)}</text>']
    svg += ['<text x="40" y="353" font-size="17" fill="#d9e2ef">0 model calls · 0 mutation attempts · in-memory checkpoint round trips</text>', '<text x="40" y="386" font-size="16" fill="#b6c6db">Scope: deterministic contracts. Model quality, live RCA and causal repair efficacy are unmeasured.</text>', '</g></svg>']
    asset.write_text('\n'.join(svg)+'\n')
    asset.with_suffix('.json').write_text(json.dumps(data,indent=2)+'\n')
    print(f"Verified {len(workflow['source_fingerprints'])} workflow / {len(cross['source_fingerprints'])} cross-service fingerprints, exact runtime inputs and case-record hashes.")
    print("Rendered docs/assets/evidence.svg and docs/assets/evidence.json")


if __name__ == "__main__":
    main()
