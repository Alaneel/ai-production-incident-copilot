import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "evals"))
from compare_semantic_reviews import agreement, compare  # noqa: E402
from review_graph_model_results import prepare  # noqa: E402


def source(case_id="one", *, hypotheses=True):
    return {"case_id": case_id, "ok": True, "model_requests_attempted": 1,
        "expected_cause": "database_connection_pool_exhaustion", "diagnosed_state": {
            "question": "Why is payment-service slow?", "services": ["payment-service"],
            "evidence": [{"id": "metric", "observation": "Pool utilization high"},
                         {"id": "log", "observation": "Connection acquisition timed out"}],
            "hypotheses": [{"id": "hyp", "rank": 1, "cause": "Pool exhaustion",
                "evidence_ids": ["metric", "log"], "conflicting_evidence_ids": [],
                "validation_notes": ["Provisional"]}] if hypotheses else []}}


def reviews(records, reviewer):
    rows = prepare(records)
    for row in rows:
        row.update(status="complete", reviewer=reviewer)
        for hyp in row["hypotheses"]:
            hyp["normalized_cause"] = "database_connection_pool_exhaustion"
            for citation in hyp["supporting_citations"]:
                citation.update(verdict="supported", rationale="Observation supports the provisional symptom")
    return rows


def test_nominal_kappa_matches_manual_table_and_constant_denominator_is_undefined():
    result = agreement([("yes", "yes"), ("yes", "no"), ("no", "no"), ("no", "no")])
    assert result == {"items": 4, "agreements": 3, "observed_agreement": 0.75,
                      "chance_agreement": 0.5, "cohen_kappa": 0.5}
    assert agreement([("same", "same")])["cohen_kappa"] is None
    assert agreement([])["observed_agreement"] is None


def test_disagreements_are_exact_and_never_automatically_adjudicated():
    records = [source()]
    a, b = reviews(records, "Alice"), reviews(records, "Bob")
    b[0]["hypotheses"][0]["normalized_cause"] = "cpu_saturation"
    b[0]["hypotheses"][0]["supporting_citations"][1].update(verdict="uncertain", rationale="Need more detail")
    before = copy.deepcopy((records, a, b))
    result = compare(records, a, b)
    assert result["cause_label_agreement"]["observed_agreement"] == 0
    assert result["supporting_citation_agreement"]["observed_agreement"] == 0.5
    assert result["uncertain_citation_verdicts"] == {"left": 0, "right": 1}
    assert result["disagreement_cases"] == 1
    assert result["disagreements"][1]["evidence_id"] == "log"
    assert result["disagreements"][1]["right_rationale"] == "Need more detail"
    assert result["adjudication_status"] == "not_performed"
    assert result["semantic_faithfulness"] is result["causal_accuracy"] is None
    assert result["model_calls"] == 0
    assert before == (records, a, b)


def test_pending_missing_and_failed_cases_stay_in_denominators():
    records = [source(), source("two"), source("three"),
               {"case_id": "failed", "ok": False, "model_requests_attempted": 1}]
    a, b = reviews(records, "Alice"), reviews(records, "Bob")
    a[1]["status"] = "pending"
    b = b[:2]
    result = compare(records, a, b)
    assert result["overlap_cases"] == 1
    assert result["overlap_source_coverage"] == 0.25
    assert result["failed_source_cases"] == 1
    assert result["left_only_completed_case_ids"] == ["three"]
    assert result["right_only_completed_case_ids"] == ["two"]


def test_empty_overlap_and_abstentions_never_produce_perfect_quality():
    records = [source(hypotheses=False)]
    result = compare(records, reviews(records, "Alice"), reviews(records, "Bob"))
    assert result["paired_abstention_cases"] == 1
    assert result["cause_label_agreement"]["items"] == 0
    assert result["supporting_citation_agreement"]["observed_agreement"] is None
    assert compare(records, prepare(records), prepare(records))["overlap_cases"] == 0
    assert compare([], [], [])["overlap_source_coverage"] is None


@pytest.mark.parametrize("name", ["Alice", " ALICE ", "Ａｌｉｃｅ"])
def test_same_declared_reviewer_is_not_independent(name):
    records = [source()]
    with pytest.raises(ValueError, match="different declared"):
        compare(records, reviews(records, "Alice"), reviews(records, name))


def test_mixed_completed_reviewers_cannot_be_silently_pooled():
    records = [source(), source("two")]
    a = reviews(records, "Alice")
    a[1]["reviewer"] = "Carol"
    with pytest.raises(ValueError, match="one declared"):
        compare(records, a, reviews(records, "Bob"))


@pytest.mark.parametrize("mutation", [
    lambda rows: rows[0].update(record_sha256="0" * 64),
    lambda rows: rows[0]["context"].update(question="altered"),
    lambda rows: rows.append(copy.deepcopy(rows[0])),
    lambda rows: rows[0]["hypotheses"][0]["supporting_citations"].pop(),
    lambda rows: rows[0]["hypotheses"][0]["supporting_citations"][0].update(verdict=None),
    lambda rows: rows[0]["hypotheses"].clear(),
])
def test_underlying_review_integrity_is_not_bypassed(mutation):
    records = [source()]
    b = reviews(records, "Bob")
    mutation(b)
    with pytest.raises(ValueError):
        compare(records, reviews(records, "Alice"), b)


def test_cli_is_offline_preserves_inputs_and_refuses_existing_output(tmp_path):
    records = [source()]
    paths = [tmp_path / name for name in ("source.jsonl", "left.jsonl", "right.jsonl")]
    for path, rows in zip(paths, (records, reviews(records, "Alice"), reviews(records, "Bob"))):
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    before = [path.read_bytes() for path in paths]
    output = tmp_path / "agreement.json"
    command = [sys.executable, str(ROOT / "evals/compare_semantic_reviews.py"),
        "--source", str(paths[0]), "--left", str(paths[1]), "--right", str(paths[2]), "--output", str(output)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["model_calls"] == 0
    report = output.read_bytes()
    assert json.loads(report)["adjudication_status"] == "not_performed"
    assert subprocess.run(command, capture_output=True, text=True, timeout=10).returncode != 0
    assert output.read_bytes() == report
    assert [path.read_bytes() for path in paths] == before
