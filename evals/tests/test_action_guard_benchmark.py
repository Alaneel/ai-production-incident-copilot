import asyncio
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "evals"))
from run_action_guard_benchmark import SCENARIOS, run_challenge  # noqa: E402
from run_workflow_benchmark import fixtures  # noqa: E402
from incident_mcp.knowledge import KnowledgeIndex  # noqa: E402


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_actual_graph_adversarial_fixture_contract_and_known_semantic_gap(scenario):
    case = next(item for item in fixtures([json.loads(line) for line in
        (ROOT / "evals/dataset/incidents.jsonl").read_text().splitlines()])
        if item["expected_cause"] == "database_connection_pool_exhaustion")
    report = asyncio.run(run_challenge(case, scenario, KnowledgeIndex.load_default()))
    assert report["ok"] and all(report["checks"].values())
    assert report["execution_attempts"] == report["model_calls"] == 0
    if scenario == "wrong_text_correct_signals":
        assert report["diagnosed_state"]["hypotheses"][0]["cause"] == "CPU saturation"
        assert report["semantic_mismatch_rejected"] == (
            report["diagnosed_state"]["remediation"]["tool"] == "none" and report["dry_run_attempts"] == 0)
        assert report["semantic_entailment_verified"] is False
        assert report["semantic_mismatch_rejected"]
        assert report["cause_family_check"]["status"] == "explicit_family_mismatch"
    if scenario == "unrecognized_cause_wording":
        assert report["cause_family_check"]["status"] == "unrecognized"
        assert report["semantic_entailment_verified"] is False
    if scenario in {"unrecognized_cause_wording", "negated_cause", "multiple_cause_families"}:
        assert report["dry_run_attempts"] == 0
        assert report["diagnosed_state"]["remediation"]["tool"] == "none"
        assert report["initial_status"] == "diagnosed"
    if scenario.endswith("pool_samples"):
        check = next(event["payload"] for event in report["diagnosed_state"]["trace"]
                     if event["event"] == "diagnostic_signal_time_checked")
        assert check["window_seconds"] == 300
        assert report["diagnosed_state"]["incident_type"] is None
        assert report["dry_run_attempts"] == 0
        assert report["initial_status"] == "diagnosed"
