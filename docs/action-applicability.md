# Signal-to-action applicability

## Diagnosis-stage current signals — 2026-10-03

The compiled graph now uses the same bounded current sample projection before
classifying failure candidates, scoped to the selected primary service. Old,
future, untimed, conflicting and foreign-service samples remain in retained
evidence but cannot supply current rule candidates. The diagnostic trace records
`diagnostic_signal_time_checked`, evaluated time/bounds, eligible sample counts
and IDs, primary scope, and any explicit simulation-only compatibility.

When no current primary metric/log samples remain, the workflow ends as
`insufficient_evidence` with no hypotheses or production action, even when a
model generator is configured; it does not spend a model call inferring a
current cause from old observations alone. A current but unclassified signal
(including a single point insufficient for a sustained rule) may still be sent
to the configured generator for investigation, with no automatic action profile.
That generator still sees retained historical context; this gate is not a
general semantic, causal or whole-response freshness verifier.

The legacy `anomalous=true` summary path now additionally needs the explicit
local provider capability `allow_simulated_signal_summaries` and an entirely
summary-only primary signal set. Source names or provider attributes alone
cannot enable it, nor can that capability override structured samples. Remote
MCP and snapshot providers do not have it; Demo remains explicitly simulated.

The current [105-case report](../evals/results/workflow_benchmark_action_guard_20261003T020410Z_65c4b2b0.json)
and [150-case report](../evals/results/workflow_benchmark_20261003T020411Z_d334eb08.json)
have no contract failures or executions. These measure fixed offline protocol
and lab-rule behavior, not model accuracy or live-cluster acceptance. Earlier
sample-window reports below remain historical.

## Current retained sample window — 2026-10-03

Structured action signals now pass a fresh projection at proposal, pre-intent
and pre-dispatch boundaries. Metric points need explicit timezone-aware sample
timestamps, finite non-boolean values and a time in the last five minutes, not
merely a recent evidence retrieval timestamp. Points are sorted chronologically;
identical duplicates collapse, and all conflicting values at a timestamp are
excluded. Only the latest metric segment with gaps no greater than two minutes
is used by the existing sustained/growth profile rules. Future, missing/naive,
stale or otherwise unusable points cannot provide a destructive-action signal.
Log events use the same five-minute clock/window, rather than retrieval time.

The trace retains `signal_time_check` with evaluation time, bounds and eligible
sample counts, plus `temporal_eligible_signal_ids`; original evidence is not
rewritten. When no unique current profile remains, the outcome is advisory-only
before dry run. Approval delays can expire the retained samples; an old approval
or existing intent cannot bypass the repeated check. Refresh the diagnosis and
review the new proposal. Checks do not fetch live telemetry, certify clock
accuracy, align different metrics precisely, or verify action efficacy. Earlier
historical diagnosis/incident classification remains separately retained and is
not retroactively certified as current by this projection. New compiled-graph
classification now uses the separate diagnosis-stage check above.

The explicit summary-only offline provider compatibility remains simulation
only, without a freshness certification; structured observations cannot use that
exception. Default MCP synthetic samples are older and now stay advisory. Positive
mock-Kubernetes/MCP/graph and fresh-process PostgreSQL recovery tests explicitly
provide current synthetic samples through the real tool validation/transport;
they do not retimestamp production data or establish live-cluster acceptance.

The [105-case fixed-protocol report](../evals/results/workflow_benchmark_action_guard_20261003T014054Z_e2f302bf.json)
has zero contract failures: 90 negative cases blocked, five explicit mismatches
blocked and ten positive controls paused then rejected. Five new sample-time
templates over five pool snapshots add stale/future/untimed/duplicate/gapped
cases. These are 21 fixed templates, not 105 independent attacks. The fresh
[150-case snapshot report](../evals/results/workflow_benchmark_20261003T014056Z_f1f880d3.json)
also has zero failures or executions and preserves 75 approval pauses, 45
advisory outcomes and 30 abstentions. Neither calls a model or measures causal
correctness; older artifacts below are historical.

## Unresolved cause families are advisory-only — 2026-10-03

