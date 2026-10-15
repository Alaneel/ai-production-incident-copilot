"""Compare independent declared reviews offline; agreement is not accuracy."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import unicodedata

from review_graph_model_results import Review, fingerprint, score

CONTRACT = "graph-semantic-review-agreement-v1"


def agreement(pairs):
    """Unweighted nominal Cohen kappa; undefined denominators remain null."""
    count = len(pairs)
    if not count:
        return {"items": 0, "agreements": 0, "observed_agreement": None,
                "chance_agreement": None, "cohen_kappa": None}
    left = Counter(pair[0] for pair in pairs)
    right = Counter(pair[1] for pair in pairs)
    matches = sum(a == b for a, b in pairs)
    observed = matches / count
    chance = sum(left[label] * right[label] for label in left.keys() | right.keys()) / count**2
    return {"items": count, "agreements": matches, "observed_agreement": observed,
            "chance_agreement": chance,
            "cohen_kappa": (observed - chance) / (1 - chance) if chance < 1 else None}


def identity(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def completed(records, annotations):
    # Reuse source/context/citation integrity and completion validation. Do not
    # compare unfinished verdicts, silently repair rows, or infer reviewer identity.
    summary = score(records, annotations)
    rows = {row.case_id: row for raw in annotations
            if (row := Review.model_validate(raw)).status == "complete"}
    declarations = {identity(row.reviewer) for row in rows.values()}
    if len(declarations) > 1:
        raise ValueError("Each worksheet must have one declared completed reviewer")
    return rows, summary, next(iter(declarations), None)


def compare(records, left_annotations, right_annotations):
    left, left_summary, left_identity = completed(records, left_annotations)
    right, right_summary, right_identity = completed(records, right_annotations)
    if left_annotations and right_annotations and left_summary["contract"] != right_summary["contract"]:
        raise ValueError("Cannot compare different review rubric versions")
    if left_identity is not None and left_identity == right_identity:
        raise ValueError("Agreement requires different declared reviewers")
    overlap = sorted(left.keys() & right.keys())
    causes, citations, conflicts, disagreements = [], [], [], []
    for case_id in overlap:
        for a, b in zip(left[case_id].hypotheses, right[case_id].hypotheses):
            causes.append((a.normalized_cause, b.normalized_cause))
            if a.normalized_cause != b.normalized_cause:
                disagreements.append({"case_id": case_id, "hypothesis_id": a.hypothesis_id,
                    "dimension": "normalized_cause", "left": a.normalized_cause, "right": b.normalized_cause})
            for ca, cb in zip(a.supporting_citations, b.supporting_citations):
                citations.append((ca.verdict, cb.verdict))
                if ca.verdict != cb.verdict:
                    disagreements.append({"case_id": case_id, "hypothesis_id": a.hypothesis_id,
                        "evidence_id": ca.evidence_id, "dimension": "supporting_citation",
                        "left": ca.verdict, "right": cb.verdict,
                        "left_rationale": ca.rationale, "right_rationale": cb.rationale})
            for ca, cb in zip(a.conflicting_citations or [], b.conflicting_citations or []):
                conflicts.append((ca.verdict, cb.verdict))
                if ca.verdict != cb.verdict:
                    disagreements.append({"case_id": case_id, "hypothesis_id": a.hypothesis_id,
                        "evidence_id": ca.evidence_id, "dimension": "conflicting_citation",
                        "left": ca.verdict, "right": cb.verdict,
                        "left_rationale": ca.rationale, "right_rationale": cb.rationale})
    return {"contract": CONTRACT, "source_cases": len(records),
        "reviewable_cases": left_summary["reviewable_cases"],
        "failed_source_cases": left_summary["failed_source_cases"],
        "left_completed_cases": len(left), "right_completed_cases": len(right),
        "overlap_cases": len(overlap), "overlap_case_ids": overlap,
        "overlap_source_coverage": len(overlap) / len(records) if records else None,
        "left_only_completed_case_ids": sorted(left.keys() - right.keys()),
        "right_only_completed_case_ids": sorted(right.keys() - left.keys()),
        "paired_abstention_cases": sum(not left[key].hypotheses for key in overlap),
        "cause_label_agreement": agreement(causes),
        "supporting_citation_agreement": agreement(citations),
        "review_rubric": (left_summary if left_annotations else right_summary)["contract"],
        "conflicting_citation_agreement": agreement(conflicts),
        "uncertain_conflicting_citation_verdicts": {"left": sum(a == "uncertain" for a, _ in conflicts),
                                                   "right": sum(b == "uncertain" for _, b in conflicts)},
        "uncertain_citation_verdicts": {"left": sum(a == "uncertain" for a, _ in citations),
                                        "right": sum(b == "uncertain" for _, b in citations)},
        "disagreement_cases": len({item["case_id"] for item in disagreements}),
        "disagreements": disagreements, "adjudication_status": "not_performed",
        "reviewer_declarations": {"left": left_summary["reviewer_declarations"],
                                  "right": right_summary["reviewer_declarations"]},
        "source_sha256": fingerprint(records), "left_reviews_sha256": fingerprint(left_annotations),
        "right_reviews_sha256": fingerprint(right_annotations),
        "comparison_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "review_validation_sha256": left_summary["scorer_sha256"], "model_calls": 0,
        "semantic_faithfulness": None, "causal_accuracy": None,
        "limitations": ["Agreement measures declared reviewers, not independently authenticated humans or experts",
            "Agreement is not correctness, semantic faithfulness, or causal proof",
            "Only completed overlapping cases are compared; omissions and source failures remain in coverage",
            "Uncertain is its own nominal verdict, never promoted to support",
            "Abstentions add no hypothesis or citation agreement items",
            "Items within a case may be dependent; no confidence interval or independent-sample claim",
            "Omitted counterevidence, conflict resolution and whole-response faithfulness are outside this rubric",
            "No automatic consensus or adjudication; preserve original worksheets"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ("source", "left", "right", "output"):
        parser.add_argument("--" + field, type=Path, required=True)
    args = parser.parse_args()

    def read(path):
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    result = compare(read(args.source), read(args.left), read(args.right))
    with args.output.open("x") as stream:
        stream.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"output": str(args.output.resolve()), "model_calls": 0,
                      "overlap_cases": result["overlap_cases"],
                      "disagreement_cases": result["disagreement_cases"]}))


if __name__ == "__main__":
    main()
