import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from retrieval_metrics import ndcg_at_k, recall_at_k  # noqa: E402
from run_benchmark import evaluate  # noqa: E402
from run_model_benchmark import aggregate  # noqa: E402
from policies import Prediction  # noqa: E402
from rescore_model_benchmark import rescore  # noqa: E402


def test_standard_log2_discount_at_second_rank():
    assert ndcg_at_k(["irrelevant", "relevant"], {"relevant"}) == pytest.approx(1 / math.log2(3))


def test_duplicate_relevant_items_cannot_inflate_ndcg():
    assert ndcg_at_k(["a", "a", "a", "a", "a"], {"a"}) == 1
    assert ndcg_at_k(["a", "a", "b"], {"a", "b"}) == pytest.approx(1.5 / (1 + 1 / math.log2(3)))


def test_cutoff_and_empty_relevance():
    assert recall_at_k(["x"] * 5 + ["a"], {"a"}) == 0
    assert ndcg_at_k(["x"] * 5 + ["a"], {"a"}) == 0
    assert recall_at_k(["a", "a"], {"a", "b"}) == 0.5
    assert recall_at_k([], set()) == ndcg_at_k([], set()) == 0


@pytest.mark.parametrize("k", [0, -1, True, 1.5])
def test_invalid_cutoffs_are_rejected(k):
    with pytest.raises(ValueError):
        recall_at_k([], set(), k)
    with pytest.raises(ValueError):
        ndcg_at_k([], set(), k)


def record():
    case = {"id": "case-1", "root_cause": "cause", "service": "payment-service",
            "relevant_evidence": ["a"], "required_tools": [],
            "recommended_action": "none", "requires_approval": False}
    prediction = Prediction(service="payment-service", ranked_causes=["cause"],
                            selected_tools=[], cited_evidence=["a"],
                            retrieved_evidence=["x"] * 5 + ["a"], action="none",
                            requires_human_approval=False, grounded=True)
    return {"model": "fixture", "benchmark_revision": "fixture-revision", "variant": "rag",
            "case": case, "prediction": vars(prediction),
            "usage": {"input_tokens": 10, "output_tokens": 5, "latency_ms": 20}}


def test_both_evaluation_harnesses_enforce_top_five_recall():
    item = record()
    baseline = evaluate("fixture", lambda _: Prediction(**item["prediction"]), [item["case"]])
    model = aggregate(variant="rag", records=[item], input_price=None, output_price=None)
    for result in [baseline, model]:
        assert result["retrieval_recall_at_5"] == result["retrieval_ndcg_at_5"] == 0


def test_offline_rescore_preserves_usage_and_does_not_price_new_calls():
    result = rescore([record()])[0]
    assert result["input_tokens"] == 10
    assert result["mean_diagnostic_latency_ms"] == 20
    assert result["token_cost_usd"] is None


def test_rescore_rejects_duplicates_and_mixed_models():
    first = record()
    with pytest.raises(ValueError, match="Duplicate"):
        rescore([first, first])
    second = record()
    second["model"] = "other"
    with pytest.raises(ValueError, match="different models"):
        rescore([first, second])
