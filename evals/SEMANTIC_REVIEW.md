# Offline semantic review protocol

This workflow scores **retained post-validation hypotheses** from
`graph_model_*_cases.jsonl`. It does not silently rescore the historical
prompt-only comparison, assume an LLM judge is a human, or run a model.

Prepare a new worksheet after a separately authorized graph-model run:

```bash
.venv/bin/python evals/review_graph_model_results.py prepare \
  --source evals/results/graph_model_<run>_cases.jsonl \
  --output evals/results/graph_model_<run>_reviews.jsonl
```

Use a fresh output filename: the tool refuses to overwrite reports or worksheets.
Every row starts `pending`. The worksheet includes the incident question,
retained observations and ordered hypothesis text. Expected labels, classifier
labels, model identity and confidence are deliberately omitted from its context.
This is limited blinding, not a guarantee against all label clues in observations.

For each hypothesis, reviewers map its actual free text to one of these labels:

- `database_connection_pool_exhaustion`
- `downstream_timeout`
- `cpu_saturation`
- `memory_leak`
- `deployment_regression`
- `kafka_consumer_lag`
- `retry_storm`
- `cache_penetration`
- `other_or_unclassifiable`

Do not label from the incident question or rule classifier. Preserve hypothesis
order. Normalization classifies what the hypothesis says, not whether the cause
is proved. A wrong but recognizable claim still receives its corresponding
label; use `other_or_unclassifiable` for genuinely unmappable text.

For **every supporting citation**, provide a verdict and short rationale:

- `supported`: the retained observation supports the specific claim being made,
  with its service, incident window and qualifications. Example: a connection
  acquisition timeout log supports a claim that requests waited for connections.
- `not_supported`: unrelated service/time, unavailable data, or a citation that
  does not support the asserted claim. Example: a deployment timestamp alone
  does not prove that release caused a database connection leak.
- `uncertain`: insufficient detail to decide. Example: a pool utilization metric
  suggests pressure, but without timeout events or pool limits it cannot confirm
  exhaustion. Uncertainty is reported separately and does not earn support.

Runbooks and historical incidents provide context, not proof of today's cause.
Distinguish observed symptoms from claims of causal ordering. Conflict citations
remain visible for context but are **not scored** in this first protocol; neither
are omission, answer relevance, whole-response faithfulness or verified causality.
These require separate rubric extensions rather than inflated supporting-citation
scores. The optional v2 extension below reviews listed conflicting citations;
it does not evaluate omitted counterevidence or how conflicts were resolved.

Leave unresolved cases `pending`; only set `complete` when all labels/verdicts
and rationales are present and `reviewer` identifies the declared reviewer.
Empty-hypothesis rows still require a reviewer to complete. Never edit context,
case IDs, record fingerprints, hypothesis IDs/order or citation IDs.

Score separately, keeping the original report unchanged:

```bash
.venv/bin/python evals/review_graph_model_results.py score \
  --source evals/results/graph_model_<run>_cases.jsonl \
  --reviews evals/results/graph_model_<run>_reviews.jsonl \
  --output evals/results/graph_model_<run>_semantic_scores.json
```

Top-1/Top-3 compare reviewed normalized hypotheses against the source case's
expected label on positive cases only. A positive case with no retained
hypothesis is a miss; negative cases use a separate workflow-abstention rate.
That rate includes pre-model/citation-rejection abstention, not just explicit
model abstention. Citation support scores use reviewed supporting citations,
with uncertain verdicts counted as not established. Empty answers do not earn
vacuous perfect faithfulness. All metrics include reviewed-subset denominators,
coverage and hashes; no reviewed examples means `null`, not 0% or 100% quality.
Failed source cases remain visible in overall coverage.

## Separate conflicting-citation review (v2)

For new worksheets, opt in explicitly during preparation:

```bash
.venv/bin/python evals/review_graph_model_results.py prepare --review-conflicts \
  --source evals/results/graph_model_<run>_cases.jsonl \
  --output evals/results/graph_model_<run>_conflict_reviews.jsonl
```

