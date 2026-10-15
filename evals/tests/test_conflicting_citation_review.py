"""Offline rubric extension fixtures are not expert reviews or model results."""
import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "evals"))
from compare_semantic_reviews import compare  # noqa: E402
from review_graph_model_results import CONFLICT_CONTRACT, prepare, score  # noqa: E402


def source():
    return [{"case_id": "conflict-fixture", "ok": True, "model_requests_attempted": 1,
             "expected_cause": "database_connection_pool_exhaustion", "diagnosed_state": {
                 "question": "Why is payment slow?", "services": ["payment-service"],
                 "evidence": [{"id": "timeout", "observation": "Connection acquisition timed out"},
                              {"id": "healthy", "observation": "Pool utilization was low during the same window"}],
                 "hypotheses": [{"id": "h1", "rank": 1, "cause": "Pool exhaustion",
                                 "evidence_ids": ["timeout"], "conflicting_evidence_ids": ["healthy"],
                                 "validation_notes": ["Contradictory observations remain unresolved"]}]}}]


def reviews(records, reviewer="fixture-reviewer-a", *, v2=True):
    rows = prepare(records, review_conflicts=v2)
    for row in rows:
        row.update(status="complete", reviewer=reviewer)
        for hyp in row["hypotheses"]:
            hyp["normalized_cause"] = "database_connection_pool_exhaustion"
            for citation in hyp["supporting_citations"] + hyp.get("conflicting_citations", []):
                citation.update(verdict="supported", rationale="Fixture verdict, not a human judgment")
    return rows


def test_conflict_rubric_is_explicit_pending_and_blinded():
    records = source()
    rows = prepare(records, review_conflicts=True)
    assert rows[0]["contract"] == CONFLICT_CONTRACT
    assert rows[0]["hypotheses"][0]["conflicting_citations"] == [
        {"evidence_id": "healthy", "verdict": None, "rationale": ""}]
    assert "expected_cause" not in rows[0]["context"]
    result = score(records, rows)
    assert result["conflicting_citation_review_enabled"]
    assert result["reviewed_conflicting_citations"] == 0
    assert result["reviewed_conflicting_citation_support_rate"] is None
    assert result["reviewed_cases"] == 0


@pytest.mark.parametrize("verdict,expected", [("supported", 1), ("not_supported", 0), ("uncertain", 0)])
def test_roles_have_separate_denominators_and_uncertainty_is_not_credit(verdict, expected):
    records = source()
    rows = reviews(records)
    rows[0]["hypotheses"][0]["conflicting_citations"][0]["verdict"] = verdict
    result = score(records, rows)
    assert result["reviewed_supporting_citation_support_rate"] == 1
    assert result["reviewed_conflicting_citation_support_rate"] == expected
    assert result["reviewed_conflicting_citations"] == 1
    assert result["uncertain_conflicting_citations"] == (verdict == "uncertain")
    assert result["model_calls"] == 0


@pytest.mark.parametrize("mutation", [
    lambda row: row["hypotheses"][0].pop("conflicting_citations"),
    lambda row: row["hypotheses"][0]["conflicting_citations"].clear(),
    lambda row: row["hypotheses"][0]["conflicting_citations"].append(
        copy.deepcopy(row["hypotheses"][0]["conflicting_citations"][0])),
    lambda row: row["hypotheses"][0]["conflicting_citations"][0].update(evidence_id="timeout"),
    lambda row: row["hypotheses"][0]["conflicting_citations"][0].update(verdict=None),
    lambda row: row["hypotheses"][0]["conflicting_citations"][0].update(rationale=" "),
    lambda row: row.update(contract="graph-semantic-review-v1"),
])
def test_missing_tampered_or_incomplete_conflict_reviews_fail_closed(mutation):
    records = source()
    rows = reviews(records)
    mutation(rows[0])
    with pytest.raises(ValueError):
        score(records, rows)


@pytest.mark.parametrize("conflicts", [["missing"], ["timeout"]])
def test_source_conflict_ids_must_be_retained_and_have_unambiguous_roles(conflicts):
    records = source()
    records[0]["diagnosed_state"]["hypotheses"][0]["conflicting_evidence_ids"] = conflicts
    with pytest.raises(ValueError):
        prepare(records, review_conflicts=True)