The action gate now requires `recognized_family_match` before any destructive
proposal dry run. Unrecognized wording, negated wording and multiple-family
text retain their hypotheses and investigation recommendations but cannot enter
restart/rollback approval. This applies before the explicit offline summary
compatibility branch too, and is rechecked before execution intent and dispatch.
Existing approval or intent cannot override a subsequently unresolved cause.
There is no operator switch that promotes unknown text to a semantic match.

This deliberately trades proposal coverage for conservative handling of
unsupported or ambiguous wording. A legitimate paraphrase outside the bounded
detector remains advisory; refresh diagnosis and gather evidence rather than
changing the wording merely to pass the guard. Recognized phrases are still
only a necessary consistency condition, not semantic entailment, causal proof
or evidence of remediation efficacy. General model/expert evaluation remains
uncompleted.

The new [80-case fixed-protocol run](../evals/results/workflow_benchmark_action_guard_20261003T010637Z_37edc8f0.json)
blocks 65 invalid/unresolved cases and five explicit contradictions before dry
run; ten positive controls still pause and are rejected. Five formerly allowed
unrecognized-wording probes now have zero destructive proposals. This expands
to 16 templates over five pool snapshots, not 80 independent attacks. The
[150-case clean snapshot run](../evals/results/workflow_benchmark_20261003T010643Z_13ef78e3.json)
has no contract failures or executions, preserving 75 approval pauses, 45
advisory outcomes and 30 abstentions. Neither report calls a model or measures
semantic faithfulness; all earlier artifacts below remain historical.
Current full isolated-PostgreSQL regression passes 717 tests without skips,
with one existing deprecation warning. Unit and real compiled-graph fixtures
cover all three unresolved statuses; intent/dispatch tests mutate each status
after a valid pause and assert only the original dry run was called.

## Operator-facing review — 2026-10-03

The approval panel now displays the retained signal/profile check, bounded
cause-family status and exact primary-service signal citations. Assessments
carry the leading hypothesis ID/text/citation set and service/profile binding;
the console checks those against the displayed incident. Missing legacy
metadata, stale bindings, ambiguous sequence numbers or invalid citations
produce an unknown/not-verified display rather than a green certification.
This projection is display-only: it neither grants execution authority nor
replaces the backend's pre-intent and pre-dispatch rechecks. Even a matching
phrase and signal check explicitly leave semantic entailment, release causation
and remediation efficacy unverified.

Actual React rendering and projection tests cover these cases. Local desktop
and 390×844 browser checks use mocked Kubernetes HTTP through real MCP and the
compiled graph, without approving or executing an action. Narrow command
previews wrap rather than stretching the approval grid. Screenshots are saved
under `docs/screenshots/action-review-*-20261003.jpg`. This is not live-cluster
or all-browser acceptance.

## Current known-family contradiction check — 2026-10-03

Before signal checking (and before the summary-only offline compatibility
branch), `cause_family.py` normalizes NFKC/format characters and checks bounded
English/Chinese known-family phrases in the leading cause. An unambiguous
recognized family different from the action profile is advisory-only, with
`cause_family_check.status=explicit_family_mismatch`. The existing pre-intent
and pre-dispatch checks rerun this logic; checkpoint text changes cannot bypass
it even when an old matching execution intent exists.

At the earlier revision below, there was no default family for unfamiliar text. Negation, multiple recognized
families and unrecognized wording get explicit unresolved statuses. They are
**not semantic matches** and did not become entailment/causal proof. They did not
by themselves reject a proposal that otherwise passed signal checks and still
requires named approval. Phrase coverage is bounded; complex paraphrases,
negation and causal chains need broader semantic review, which remains open.

The [new 70-case report](../evals/results/workflow_benchmark_action_guard_20261003T004238Z_38954dd1.json)
retains 50/50 negative citation/action rejections and 10/10 valid controls, and
now rejects all five explicit CPU-text/pool-signal mismatch probes before dry
run. Five new unrecognized-wording probes still reach approval. No semantic
faithfulness or model robustness score is inferred. Tests cover all eight
canonical names, English/Chinese/Unicode mismatch detection, unresolved
wording/negation/multiple families and cause changes at execution boundaries.
Full isolated-PG regression: 705 tests, no skips, one existing warning. The
[fresh 150-case clean run](../evals/results/workflow_benchmark_20261003T004325Z_9d433f2e.json)
has no lab-contract failures or executions; mean snapshot latency is 7.79ms.
The earlier mismatch report below is preserved as historical evidence.