This creates `graph-semantic-review-v2` rows with a separate
`conflicting_citations` list per hypothesis. Review each listed citation against
the hypothesis's actual claim, not against the presumed incident label:

- `supported`: this observation genuinely undermines or qualifies that claim
  in the same service/time scope. For example, a healthy pool measurement in the
  relevant window can conflict with a claim of sustained pool exhaustion.
- `not_supported`: the observation does not provide the stated counterevidence
  (for example, a healthy measurement from yesterday or another service).
- `uncertain`: scope or detail is insufficient to determine whether it conflicts.

Provide a rationale for every verdict. Here `supported` means the citation's
**conflicting role** is supported; it does not endorse the hypothesis. The
scorer reports supporting and conflicting citation rates with separate counts,
never averaging them into a faithfulness metric. No listed/reviewed conflicts
means a `null` rate, not perfect conflict handling. Partial reviews still retain
the source-case coverage denominator.

Use the same scoring command as above; its rubric comes from the worksheet.
v1 stays supporting-only and is not automatically upgraded. v2 requires all
listed conflict IDs in original order, once each, and complete verdicts/rationales
before a case counts as reviewed. Missing/foreign conflict IDs and a citation
listed in both roles for one hypothesis fail validation. Rubric versions cannot
be pooled in one score or compared against each other. Prepare fresh v2
worksheets instead of changing the contract on existing reviews.

This extension does not measure omitted counterevidence, conflict resolution,
whole-response faithfulness or verified causality. Fixture tests are not actual
human calibration and do not establish a model quality result.

## Independent worksheet comparison

An offline comparison tool checks two independently completed worksheets against
the same source before comparing their overlapping cases:

```bash
.venv/bin/python evals/compare_semantic_reviews.py \
  --source evals/results/graph_model_<run>_cases.jsonl \
  --left evals/results/graph_model_<run>_reviewer_a.jsonl \
  --right evals/results/graph_model_<run>_reviewer_b.jsonl \
  --output evals/results/graph_model_<run>_review_agreement.json
```

Each worksheet must have one declared reviewer for its completed rows; the two
declarations must differ after Unicode/case/whitespace normalization. Original
source fingerprints, context, hypothesis order and citation IDs must pass the
same scoring validation. Pending or omitted cases are not paired, and failed
source cases remain in the coverage denominator. No overlapping completed items
means `null` agreement, not perfect scores. Paired abstentions contribute to case
coverage but not hypothesis/citation agreement.

The report separates normalized-cause label agreement from supporting-citation
verdict agreement and, for v2 worksheets, conflicting-citation verdict agreement,
with unweighted nominal Cohen's kappa, item counts and overlap
coverage. `uncertain` is a distinct verdict, never support. A constant shared
label can have observed agreement of 1 while kappa is undefined (`null` because
chance agreement is 1). Items within a case can be dependent: no confidence
interval or independent-sample inference is claimed. Agreement does not show
correctness, semantic faithfulness or causal efficacy; those fields remain null.

Exact disagreements retain case/hypothesis/evidence IDs, both verdicts and
citation rationales. This report does not automatically adjudicate, produce a
merged worksheet, or change either input. A human adjudicator should document
resolution in a separately retained final worksheet, preserving the originals.
Existing outputs cannot be overwritten.

Reviewer identity is declared, not authenticated. Comparing declared worksheets
does not establish actual expert agreement or independent human calibration.
Before publishing results,
use multiple blinded expert reviewers on an overlapping sample, document
disagreements and adjudication, and report coverage and rubric version with the
scores. The scorer accepts one annotation per case; consensus should be
explicitly adjudicated into a separate retained worksheet, never silently merged.

This protocol follows the recommendation to use detailed scorecards and multiple
human-review rounds in
[OpenAI evaluation best practices](https://developers.openai.com/api/docs/guides/evaluation-best-practices#human-evals).
