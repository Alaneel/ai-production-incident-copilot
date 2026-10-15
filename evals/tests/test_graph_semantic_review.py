import copy
import asyncio
import sys
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "evals"))

from review_graph_model_results import prepare, score  # noqa: E402


def record(case_id="case-1", *, expected="database_connection_pool_exhaustion", hypotheses=True):
    return {"case_id": case_id, "ok": True, "model_requests_attempted": 1,
            "expected_cause": expected, "diagnosed_state": {
                "question": "Why is payment-service slow?", "services": ["payment-service"],
                "incident_type": expected, "evidence": [
                    {"id": "metric-1", "observation": "Pool utilization high"},
                    {"id": "log-1", "observation": "Pool acquisition timed out"}],
                "hypotheses": [{"id": "h-1", "rank": 1, "cause": "Database pool pressure",
                                "evidence_ids": ["metric-1", "log-1"], "conflicting_evidence_ids": [],
                                "validation_notes": ["Causality provisional"]}] if hypotheses else []}}


def completed(records):
    reviews = prepare(records)
    for review in reviews:
        review["status"] = "complete"
        review["reviewer"] = "offline-test-reviewer"
        for hypothesis in review["hypotheses"]:
            hypothesis["normalized_cause"] = "database_connection_pool_exhaustion"
            for citation in hypothesis["supporting_citations"]:
                citation.update(verdict="supported", rationale="Observation supports this claim")
    return reviews


def test_pending_reviews_are_not_zeros_or_perfect_scores_and_labels_are_blinded():
    source = [record()]
    reviews = prepare(source)
    assert "expected_cause" not in reviews[0]
    assert "incident_type" not in reviews[0]["context"]
    result = score(source, reviews)
    assert result["reviewed_cases"] == 0
    assert result["review_coverage"] == 0
    assert result["reviewed_subset_root_cause_top1"] is None
    assert result["reviewed_supporting_citation_support_rate"] is None


def test_partial_review_has_explicit_denominators_and_uncertainty_is_not_support():
    source = [record(), record("case-2")]
    reviews = completed(source)
    reviews[1]["status"] = "pending"
    reviews[0]["hypotheses"][0]["supporting_citations"][1]["verdict"] = "uncertain"
    result = score(source, reviews)
    assert result["review_coverage"] == 0.5
    assert result["reviewed_positive_cases"] == 1
    assert result["reviewed_subset_root_cause_top1"] == 1
    assert result["reviewed_supporting_citation_support_rate"] == 0.5
    assert result["uncertain_supporting_citations"] == 1
    assert result["reviewed_nonempty_response_all_supporting_citations_supported_rate"] == 0


def test_abstention_is_not_vacuously_faithful_and_positive_abstention_is_a_miss():
    source = [record(hypotheses=False), record("negative", expected=None, hypotheses=False)]
    result = score(source, completed(source))
    assert result["reviewed_subset_root_cause_top1"] == 0
    assert result["reviewed_subset_root_cause_top3"] == 0
    assert result["reviewed_subset_negative_workflow_abstention_rate"] == 1
    assert result["reviewed_supporting_citation_support_rate"] is None
    assert result["reviewed_nonempty_response_all_supporting_citations_supported_rate"] is None


@pytest.mark.parametrize("mutation", [
    lambda source, reviews: source[0]["diagnosed_state"].update(question="changed source"),
    lambda source, reviews: reviews[0]["context"].update(question="changed worksheet"),
    lambda source, reviews: reviews.append(copy.deepcopy(reviews[0])),
    lambda source, reviews: reviews[0].update(case_id="unknown"),
    lambda source, reviews: reviews[0].update(reviewer=" "),
    lambda source, reviews: reviews[0]["hypotheses"].clear(),
    lambda source, reviews: reviews[0]["hypotheses"][0].update(normalized_cause="invented_label"),
    lambda source, reviews: reviews[0]["hypotheses"][0]["supporting_citations"].pop(),
    lambda source, reviews: reviews[0]["hypotheses"][0]["supporting_citations"][0].update(verdict=None),
    lambda source, reviews: reviews[0]["hypotheses"][0]["supporting_citations"][0].update(rationale=" "),
])
def test_tampering_or_incomplete_reviews_fail_closed(mutation):
    source = [record()]
    reviews = completed(source)
    mutation(source, reviews)
    with pytest.raises(ValueError):
        score(source, reviews)


