"""Prepare blinded review worksheets and score retained graph-model outputs offline."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from run_workflow_benchmark import FAULT_SIGNALS

CONTRACT = "graph-semantic-review-v1"
CONFLICT_CONTRACT = "graph-semantic-review-v2"
CAUSES = set(FAULT_SIGNALS) | {"other_or_unclassifiable"}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


class CitationReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence_id: str
    verdict: Literal["supported", "not_supported", "uncertain"] | None
    rationale: str


class HypothesisReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hypothesis_id: str
    normalized_cause: str | None
    supporting_citations: list[CitationReview]
    conflicting_citations: list[CitationReview] | None = None


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contract: Literal["graph-semantic-review-v1", "graph-semantic-review-v2"]
    case_id: str
    record_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["pending", "complete"]
    reviewer: str | None
    hypotheses: list[HypothesisReview]
    context: dict


def index_records(records):
    indexed = {}
    for record in records:
        key = record["case_id"]
        if key in indexed:
            raise ValueError("Duplicate source case ID")
        if not isinstance(record["ok"], bool) or "model_requests_attempted" not in record:
            raise ValueError("Source must be graph-model case records")
        if record["ok"]:
            if record["expected_cause"] is not None and record["expected_cause"] not in FAULT_SIGNALS:
                raise ValueError("Unknown expected root-cause label")
            state = record["diagnosed_state"]
            evidence_ids = [item["id"] for item in state["evidence"]]
            hypotheses = state["hypotheses"]
            if len(set(evidence_ids)) != len(evidence_ids):
                raise ValueError("Ambiguous retained evidence IDs")
            if len({item["id"] for item in hypotheses}) != len(hypotheses):
                raise ValueError("Duplicate retained hypothesis IDs")
            if [item["rank"] for item in hypotheses] != list(range(1, len(hypotheses) + 1)):
                raise ValueError("Hypotheses must retain consecutive rank order")
            if any(not set(item["evidence_ids"]).issubset(evidence_ids) for item in hypotheses):
                raise ValueError("Unretained supporting citation")
            if any(not set(item["conflicting_evidence_ids"]).issubset(evidence_ids) for item in hypotheses):
                raise ValueError("Unretained conflicting citation")
            if any(set(item["evidence_ids"]) & set(item["conflicting_evidence_ids"]) for item in hypotheses):
                raise ValueError("Citation cannot be both supporting and conflicting for one hypothesis")
        indexed[key] = record
    return indexed


def context_for(record):
    state = record["diagnosed_state"]
    # Deliberately omit expected_cause, rule-derived incident_type, model identity
    # and confidence, to reduce scorer-label and model-reputation bias.
    return {"question": state["question"], "services": state["services"],
            "evidence": state["evidence"],
            "hypotheses": [{key: hypothesis[key] for key in (
                "id", "rank", "cause", "evidence_ids", "conflicting_evidence_ids", "validation_notes")}
                for hypothesis in state["hypotheses"]]}


def prepare(records, *, review_conflicts=False):
    indexed = index_records(records)
    reviews = []
    for case_id, record in indexed.items():
        if not record["ok"]:
            continue
        reviews.append({"contract": CONFLICT_CONTRACT if review_conflicts else CONTRACT, "case_id": case_id,
                        "record_sha256": fingerprint(record), "status": "pending", "reviewer": None,
                        "context": context_for(record),
                        "hypotheses": [{"hypothesis_id": hypothesis["id"], "normalized_cause": None,
                                        "supporting_citations": [{"evidence_id": evidence_id,
                                                                 "verdict": None, "rationale": ""}
                                                                for evidence_id in dict.fromkeys(hypothesis["evidence_ids"])]}
                                       for hypothesis in record["diagnosed_state"]["hypotheses"]]})
        if review_conflicts:
            for item, original in zip(reviews[-1]["hypotheses"], record["diagnosed_state"]["hypotheses"]):
                item["conflicting_citations"] = [
                    {"evidence_id": evidence_id, "verdict": None, "rationale": ""}
                    for evidence_id in dict.fromkeys(original["conflicting_evidence_ids"])]
    return reviews


def score(records, annotations):
    indexed = index_records(records)
    seen = set()
    reviewed = []
    contracts = {Review.model_validate(raw).contract for raw in annotations}
    if len(contracts) > 1:
        raise ValueError("Cannot mix review rubric versions")
    contract = next(iter(contracts), CONTRACT)
    for raw in annotations:
        review = Review.model_validate(raw)
        if review.case_id in seen:
            raise ValueError("Duplicate review case ID")
        seen.add(review.case_id)
        record = indexed.get(review.case_id)
        if record is None or not record["ok"]:
            raise ValueError("Review refers to missing or failed source case")
        if review.record_sha256 != fingerprint(record):
            raise ValueError("Review fingerprint does not match source record")
        if review.context != context_for(record):
            raise ValueError("Review context differs from retained observations")
        originals = record["diagnosed_state"]["hypotheses"]
        if [item.hypothesis_id for item in review.hypotheses] != [item["id"] for item in originals]:
            raise ValueError("Review must retain every hypothesis in original rank order")
        for item, original in zip(review.hypotheses, originals):
            ids = [citation.evidence_id for citation in item.supporting_citations]
            if ids != list(dict.fromkeys(original["evidence_ids"])):
                raise ValueError("Review must retain each supporting citation exactly once")
            if review.contract == CONFLICT_CONTRACT:
                if item.conflicting_citations is None or [citation.evidence_id for citation in item.conflicting_citations] != list(dict.fromkeys(original["conflicting_evidence_ids"])):
                    raise ValueError("Review must retain each conflicting citation exactly once")
            elif "conflicting_citations" in item.model_fields_set:
                raise ValueError("Conflicting citation review requires the v2 rubric")
            if item.normalized_cause is not None and item.normalized_cause not in CAUSES:
                raise ValueError("Unknown normalized root-cause label")
        if review.status == "pending":
            continue
        if not review.reviewer or not review.reviewer.strip():
            raise ValueError("Completed review requires a declared reviewer")
        for item in review.hypotheses:
            if item.normalized_cause is None:
                raise ValueError("Completed review requires every root-cause label")
            for citation in item.supporting_citations + (item.conflicting_citations or []):
                if citation.verdict is None or len(citation.rationale.strip()) < 3:
                    raise ValueError("Completed review requires every citation verdict and rationale")
        reviewed.append((record, review))
    positives = [(record, review) for record, review in reviewed if record["expected_cause"] is not None]
    negatives = [(record, review) for record, review in reviewed if record["expected_cause"] is None]
    citations = [citation for _, review in reviewed for item in review.hypotheses for citation in item.supporting_citations]
    nonempty = [review for _, review in reviewed if review.hypotheses]
    conflicts = [citation for _, review in reviewed for item in review.hypotheses
                 for citation in (item.conflicting_citations or [])]

    def rate(values):
        return sum(values) / len(values) if values else None

    return {"contract": contract, "source_cases": len(records),
            "failed_source_cases": sum(not record["ok"] for record in records),
            "reviewable_cases": sum(record["ok"] for record in records),
            "reviewed_cases": len(reviewed), "reviewed_positive_cases": len(positives),
            "reviewed_negative_cases": len(negatives),
            "review_coverage": len(reviewed) / len(records) if records else None,
            "reviewed_subset_root_cause_top1": rate([
                bool(review.hypotheses) and review.hypotheses[0].normalized_cause == record["expected_cause"]
                for record, review in positives]),
            "reviewed_subset_root_cause_top3": rate([
                record["expected_cause"] in [item.normalized_cause for item in review.hypotheses[:3]]
                for record, review in positives]),
            "reviewed_subset_negative_workflow_abstention_rate": rate([not review.hypotheses for _, review in negatives]),
            "reviewed_supporting_citations": len(citations),
            "uncertain_supporting_citations": sum(item.verdict == "uncertain" for item in citations),
            "reviewed_supporting_citation_support_rate": rate([item.verdict == "supported" for item in citations]),
            "conflicting_citation_review_enabled": contract == CONFLICT_CONTRACT,
            "reviewed_conflicting_citations": len(conflicts),
            "uncertain_conflicting_citations": sum(item.verdict == "uncertain" for item in conflicts),
            "reviewed_conflicting_citation_support_rate": rate([item.verdict == "supported" for item in conflicts]),
            "reviewed_nonempty_response_all_supporting_citations_supported_rate": rate([
                bool([citation for item in review.hypotheses for citation in item.supporting_citations])
                and all(citation.verdict == "supported" for item in review.hypotheses for citation in item.supporting_citations)
                for review in nonempty]),
            "reviewer_declarations": sorted({review.reviewer for _, review in reviewed}),
            "source_sha256": fingerprint(records), "reviews_sha256": fingerprint(annotations),
            "scorer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "model_calls": 0,
            "limitations": ["Metrics apply only to the reviewed subset; coverage is mandatory",
                            "Reviewer identity is declared, not independently verified",
                            "Normalized free-text cause label matching is not proof of causality",
                            "Scored hypotheses are post-validation; rejected hypotheses count as workflow abstention",
                            "Citation roles are reviewed separately; omitted counterevidence, conflict resolution and full response faithfulness are not scored",
                            "No inter-reviewer calibration or consensus is established"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["prepare", "score"])
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reviews", type=Path)
    parser.add_argument("--review-conflicts", action="store_true",
                        help="Prepare a v2 worksheet with separate conflicting-citation review")
    args = parser.parse_args()
    if args.mode == "score" and args.reviews is None:
        parser.error("--reviews is required for scoring")
    if args.mode == "prepare" and args.reviews is not None:
        parser.error("--reviews only applies to scoring")
    if args.mode == "score" and args.review_conflicts:
        parser.error("--review-conflicts only applies to preparation; scoring uses the worksheet rubric")
    records = [json.loads(line) for line in args.source.read_text().splitlines() if line.strip()]
    if args.mode == "prepare":
        output = "".join(json.dumps(item, ensure_ascii=False) + "\n"
                         for item in prepare(records, review_conflicts=args.review_conflicts))
    else:
        reviews = [json.loads(line) for line in args.reviews.read_text().splitlines() if line.strip()]
        output = json.dumps(score(records, reviews), ensure_ascii=False, indent=2) + "\n"
    # Do not overwrite source reports or an existing human review worksheet.
    with args.output.open("x") as stream:
        stream.write(output)
    print(json.dumps({"output": str(args.output.resolve()), "model_calls": 0}))


if __name__ == "__main__":
    main()
