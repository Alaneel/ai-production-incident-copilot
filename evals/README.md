# Incident evaluation set

## Cross-service candidate benchmark — no model

The current
[32-case report](results/cross_service_benchmark_20261007T164402Z_be5fa187b20c48189ff819ca80373e32.json)
executes the compiled graph and BM25 over distinct synthetic dependency structures:
direct/two-hop observed/catalog paths, seven remote fault families, fan-out,
upstream distractors, ambiguous sources, cycles, unavailable telemetry, temporal
negatives and budget omissions. All 32 structural contracts pass; source hashes
are unchanged during execution. Top-1 covers 12 eligible cases, Top-3 covers 15,
and negative remote-candidate abstention covers 17. These denominators are
reported separately, not described as 32 independent production incidents or
100% real root-cause accuracy.

```bash
PYTHONPATH=services/orchestrator:services/mcp-server \
  .venv/bin/python evals/run_cross_service_benchmark.py
```

The scorer independently checks retained timestamps, units, sustained signals,
local/symptom citation contributions, window intersection and exact path basis.
It does not trust candidate green flags or invoke the implementation's pure
candidate assessor as ground truth. Reports and full-case JSONL are exclusive,
run-specific and source/fixture/corpus hash-bound. Model calls, mutation attempts
and new API cost are zero. Live RCA, model causal accuracy, semantic faithfulness
and action efficacy remain null. Separate ASGI MCP/MockTransport gateway tests
exercise the wire path; the synthetic benchmark itself does not establish live
MCP, PostgreSQL, Kubernetes, wall-clock timeout cancellation or causal evidence.

## Current single-fault workflow benchmark — no model

The [2026-10-08 source-matched 150-case run](results/workflow_benchmark_20261007T164404Z_f5bb6a04.json)
has 120 positive and 30 negative synthetic snapshots, zero contract failures,
75 guarded destructive proposals subsequently rejected, zero execution/model
calls and zero new API cost. It exercises the compiled synchronous workflow,
not the new HTTP diagnosis stream. API/stream/real PostgreSQL durability tests
are separate evidence. This corpus varies eight canonical single-fault patterns;
it is not 150 independently observed production incidents. Model root-cause
accuracy, semantic faithfulness and causal action efficacy remain null.
The 150-case runner records final source hashes; unlike the cross-service runner,
it does not itself attest to source stability before/after the run.

## Model-backed actual LangGraph snapshot benchmark

Preview the current 150-case workflow dataset without credentials or network:

```bash
.venv/bin/python evals/run_graph_model_benchmark.py --plan --limit 150
```

This is distinct from the historical prompt-only model comparison below.
The new runner injects the configured Responses hypothesis generator into the
actual compiled LangGraph with synthetic snapshot tools and real BM25 retrieval.
Samples are round-robin by fault family, including healthy and missing-data
negatives. Labels stay in the scorer; the model sees the workflow's rule-derived
classification and retained observations, not expected ground truth.

Only after separate human approval of the call budget:

```bash
.venv/bin/python evals/run_graph_model_benchmark.py --confirm-api-cost --limit 3
```