def test_legacy_reviews_do_not_gain_conflict_credit_or_silently_upgrade():
    records = source()
    rows = reviews(records, v2=False)
    result = score(records, rows)
    assert not result["conflicting_citation_review_enabled"]
    assert result["reviewed_conflicting_citation_support_rate"] is None
    assert "conflicting_citations" not in rows[0]["hypotheses"][0]
    for value in (None, []):
        changed = copy.deepcopy(rows)
        changed[0]["hypotheses"][0]["conflicting_citations"] = value
        with pytest.raises(ValueError, match="v2 rubric"):
            score(records, changed)


def test_no_conflicts_and_abstentions_do_not_earn_perfect_conflict_scores():
    records = source()
    records[0]["diagnosed_state"]["hypotheses"][0]["conflicting_evidence_ids"] = []
    assert score(records, reviews(records))["reviewed_conflicting_citation_support_rate"] is None
    records[0]["diagnosed_state"]["hypotheses"] = []
    assert score(records, reviews(records))["reviewed_conflicting_citation_support_rate"] is None


def test_partial_conflict_reviews_and_source_failures_keep_coverage_visible():
    records = source()
    second = copy.deepcopy(records[0])
    second["case_id"] = "pending-fixture"
    records.extend([second, {"case_id": "failed-fixture", "ok": False, "model_requests_attempted": 1}])
    rows = reviews(records)
    rows[1]["status"] = "pending"
    result = score(records, rows)
    assert result["review_coverage"] == 1 / 3
    assert result["reviewable_cases"] == 2
    assert result["failed_source_cases"] == 1
    assert result["reviewed_conflicting_citations"] == 1


def test_conflict_order_is_bound_to_source_and_duplicates_are_normalized_once():
    records = source()
    state = records[0]["diagnosed_state"]
    state["evidence"].append({"id": "another", "observation": "Another bounded counterobservation"})
    state["hypotheses"][0]["conflicting_evidence_ids"] = ["healthy", "another", "healthy"]
    rows = reviews(records)
    assert [item["evidence_id"] for item in rows[0]["hypotheses"][0]["conflicting_citations"]] == ["healthy", "another"]
    assert score(records, rows)["reviewed_conflicting_citations"] == 2
    rows[0]["hypotheses"][0]["conflicting_citations"].reverse()
    with pytest.raises(ValueError, match="conflicting citation exactly once"):
        score(records, rows)


def test_comparison_keeps_conflict_disagreements_separate_without_mutating_inputs():
    records = source()
    a, b = reviews(records), reviews(records, "fixture-reviewer-b")
    b[0]["hypotheses"][0]["conflicting_citations"][0].update(
        verdict="uncertain", rationale="Fixture uncertainty")
    before = copy.deepcopy((records, a, b))
    report = compare(records, a, b)
    assert report["review_rubric"] == CONFLICT_CONTRACT
    assert report["supporting_citation_agreement"]["observed_agreement"] == 1
    assert report["conflicting_citation_agreement"]["observed_agreement"] == 0
    assert report["uncertain_conflicting_citation_verdicts"] == {"left": 0, "right": 1}
    assert report["disagreements"][0]["dimension"] == "conflicting_citation"
    assert report["disagreements"][0]["evidence_id"] == "healthy"
    assert report["semantic_faithfulness"] is report["causal_accuracy"] is None
    assert before == (records, a, b)


def test_versions_cannot_be_mixed_in_scoring_or_compared():
    records = source()
    a, b = reviews(records), reviews(records, "fixture-reviewer-b", v2=False)
    with pytest.raises(ValueError, match="rubric versions"):
        score(records, [*a, *b])
    with pytest.raises(ValueError, match="rubric versions"):
        compare(records, a, b)


def test_cli_explicit_v2_is_offline_preserves_inputs_and_refuses_overwrite(tmp_path):
    records = source()
    path, worksheet, output = [tmp_path / name for name in ("source.jsonl", "reviews.jsonl", "scores.json")]
    path.write_text(json.dumps(records[0]) + "\n")
    before = path.read_bytes()
    script = str(ROOT / "evals/review_graph_model_results.py")
    command = [sys.executable, script, "prepare", "--review-conflicts", "--source", str(path),
               "--output", str(worksheet)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["model_calls"] == 0
    assert json.loads(worksheet.read_text())["contract"] == CONFLICT_CONTRACT
    original_worksheet = worksheet.read_bytes()
    assert subprocess.run(command, capture_output=True, text=True, timeout=10).returncode != 0
    assert worksheet.read_bytes() == original_worksheet
    result = subprocess.run([sys.executable, script, "score", "--source", str(path),
                             "--reviews", str(worksheet), "--output", str(output)],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert json.loads(output.read_text())["reviewed_conflicting_citation_support_rate"] is None
    assert path.read_bytes() == before