Citation integrity and an owned rollback revision do not prove that an action
addresses the failure. Before a rollback/restart dry run, the workflow now
checks a bounded **signal consistency** contract:

- The tool must match the classified incident profile.
- Grounding must still pass for all hypotheses.
- There must be exactly one rank-one hypothesis and no unresolved conflicting
  evidence IDs on it. Confidence is not authorization.
- Only its cited primary-service metric/log evidence is used. Uncited signals,
  downstream observations, runbooks, previous incidents and event timestamps
  cannot fill a missing action signal.
- Existing bounded profile rules must reproduce exactly the selected profile.
  A different, missing or competing rule signal produces an advisory-only
  outcome, without dry run or approval interruption.

The trace retains `action_signal_applicability_checked` with cited signal IDs,
matched profiles, reason and explicit `semantic_entailment_verified=false` and
`causation_verified=false`. Hypotheses remain available for investigation.
Grounding/target checks and signed named-human approval remain required.
Applicability is rechecked before committing execution intent and again before
dispatch/receipt generation, including restored checkpoints. A matching old
intent does not bypass the check. Rejection raises a guardrail error and never
replays a mutation; retained unknown execution outcomes are not erased.

## Scope and limitations

This does **not** establish semantic entailment, release causation, a known
healthy prior revision or action efficacy. The rule may match a signal even
when the free-text cause is wrong. It cannot substitute for human review or
the still-pending expert faithfulness evaluation. Deployment events alone
cannot authorize a rollback. Profile mitigation choices remain lab heuristics.

Rules still use the existing classifier's bounded thresholds and memory-growth
criteria. The current action projection above now validates retained sample
time/order before applying those rules; it does not establish exact cross-signal
alignment or certify a current incident classification elsewhere. Delayed
approval can lose a current metric/log signature and fail the recheck: refresh
the diagnosis rather than treating old approval as timeless execution authority.

`DemoTelemetryTools` explicitly permits its legacy summary-only fixtures via
the code-level `allow_simulated_action_summaries` capability. The exception is
recorded as simulation-only compatibility, and applies only when no cited
signal contains normalized series/events. It cannot override structured healthy
signals, missing/ambiguous citations, conflicting hypotheses or wrong tools.
The remote MCP adapter and snapshot model benchmark do not have this capability;
provider-returned attributes cannot enable it.

## Verification

Unit and compiled-graph tests cover matching signals, generic/healthy/foreign
and uncited metrics, conflicting citations, missing rank one, wrong tools and
structured evidence despite offline compatibility. A confidence-1.0 generated
hypothesis with mismatched citations becomes advisory-only before dry run.
Changes after proposal and after execution intent are rejected before dispatch.
The model benchmark's local generic-citation fixture now correctly records an
advisory outcome, without claiming model root-cause accuracy or faithfulness.
No paid model or real Kubernetes action is needed for these tests.

## Measured adversarial fixture run — 2026-10-03

`evals/run_action_guard_benchmark.py` runs actual LangGraph over 13 fixed
hypothesis protocol templates and five pool snapshots. It includes high-confidence
healthy/unrelated metrics, deployment/event-only context, metrics-only/missing/
duplicate IDs, conflicting citations, foreign-service metrics and missing or
ambiguous rank one, plus valid and low-confidence positive controls.

The [65-case report](../evals/results/workflow_benchmark_action_guard_20261003T003940Z_bacddc93.json)
records 50/50 negative fixtures stopped before dry run, 10/10 positive controls
paused then rejected by the benchmark operator, and zero execution attempts.
The five free-text semantic mismatch probes were **not rejected by the action
gate**: they cite actual pool signals but say CPU saturation, and still propose
pool-profile rollback. They are observed gaps, not successful safety rejection.
Every paused proposal is rejected by the benchmark before execution.

The generator is a fixed local protocol fixture, not an LLM. This measures
workflow checks, not model prompt-injection resistance, semantic faithfulness
or real incident safety. Repeated templates are not independent attacks.
Semantic probes report current behavior without requiring the bad behavior to
persist in future tests. Per-case state/trace, hashes and source fingerprints
are retained in the report pair. Full regression: 685 passing Python tests with
isolated PostgreSQL, no skips and one existing deprecation warning.
