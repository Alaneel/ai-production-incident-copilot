import asyncio
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "evals"))

from run_workflow_benchmark import FAULT_SIGNALS, SnapshotTools, fixtures, run_case, save_report  # noqa: E402
from incident_mcp.knowledge import KnowledgeIndex  # noqa: E402


def cases():
    return fixtures([json.loads(line) for line in (ROOT / "evals/dataset/incidents.jsonl").read_text().splitlines()])


def test_report_artifacts_are_run_bound_hashed_and_cannot_overwrite_history(tmp_path):
    import hashlib
    records = [{"case_id": "one", "model_calls": 0}]
    summary, cases_path = save_report(records, {"cases": 1}, tmp_path, run_id="fixture-run")
    report = json.loads(summary.read_text())
    assert report["case_records_file"] == cases_path.name
    assert report["case_records_sha256"] == hashlib.sha256(cases_path.read_bytes()).hexdigest()
    original = summary.read_bytes(), cases_path.read_bytes()
    with pytest.raises(FileExistsError):
        save_report([{"case_id": "replacement"}], {}, tmp_path, run_id="fixture-run")
    assert original == (summary.read_bytes(), cases_path.read_bytes())
    with pytest.raises(ValueError):
        save_report(records, {}, tmp_path, run_id="../outside")


def test_balanced_fixture_contract_includes_real_negative_cases():
    data = cases()
    assert len(data) == 150
    for fault in FAULT_SIGNALS:
        assert sum(case["expected_cause"] == fault for case in data) == 15
    assert sum(case["expected_cause"] is None for case in data) == 30
    assert any(not case["metrics"] and not case["logs"] for case in data)


@pytest.mark.parametrize("fault", FAULT_SIGNALS)
def test_current_workflow_diagnoses_snapshot_then_rejects_any_production_proposal(fault):
    case = next(case for case in cases() if case["expected_cause"] == fault)
    record = asyncio.run(run_case(case, KnowledgeIndex.load_default()))
    assert record["predicted_cause"] == fault
    assert record["checkpoint_roundtrip"]
    assert record["execution_attempts"] == 0
    assert record["predicted_action"] == case["expected_action"]
    if record["state"]["remediation"]["tool"] != "none":
        check, = [item["payload"] for item in record["diagnosed_state"]["trace"]
                  if item["event"] == "action_signal_applicability_checked"]
        assert check["allowed"]
        assert record["initial_status"] == "waiting_approval"
        assert record["final_status"] == "rejected"
    else:
        assert record["final_status"] == "diagnosed"


@pytest.mark.parametrize("kind", ["healthy", "no_data"])
def test_negative_snapshots_abstain_even_with_retrieved_knowledge_and_deployments(kind):
    case = next(case for case in cases() if case["id"].startswith(kind))
    record = asyncio.run(run_case(case, KnowledgeIndex.load_default()))
    assert record["initial_status"] == "insufficient_evidence"
    assert record["state"]["hypotheses"] == []
    assert record["state"]["remediation"]["tool"] == "none"
    assert record["execution_attempts"] == 0


def test_snapshot_adapter_contains_no_ground_truth_and_cannot_execute():
    tools = SnapshotTools(service="payment-service", metrics={}, logs=[], index=KnowledgeIndex.load_default())
    assert not hasattr(tools, "expected_cause")
    with pytest.raises(RuntimeError, match="forbids execution"):
        asyncio.run(tools.rollback_release(dry_run=False))
    assert tools.execution_attempts == 1


@pytest.mark.parametrize("question", [
    "Investigate payment-service CPU saturation",
    "Investigate payment-service database connection pool exhaustion",
    "Investigate payment-service Kafka consumer lag",
    "Investigate payment-service retry storm",
    "Investigate payment-service cache penetration",
    "Investigate payment-service deployment regression",
    "Investigate payment-service memory leak",
    "Investigate payment-service downstream timeout",
])
def test_user_fault_label_does_not_override_healthy_real_observations(question):
    case = next(case for case in cases() if case["id"] == "healthy-00").copy()
    case["question"] = question
    case["metrics"] = {**case["metrics"], "cpu_utilization": [0.6] * 5,
                       "kafka_consumer_lag": [50] * 5, "retry_rate": [0.5] * 5,
                       "cache_miss_rate": [0.5] * 5}
    record = asyncio.run(run_case(case, KnowledgeIndex.load_default()))
    assert record["initial_status"] == "insufficient_evidence"
    assert record["state"]["hypotheses"] == []
    assert record["state"]["remediation"]["tool"] == "none"
    assert record["execution_attempts"] == 0


@pytest.mark.parametrize("case_id", ["healthy-01", "healthy-02"])
def test_moderate_and_transient_pressure_abstain_in_full_workflow(case_id):
    case = next(case for case in cases() if case["id"] == case_id)
    record = asyncio.run(run_case(case, KnowledgeIndex.load_default()))
    assert record["initial_status"] == "insufficient_evidence"
    assert record["state"]["remediation"]["tool"] == "none"