def test_failed_model_cases_remain_visible_in_coverage_not_silently_dropped():
    source = [record(), {"case_id": "failed", "ok": False, "model_requests_attempted": 1,
                         "error_type": "ModelGenerationError"}]
    assert len(prepare(source)) == 1
    result = score(source, completed(source))
    assert result["source_cases"] == 2
    assert result["failed_source_cases"] == 1
    assert result["review_coverage"] == 0.5


def test_normalized_cause_mismatch_does_not_get_rule_classifier_credit():
    source = [record()]
    reviews = completed(source)
    reviews[0]["hypotheses"][0]["normalized_cause"] = "downstream_timeout"
    result = score(source, reviews)
    assert result["reviewed_subset_root_cause_top1"] == 0
    assert result["reviewed_subset_root_cause_top3"] == 0


def test_ranked_top3_differs_from_top1_without_reordering_hypotheses():
    source = [record()]
    second = copy.deepcopy(source[0]["diagnosed_state"]["hypotheses"][0])
    second.update(id="h-2", rank=2)
    source[0]["diagnosed_state"]["hypotheses"].append(second)
    reviews = completed(source)
    reviews[0]["hypotheses"][0]["normalized_cause"] = "other_or_unclassifiable"
    result = score(source, reviews)
    assert result["reviewed_subset_root_cause_top1"] == 0
    assert result["reviewed_subset_root_cause_top3"] == 1


def test_real_graph_output_can_be_prepared_without_assigning_semantic_scores():
    import httpx
    import json
    from run_graph_model_benchmark import plan, evaluate_case
    from incident_mcp.knowledge import KnowledgeIndex
    from incident_copilot.reasoning import OpenAIResponsesHypothesisGenerator

    async def run():
        _, cases = plan(3)

        def handler(request):
            context = json.loads(json.loads(request.content)["input"][1]["content"][0]["text"])
            metric = next(item["id"] for item in context["evidence"] if item["kind"] == "metric")
            deployment = next(item["id"] for item in context["evidence"] if item["kind"] == "deployment")
            output = {"hypotheses": [{"cause": "Unproven database pool pressure", "confidence": 0.5,
                                       "evidence_ids": [metric, deployment], "conflicting_evidence_ids": [],
                                       "validation_notes": ["Synthetic HTTP fixture, not a semantic review"]}]}
            return httpx.Response(200, json={"status": "completed", "output_text": json.dumps(output)})

        async with httpx.AsyncClient(base_url="https://api.openai.test/v1",
                                    transport=httpx.MockTransport(handler)) as client:
            generator = OpenAIResponsesHypothesisGenerator(api_key="fake", model="local-test", client=client)
            actual = await evaluate_case(cases[0], KnowledgeIndex.load_default(), generator)
        worksheet = prepare([actual])
        assert actual["ok"]
        assert worksheet[0]["hypotheses"]
        summary = score([actual], worksheet)
        assert summary["reviewed_cases"] == 0
        assert summary["reviewed_subset_root_cause_top1"] is None
        assert summary["model_calls"] == 0
        from compare_semantic_reviews import compare
        paired = compare([actual], worksheet, copy.deepcopy(worksheet))
        assert paired["overlap_cases"] == 0
        assert paired["supporting_citation_agreement"]["observed_agreement"] is None
        assert paired["semantic_faithfulness"] is None
        assert paired["model_calls"] == 0
    asyncio.run(run())


def test_cli_prepare_and_score_are_offline_and_do_not_overwrite_source(tmp_path):
    source = tmp_path / "cases.jsonl"
    worksheet = tmp_path / "reviews.jsonl"
    summary = tmp_path / "scores.json"
    source.write_text(json.dumps(record()) + "\n")
    before = source.read_bytes()
    script = ROOT / "evals/review_graph_model_results.py"
    for mode, output, extra in [("prepare", worksheet, []),
                                ("score", summary, ["--reviews", str(worksheet)])]:
        result = subprocess.run([sys.executable, str(script), mode, "--source", str(source),
                                 "--output", str(output), *extra], capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["model_calls"] == 0
    assert source.read_bytes() == before
    assert json.loads(summary.read_text())["reviewed_cases"] == 0
    repeat = subprocess.run([sys.executable, str(script), "prepare", "--source", str(source),
                             "--output", str(source)], capture_output=True, text=True, timeout=10)
    assert repeat.returncode != 0
    assert source.read_bytes() == before