The default small smoke spans three different faults. Each case permits at most
one model generation, no transport retries, and no execution; destructive
proposals are rejected through the actual approval workflow. Maximum requests
are bounded by `--limit` (1–150), not a promise of actual billing. Like the single
smoke, execution safely loads local configuration without displaying keys.
The graph benchmark additionally sends an explicit `max_output_tokens` cap,
defaulting to 4096 per request. To change it, use the same
`--max-output-tokens <positive-integer>` in preview and execution. The manifest
retains the per-request cap and total maximum requested output tokens; a
three-case default smoke requests at most 12,288 output tokens. That includes
non-visible reasoning/formatting tokens according to the
[official token-counting documentation](https://developers.openai.com/api/docs/guides/token-counting).
It is not an input-token cap, exact input estimate or fixed USD budget. A
Responses-compatible provider must honor this parameter; rejection/truncation
stops the batch, without retry or treating it as successful abstention. Where an
incomplete response supplies usage, only nonnegative integer token counts are
retained. Missing/invalid usage stays unknown, and cost is still null without
verified prices. The production adapter's cap remains optional for existing
callers; the benchmark explicitly enables it. Current ordinary runtime defaults
are unchanged and must not be claimed as generally cost-capped.
Timestamp/UUID-named artifacts retain source/dataset/corpus hashes, complete
diagnosed and final states, checkpoints, measured token metadata and latency.
Each case is flushed to JSONL before continuing; failures are redacted and stop
the batch, with completed work preserved. A finalized summary can now be used
to continue only unattempted cases without retrying prior failures:

```bash
.venv/bin/python evals/run_graph_model_benchmark.py --plan --limit 150 \
  --continue-from evals/results/graph_model_<run>.json
```

The limit/selected cases, code/data/corpus hashes and prompt contract must match;
the output cap and its aggregate requested budget must match too, so continuation
cannot silently increase a previously planned generation budget. Continuation
previews include `remaining_maximum_requested_output_tokens` as well as the
remaining request count. These still do not establish a USD ceiling.
planning remains credential-free. The plan reports `remaining_maximum_api_requests`.
After separate human approval of that remaining budget, use `--confirm-api-cost`
with the same limit and `--continue-from`. Configured model/provider fingerprint
must also match. The runner never prints the raw endpoint or key.

Continuation creates new artifacts that carry prior records and source lineage;
it leaves originals unchanged and reports aggregate versus this-invocation
requests separately. Failed requests remain failed with unknown usage as needed.
A permanent exclusive `.continued` reservation allows one child per canonical
parent summary, preventing competing continuations. It is intentionally not
removed after a crash: without a new finalized summary, request outcomes may be
unknown. Do not remove it simply to retry or invoke the original parent again.
There is no automatic recovery of a process killed before its final summary;
that outcome needs manual reconciliation, not speculative duplicate spending.
Existing older revisions without matching identities cannot be continued.
Running without `--continue-from` is a separately authorized fresh run, not a
deduplicated continuation.

`ok` measures workflow completion/safety, not semantic correctness. Model
root-cause Top-1 and semantic faithfulness remain `null` until separate review;
rule classification must not be reported as model accuracy. Total tokens stay
unknown if any attempted request lacks valid usage. Cost remains `null` without
verified prices. Successful-case snapshot latency includes diagnosis, simulated
human rejection and checkpoint reads; it is not live production diagnosis time.
No paid batch result is claimed for this new runner; offline generator-injection
tests are harness verification only.

Use [the offline semantic review protocol](SEMANTIC_REVIEW.md) to prepare a
label-blinded worksheet and score separately retained reviewer annotations.
The scorer verifies record fingerprints, original hypothesis order and every
supporting citation. It reports reviewed-subset Top-1/Top-3, supporting-citation
support and explicit coverage; pending reviews do not become quality scores.
These are declared-reviewer judgments, not authenticated human consensus or
whole-response faithfulness. The original graph/model reports remain unchanged.
Opt-in `prepare --review-conflicts` creates a versioned v2 worksheet for separate
conflicting-citation verdicts, counts and support rates. Missing counterevidence
and conflict resolution are not measured. v1 stays supporting-only; rubric
versions cannot be silently mixed. Both verdict roles require complete rationales
and exact retained IDs. Empty conflict sets produce null scores, not perfect
conflict handling. The comparison tool reports v2 conflict agreement separately.
`compare_semantic_reviews.py` now compares two independently declared worksheets
offline, retaining overlap coverage, nominal cause/citation agreement, Cohen's
kappa and exact unresolved disagreements. It validates both sheets first,
rejects a shared declared reviewer, leaves undefined metrics null and never
merges annotations or awards correctness from agreement. The protocol includes
the invocation and limitations; actual expert review/calibration remains pending.

The current reasoning prompt is `evidence-contract-v3-abstention`: the required
`hypotheses` array may be empty, rather than forcing an invented cause. An empty
model response becomes `insufficient_evidence`, a separate
`model_hypothesis_abstained` trace event and a non-actionable evidence collection
recommendation; no approval/dry-run/execute is entered. Missing telemetry already
abstains before making a model request. Incomplete/refused/missing provider
outputs still raise generation errors; they are not converted into successful
abstentions. Offline HTTP-response tests prove parsing and graph behavior, not
that the configured real model reliably chooses to abstain. Historical paid
results predate this prompt revision and remain unchanged.

## Single model-backed diagnosis smoke

Run the current LangGraph diagnosis once with synthetic demo observations:

```bash
.venv/bin/python evals/run_diagnosis_smoke.py --confirm-api-cost
```

This explicitly authorizes one model request with no retry. The runner reads
`OPENAI_API_KEY`, `OPENAI_MODEL` and optional `OPENAI_BASE_URL` from the local
environment, overridden by `.env.local`, without displaying their values.
It saves the complete incident/trace to a timestamped `results/diagnosis_smoke_*.json`.
It uses an in-memory checkpointer, not live MCP or PostgreSQL. It does not
approve a remediation; any tool proposal can only reach a simulated dry-run.
`ok=true` means the workflow completed, not that the diagnosis passed grounding:
inspect `incident.status`, including `insufficient_evidence`, and the rejection
trace. A smoke is not an accuracy benchmark. Provider/configuration failures
exit nonzero and print only redacted error type/status metadata.

The runner now independently refuses all non-boolean-true `dry_run` requests,
counts dry runs/execution attempts, reads the final in-memory checkpoint back,
and checks that destructive proposals remain paused without approval. New
reports contain source hashes and explicit limitations; `ok` aggregates these
runtime safety checks, not diagnostic correctness. `semantic_groundedness`
remains `null` until a separate semantic review is performed. Offline tests
inject a local hypothesis generator into this same workflow without reading
credentials or calling a model. Historical reports are not retroactively
assigned checks or hashes that were not measured at run time.

## Dependency observation benchmark

Run `.venv/bin/python evals/run_dependency_benchmark.py` from the repository
root to exercise the actual LangGraph workflow on 18 synthetic dependency
cases: nine observation patterns on two service pairs. Patterns cover overlapping
and non-overlapping latency windows, empty data, stale/future samples, normal
samples, a single spike, transport failure and provider-raised timeout.
Expected impact statuses stay in the scorer and are not passed to tools.
The runner writes `results/dependency_benchmark.json` plus full incident/trace
records in `results/dependency_benchmark_cases.jsonl`, with fixture/code hashes.
Metrics cover observation status, window overlap, primary-classification
preservation and checkpoint roundtrip; no mutation executes. These are not
causal-direction or production root-cause accuracy measurements, and do not
exercise live MCP, PostgreSQL or model reasoning. Timeout is injected by the
provider, not a measured cancellation deadline. Results remain separate from
the existing 150-case single-fault workflow benchmark.

## Single-fault dataset

`generate_dataset.py` creates 150 deterministic cases across eight fault types:

- database connection pool exhaustion;
- downstream timeout;
- CPU saturation;
- memory leak;
- deployment regression;
- Kafka consumer lag;
- retry storm;
- cache penetration.

Every case includes the target service, root-cause label, required tools,
relevant and distracting evidence, remediation class, approval requirement, and
fault seed.

Run:

```bash
python evals/generate_dataset.py
python evals/run_benchmark.py
python evals/run_retrieval_benchmark.py
```

The checked-in results are generated by the executable harness, never written
in advance. The initial policies are deterministic harness-validation
baselines, not claims about LLM quality. Accordingly, token cost is reported as
`null`, and policy execution latency is kept separate from end-to-end diagnostic
latency. Model-backed runs will add prompt/model metadata and measured token
usage before any result is presented as an Agent benchmark.

`run_retrieval_benchmark.py` executes the real checked-in BM25 knowledge index
against all 150 cases. Its query contains the service, incident question, and
telemetry evidence labels, but not the ground-truth root-cause label. The result
records Recall@5, NDCG@5, latency, per-fault slices, and SHA-256 fingerprints of
both dataset and corpus.

## Credentialed model comparison

`run_model_benchmark.py` compares `single_prompt`, `rag`, and
`validated_agent` with the same structured prediction schema. It refuses to
make a request unless both `OPENAI_API_KEY` and `OPENAI_MODEL` are set and the
caller passes `--confirm-api-cost`.

The current reviewed target is `gpt-5.6-terra`, held constant across all three
variants so the benchmark measures workflow differences. See the dated,
source-linked [`model evaluation plan`](../docs/model-evaluation-plan.md) for
the selected standard prices and two-stage approval sequence. The model and
prices remain explicit configuration rather than code defaults.

Preview the exact call count and checkpoint state without credentials or API
calls:

```bash
python evals/run_model_benchmark.py --plan --limit 150
```

The plan reports selected variants, dataset/corpus hashes, benchmark revision,
completed checkpoints, and remaining calls. After a smoke run has measured
token usage, supplying both price arguments lets the plan estimate remaining
cost from those real samples; it does not invent a token estimate before then.

Start with a small smoke run:

```bash
python evals/run_model_benchmark.py \
  --confirm-api-cost \
  --limit 3 \
  --variants single_prompt
```

Run all 150 cases across all three variants only after reviewing the expected
450 API calls:

```bash
python evals/run_model_benchmark.py \
  --confirm-api-cost \
  --limit 150 \
  --input-usd-per-million '<current model input price>' \
  --output-usd-per-million '<current model output price>'
```

Pricing is never hard-coded because it is model- and date-dependent. Cost stays
`null` unless both explicit price arguments are supplied. The runner records
model ID, response IDs, token usage, latency, dataset/corpus hashes, aggregate
metrics, and per-case JSONL outputs. Each completed case is appended
immediately; rerunning the same model and benchmark revision resumes from that
file rather than paying for completed requests again.

### Checked-in credentialed result

The 2026-08-08 `gpt-5.6-terra` run completed all 450 planned calls:

| Variant | Root-cause Top-1 | Grounded | Mean latency | Cost / incident |
| --- | ---: | ---: | ---: | ---: |
| Single prompt | 19.3% | 0.0% | 2.633 s | $0.00318 |
| RAG | 64.0% | 99.3% | 2.407 s | $0.00389 |
| Validated Agent | 81.3% | 100% | 2.785 s | $0.00468 |

The total measured cost was $1.76228. The aggregate and all per-case outputs are
checked in under `evals/results/`. The first implementation included semaphore
queue wait in each case's timer; `normalize_queued_latency.py` preserved that
raw value and deterministically reconstructed request elapsed time from the
measured FIFO completion schedule. Future runs time requests after acquiring
the semaphore.

Aggregate output includes service accuracy, root-cause top-1/top-3, evidence
precision, retrieval Recall@5/NDCG@5, grounded-response rate, citation
faithfulness, tool-selection Jaccard, remediation and approval accuracy, unsafe
rejection rate, destructive-action guardrail rate, unguarded destructive action
count, mean/p95 diagnostic latency, tokens per incident, and cost per incident.

## Scoring definitions and offline rescoring

All runners now use the shared `retrieval_metrics.py`: binary NDCG discounts
rank `r` by `log2(r + 1)`, and Recall@5 uses only the first five positions.
Duplicate documents consume positions but earn relevance gain only once.
`metrics_version` identifies this scoring contract. The historical model
aggregate used an integer-based discount approximation; it remains untouched
as dated evidence rather than silently changing its numbers.

Recompute from the retained model outputs without credentials or API calls:

```bash
.venv/bin/python evals/rescore_model_benchmark.py
```

The separate `results/model_benchmark_rescored.json` records the source hash,
original model/revision, corrected scores, original request latency/token
measurements, and zero new calls. Historical prices are not silently replaced
with current prices. This rescored 450-output artifact has signal Recall@5
of 0%, 66.7%, and 100%, and NDCG@5 of 0%, 76.5%, and 100%, for single prompt,
RAG, and validated Agent respectively. This is rescoring, not a new model run.

Interpret the metrics narrowly: citation faithfulness currently checks that
cited IDs appear in retrieved evidence; grounded response additionally requires
at least one relevant labeled item. Neither proves semantic entailment or
causality. These model comparison variants use structured prompts and supplied
signal contexts, not end-to-end executions of the current LangGraph runtime.
The separate knowledge-retrieval benchmark measures actual BM25 document
ranking, whereas model-comparison retrieval metrics concern supplied signal-ID
lists. Deterministic harness-validation policies consume labeled cases and are
not deployable diagnosis agents or unbiased accuracy baselines.

## Actual workflow snapshot benchmark

```bash
.venv/bin/python evals/run_workflow_benchmark.py
```

This runner executes the current compiled LangGraph workflow and real BM25
knowledge index, including grounding, remediation dry-run, human rejection,
and checkpoint reads. Tool observations are synthetic recorded-value adapters;
there is no model client, network telemetry, or production execution. Ground
truth remains in the scorer, not the tool adapter or workflow inputs.

The 150 cases comprise 15 service/seed variants for each of eight canonical
fault patterns (120 positive cases), plus 15 healthy and 15 missing-telemetry
cases. They are not 150 independent failure modes.
The healthy split includes five baseline, five moderate-pressure, and five
single-spike cases so that all negatives are not trivial zero-valued signals.
These thresholds are specific to the fault lab, not general production SLOs.
The measured run correctly
classified all 120 positive cases, abstained on all 30 negatives, round-tripped
every checkpoint, guarded and rejected all 75 destructive proposals, and made
zero execution attempts. These are synthetic state-machine regression results,
not production RCA accuracy or LLM quality measurements.

`results/workflow_benchmark.json` records aggregate measurements, explicit
limitations, and dataset/fixture/code/corpus hashes. The matching
`results/workflow_benchmark_cases.jsonl` retains each final incident's full
evidence, hypotheses, trace, decision, status, and measured elapsed time for
inspection. Latency measures this local snapshot workflow, not a live diagnostic
request. The live fault-injection matrix remains a separate acceptance gate.
Subsequent Compose/Kind single-pool runs succeeded, but do not complete the
current-source eight-case matrix. An earlier Docker disk-full condition is not
evidence of a current blocker; see the dated runtime reports and completion audit.
