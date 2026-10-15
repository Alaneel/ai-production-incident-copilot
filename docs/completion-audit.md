# Completion audit

## Current source: cross-service diagnosis, protected console and durable streaming — 2026-10-08

The current workflow retains the complaint service separately from bounded
two-hop service/family candidates. Current local signals, complaint symptom
windows, directed path provenance and citations are checked; temporal overlap
is not causal proof. Remote candidates remain advisory-only. Missing bounded
investigations, failed/unavailable telemetry and unexplained downstream anomalies
block destructive proposals instead of being treated as healthy dependencies.
Primary classification and action gates use the same unit/range checks, and
excluded tool responses cannot supply a rollback revision. The console separately
validates candidate projections before rendering them.

Authenticated `POST /v1/incidents/stream` emits node-boundary progress. Each
snapshot is reread from the LangGraph saver and saved to the canonical repository
before its frame is sent. Sync stream updates are not trusted as persistence
acknowledgements on their own. Heartbeats do not assert progress; disconnect
cancels/closes the owned iteration without background replay. Tool/model/PG
failures emit redacted errors, not complete frames. Cache failure after successful
canonical persistence does not erase completion.

The stream exposed a proposal/interrupt race: a saved `waiting_approval` proposal
could precede the actual human-node interrupt. API decisions now require the
saved interrupt before consuming an approval/operation claim, and direct workflow
resume rechecks it. Early approve/reject returns 409 with zero claims/dispatch.
The console requires explicit stream completion, isolates stale responses and
keeps interrupted prefixes read-only; a state GET is not proof of a real pause.
Memory-only Reader/Operator token entry with exact backend trust is implemented
for development, not production SSO or verified human identity. Credentials are
not retained in incident URLs or browser storage.

The sanitized joint orchestrator/MCP/evaluation/Kubernetes-verifier regression
passes 1680 tests with zero skips and one existing Starlette warning. This includes
all 24 dedicated-PostgreSQL opt-ins. The four new stream-PG checks verify exact
per-frame saver/canonical state, fresh-connection restoration after partial close,
an actual closed-connection persistence error with no complete event, and early
proposal approvals with zero SQL approval/idempotency/operation claims. The 31
ordinary stream tests also exercise real ASGI disconnect during a blocked tool,
heartbeat/cancellation, redacted errors and retained-only snapshots. These are
not a new OS-kill, live Kubernetes or live-model acceptance claim.

The frontend separately passes 209 pure tests, typecheck and build; lint has no
errors and three existing generated-declaration warnings. The explicit
`npm run test:stream-contract` requires the backend virtualenv and never silently
skips it. Real Python API/compiled-graph/Pydantic SSE output passes the frontend
schema/parser and fragmented fetch-body decoder. It retains the exact final
checkpoint and keeps an EOF without complete unconfirmed, with one Reader POST
and no fallback/replay. This is in-process backend output with an injected fetch
body, not independent TCP or live-model evidence. The browser initially exposed
a legitimate empty broad-log `query` that hand-built fixtures missed; the parser
now accepts bounded empty query strings while still rejecting invalid types.

A separate actual HTTP browser run for `INC-4E2E91` shows eight provisional
events before explicit completion at 46. A wrong operator actor is refused;
the matching actor rejects the simulation proposal. Read-only API inspection
confirms `rejected`, 20 evidence objects, 50 events, exactly one retained reject
decision and zero execute-step events. The
[rejection screenshot](screenshots/diagnosis-stream-rejected-20261008-inc4e2e91.jpg)
is a cropped result view, not a whole-page or arbitrary-browser acceptance.
This API used required development auth, memory stores, deterministic direct
fixtures and a runtime-only metrics delay to expose progress; it did not use
MCP/Go/live observability, production secrets, a paid model or a real operation.
The test credentials/trust were cleared, the temporary tab closed, and both
owned localhost listeners stopped and confirmed absent. Partial-prefix restore
and no-401-demo-fallback were also observed in the preceding browser run; neither
read-only recovery nor receiving a proposal implies a completed diagnosis.

Fresh source-bound offline runs pass all
[32 cross-service contracts](../evals/results/cross_service_benchmark_20261007T164402Z_be5fa187b20c48189ff819ca80373e32.json)
and [150 single-fault contracts](../evals/results/workflow_benchmark_20261007T164404Z_f5bb6a04.json)
with zero model calls/production execution. The former verifies pre/post source
stability; the latter records final hashes. Neither runner tests HTTP streaming
or proves semantic faithfulness, real RCA accuracy or causal mitigation efficacy.

Four fresh images tagged `stream-live-20261008-4e2e91` build successfully. The
current [CPU run](../deploy/kubernetes/results/kind_live_cpu-spike_20261007T170542Z_19947180.json)
passes 35/35 in 146.054 seconds; the
[actual PG pool run](../deploy/kubernetes/results/kind_live_db-pool-exhaustion_20261007T171001Z_e8495cc4.json)
passes 42/42 in 184.689 seconds; the
[downstream-timeout run](../deploy/kubernetes/results/kind_live_downstream-timeout_20261007T171248Z_95da1219.json)
passes 35/35 in 151.687 seconds; and
[bounded memory-growth run](../deploy/kubernetes/results/kind_live_memory-leak_20261007T171518Z_d08173d2.json)
passes 35/35 in 131.925 seconds. Each owns a fresh private-config Kind cluster,
uses real live Go/MCP telemetry and required deterministic/PG/Redis API mode,
verifies server-side dry-run without template/generation/operation-marker
persistence, inspects the actual graph via a read-only PG connection, and restores
the exact checkpoint after replacing the API Pod. Pool additionally retains
controlled request/log/span parentage correlation and four actual PG leases.
All report zero model/production actions and successful owned cleanup. Runtime
includes cluster lifecycle, not model diagnostic latency; recovery clears the
injected fault, not an Agent-selected repair. CPU/memory fidelity remains bounded.

The independent [four-case aggregate](../deploy/kubernetes/results/kind_fault_matrix_stream_four_20261007T1715Z_4e2e91.json)
accepts their identical current 76-input source set/four immutable image IDs,
with `full_fault_matrix=false`. The other four scenarios are still running at
this snapshot; no complete-matrix, live cross-service, semantic quality,
namespace isolation or approved-action efficacy is inferred. Existing historical
reports and the preceding three-case partial aggregate remain unchanged.

Current source discovery has 76 inputs. The preceding
[42-check real-pool run](../deploy/kubernetes/results/kind_live_db-pool-exhaustion_20261007T154949Z_10b0addc.json)
had 75 inputs, 25 evidence objects and 44 workflow events; it verified actual PG
pool exhaustion/live telemetry/new-Pod restoration and owned cleanup, not an
Agent-chosen repair. The historical CPU/pool images below cannot certify current
cross-service/stream/approval code. Complete current-image all-eight acceptance,
live cross-service diagnosis, explicitly approved live action efficacy, production
identity/namespace telemetry isolation and model/expert quality remain unfinished.
The original scope is not complete.

## Real Kubernetes server preflight and approval checkpoint — 2026-10-07

Fresh images `preflight-v3-20261007-6c3174de` build successfully: orchestrator
`sha256:5882060221014c067ccc7d986316a8f49c7eb809b638ea37030b78a3bb80b135`
and MCP `sha256:1a063fd0f5197f8c3b42b46c675dff904e5922bfd2a9753d2337a8d7217c5abc`.
Existing source-matched lab/gateway images are reused by immutable ID. The renderer
explicitly enables real Kubernetes remediation and the isolated namespace; it
preserves the existing service account/RBAC without expanding privileges.

The runner/report contract is now `kind-live-fault-acceptance-v2`, with 35 common
checks (42 for pool-specific correlation). Every case directly exercises the
installed read-token MCP client with two same-key restart dry runs and bounded
forbidden-namespace/missing-Deployment negative probes, never an execute or
approval call. It records independent before/after UID/template/generation/marker
snapshots and separately recomputes the MCP Pod's API-origin/CA fingerprint.
Controller-only resourceVersion drift is allowed. Approval scenarios additionally
require real-mode, strictly scoped context, its matching successful trace, target
snapshots and the same interrupt mode/arguments. The offline v2 matrix validator
independently recomputes these bindings, rather than trusting green flags; it
rejects legacy v1 artifacts and coordinated context/trace/snapshot tampering.

The new CPU run
`deploy/kubernetes/results/kind_live_cpu-spike_20261007T151549Z_b4c73a48.json`
passes 35/35 in 155.981 seconds including cluster lifecycle. Fresh dry-run
timestamps differ by about 9 ms. Before/after risk-engine snapshots retain
Deployment UID `5d8f9a6d-fdd6-4826-bc5a-ef4c24ccb6d3`, resourceVersion 1013,
generation 1, identical template SHA-256 and empty operation markers. The generic
primary-service question retains 23 evidence objects and 42 events, classifies
the live lab CPU signal and pauses before restart approval. Independent
PostgreSQL-enforced read-only inspection confirms the exact checkpoint/pending
node/context-bound interrupt before and after a new Ready API Pod replaces the
old test API process. This replacement is an owned test operation, not an
approved Agent remediation. No paid model or API approval runs.

All 74 source fingerprints and recorded image bindings pass the strict offline
validator. The new aggregate
`deploy/kubernetes/results/kind_fault_matrix_preflight_cpu_20261007T151619Z_d3df17c2.json`
has `accepted=true`, one accepted case and `full_fault_matrix=false`; the other
seven current cases remain missing. Kind inventory confirms no clusters, full
Docker inventory contains no exact owned node, and temporary kubeconfig directory
inventory is empty. Other pre-existing containers are preserved, as are cached
images and immutable reports. The injected fault is explicitly cleared, not
mitigated by an Agent-selected action. Template/generation/marker snapshots do
not alone prove zero ordinary PATCH HTTP requests; no Kubernetes API audit
packet/verb attestation was gathered.

Kubernetes regression passes 280 tests, including 174 offline-matrix cases.
A sanitized joint run passes 1137 with 14 dedicated-PG skips and one deliberately
deselected owned Unix-socket Redis test. The final complete approved joint run
passes 1138 with the same 14 dedicated-PG skips, including the owned Redis test;
the existing TestClient deprecation warning remains.
Ruff/scoped diff checks pass. Frontend/lab/Go suites were not rerun in this update;
previous results keep their dated scope. Current all-eight acceptance, approved
execution, full release rollback, causal efficacy and model/expert quality remain
open. The CPU fault is a bounded busy loop/gauge, not actual host saturation;
the original project scope is not complete.

## Kubernetes preflight and target-bound approval v3 — 2026-10-07

Both orchestration and MCP now issue/verify v3 approvals, always signing a
`target_context_sha256` field (null only for explicitly simulated actions).
Versionless/v2 approvals fail closed. Backend-observed target identity records
the exact action/service/namespace, Deployment UID, observed resourceVersion,
full pod-template digest and configured API-origin/CA fingerprint. Rollback also
binds a unique owned ReplicaSet UID/revision/template digest. Configuration
fingerprinting is not an authoritative Kubernetes cluster UID, and never includes
bearer credentials.

Each live dry run queries its configured backend afresh, enforces namespace scope,
and submits the shared conditional patch with `dryRun=All`. Missing resources,
forbidden/admission-denied requests, incomplete/ambiguous history and malformed
target responses cannot downgrade to a successful simulation. The graph requires
explicit boolean acceptance/non-execution/dry-run, validates mode/backend/scope,
and retains normalized context in remediation arguments, successful preflight
trace, approval interrupt and durable execution intent. It checks the same
context again before signing, dispatch or operation inspection. Old proposals
without retained preflight binding need renewed diagnosis and approval.

Live execution/retries bypass process response-cache success and validate fresh
Deployment UID/configuration before durable-marker replay. New actions reject
changed pod templates or rollback target identity; controller-only resourceVersion
updates use a fresh conditional version. A conflict rechecks identity without
blind replay. Matching same-UID operation markers may be read-only replayed even
if old ReplicaSet history was garbage collected; that is reported past outcome,
not a new rollback. Status reconstructs the same context-bound fingerprint and
only reads Kubernetes. Marker matches still do not verify rollout health.

Adapter tests pass 67 cases; the complete MCP suite passes 263 with two optional
dedicated-PostgreSQL skips. The final joint orchestrator/MCP/evaluation/Kubernetes
verifier regression passes 1016 with 14 dedicated-PG skips and the existing
TestClient deprecation warning. Forty-nine focused graph/MCP/receipt tests pass:
actual MCP transport receives the context from preflight/checkpoint/interrupt,
v3 digest verifies across independently packaged wire models, mock UID replacement
blocks all ordinary PATCH calls, and fresh-registry status reads the original
marker without another mutation. Malformed/legacy/tampered context and digest
fail at the intended boundary. A sandbox run excluding the owned Redis process
test passed 1006/14 skipped/1 deselected before nine additional signer tests;
the complete approved run includes the scoped temporary Unix-socket Redis test.
No real Kubernetes patch, model request or paid API call occurred. In-memory
graph restoration in these tests is not new process/PostgreSQL durability proof.

Approval command previews now include namespace; rollback says container-image
restoration, not full `kubectl rollout undo`. Full release rollback and measured
causal mitigation remain open. The console independently checks retained mode,
scope, normalized identity and the latest successful preflight record before
showing simulation/server-dry-run metadata. Unknown/legacy/malformed/inconsistent
records disable Approve in both its button and handler; Reject remains available.
It shows exact namespace/Deployment UID/resourceVersion, configuration/template
digests and rollback RS/revision, stating that metadata does not authorize a
change or prove recovery. Eleven new frontend tests cover 34 malicious shapes,
latest failed/duplicate records, final-newline regex ambiguity, escaping and actual
React/console HTML rendering. Full `npm test` (typecheck/build/Node) passes 97
tests without skips. Lint exits zero with three existing generated
`worker/runtime.d.ts` unused-disable warnings; build retains a non-failing vinext
route-classification notice. No fresh browser visual or live UI/backend test ran.
Lab regression separately passes 161 with three optional dedicated-PG skips and
the existing TestClient warning. Go tests were not rerun for this update.

Source discovery now includes 74 inputs, including
the new MCP context module. The offline matrix verifier rejects all three old
CPU/memory/lag reports in
`deploy/kubernetes/results/kind_fault_matrix_stale_targets_20261007T150321Z_408f6db1.json`
with zero accepted current cases. No images were rebuilt or live acceptance rerun;
historical artifacts are preserved. Current complete eight-case acceptance,
approved live execution, model/expert quality and the original scope remain open.
Any paid model smoke still requires separate human authorization and a fresh plan.

## Namespace-bound approval v2 and fault-matrix consistency — 2026-10-07

Read-only safety review found two concrete preflight/approval gaps. MCP dry run
accepted a missing Deployment in a forbidden namespace without contacting its
configured Kubernetes backend. Separately, a valid rollback receipt was reusable
for the same service/revision in another namespace when given a fresh operation
key. Backend allow-lists/RBAC limit that risk in today's single-namespace lab;
the probe used MockTransport/recording fake backends, never a real mutation.

The receipt gap is now fixed on both sides. Orchestrator `ApprovalReceiptSigner`
and MCP `ApprovalReceiptVerifier` require namespace explicitly and sign v2
payloads with a separate namespace field. The compiled graph passes its exact
approved remediation namespace. MCP authenticates and validates the signed
object/version, required nonempty identity/scope fields and integer expiration,
then compares namespace with the execution request before cache replay or
backend dispatch. Legacy versionless/namespace-less receipts fail closed; new
idempotency keys do not broaden approval. Tests retain actor/incident binding,
authorized replay and valid-receipt namespace-change key-conflict coverage.
Signer/verifier must be deployed together; no legacy/default-namespace fallback
or automatic upgrade of external old receipts is provided.

Thirteen new orchestrator signer/compiled-graph tests failed against the old
contract and pass after the fix. Full MCP regression passes 154 tests with two
dedicated-PG skips. The joint orchestrator/MCP/evaluation/Kubernetes-verifier
run passes 875 tests with 14 dedicated-PostgreSQL tests skipped; 25 focused actual
graph/MCP transport tests also pass. One existing TestClient deprecation warning
remains. The owned Unix-socket Redis test initially exits before readiness in
the sandbox; the same test passes under approved execution. A full-suite approval
request temporarily failed because the automatic reviewer was at capacity and
did not execute; a later approved retry gives the joint passing result above.
No bypass or unrelated service configuration was used. Ruff/diff checks pass.
Go, fault-lab and frontend full suites were not rerun in this update; skipped PG
tests are not silently counted as passing or new persistence acceptance.

The new offline `deploy/kubernetes/verify_fault_matrix.py` takes only explicit
report paths and opens optional output exclusively. It verifies the exact
current 73-source input set, common/pool-specific check contracts, same image
IDs, primary-service metric/log/trace payloads, cited evidence identity,
approval/advisory boundaries, exact read-only checkpoint/interrupt recovery,
distinct replacement Pod UID and controlled pool-request correlation. Only
eight unique accepted canonical cases can produce full-matrix status; valid
subsets remain partial. It preserves input-byte SHA-256/path references and
individual manifest/source/corpus/image bindings. Duplicate JSON keys,
non-finite data, boolean-as-zero counters, missing/false/extra checks, drifted
checkpoint/sources/images, legacy contracts and duplicate/unknown cases fail.
Seventy-eight adversarial tests pass. It is retained-evidence consistency, not
independent runtime/build/report-honesty attestation or semantic/model scoring.

Before the security source change, the independent consumer-lag report
`deploy/kubernetes/results/kind_live_kafka-consumer-lag_20261007T143715Z_9e605f43.json`
passed 28/28 in 137.032 seconds. It retained 23 evidence objects and 42 workflow
events, primary ledger-service classification and an exact restart approval
interrupt after API replacement. The lag signal is a synthetic counter, not a
real Kafka consumer/broker failure. No model/approval/action ran. Kind and exact
Docker-node inventory independently confirmed cleanup. Test databases/private
config were removed; images and immutable reports remain.

The namespace fix changes core source fingerprints. No application images were
rebuilt or live acceptance rerun afterward. Actual offline aggregation of the
CPU, memory and consumer-lag reports now rejects all three as stale in
`deploy/kubernetes/results/kind_fault_matrix_stale_receipts_20261007T144300Z_f6ca9627.json`,
with `accepted=false`, `full_fault_matrix=false`, and zero accepted current cases.
Historical passing artifacts are unchanged, not retroactively assigned new
security behavior. Current eight-case acceptance remains incomplete.

Remaining safety work includes real backend preflight (the current dry run is
still policy/parameter-only), binding the proposed Deployment UID/cluster
context, rejecting stale/replaced targets, explicitly human-approved actual
execution and causal mitigation checks. Current model/expert quality and the
full original scope remain open; no paid model calls were made or authorized by
an automatic goal continuation.
Saved model-smoke plans also predate the graph source change and need a fresh
credential-free fingerprint preview before any separately authorized paid run.

## Generalized Kubernetes CPU/memory faults and graph checkpoints — 2026-10-07

The live runner now accepts all eight canonical scenarios, always in a fresh
owned cluster with new observation stores, generated credentials and empty
model configuration. Non-pool traffic reaches risk-engine through ledger;
the API gets a generic primary-service question, never the injected mode or
expected cause. Explicit expected families and advisory/approval boundaries
are verifier-only inputs. Reports record per-scenario fidelity and remain
single-case (`full_fault_matrix=false`). Synthetic Kafka/cache/release signals
must not be described as actual brokers/caches/rollout regressions.

Persisted approval validation now goes beyond visible incident state. A separate
process compiles the actual graph with no usable tool provider and a PostgreSQL
connection enforcing read-only transactions; no setup/invoke/resume occurs.
It requires exact state plus either a completed graph without interrupts or one
pending human-approval node with a nonempty interrupt ID bound to incident,
remediation, tool, arguments, dry-run and hypothesis evidence. After replacement
by a new Ready API Pod, the complete read-only snapshot including checkpoint ID
and interrupt must be unchanged. No approval endpoint is called.

`deploy/kubernetes/results/kind_live_cpu-spike_20261007T142832Z_a2e80b10.json`
passes 28/28 in 162.187 seconds including cluster lifecycle. Risk-engine is the
primary service; live metric/log/trace payloads are nonempty, classification is
CPU saturation, and its successful-dry-run restart proposal waits for approval.
That dry run is MCP policy/parameter validation, not Kubernetes server-side dry
run or rollout-health proof; the mutation adapter is never invoked by it.
The run retains 23 evidence objects and 42 workflow events. Actual PostgreSQL
checkpoint and interrupt identity survive API replacement. Source checks inside
Python images pass; all 73 recorded source/config/corpus fingerprints are
independently verified with zero mismatches. A fresh orchestrator image includes
the current output-budget adapter (`sha256:008aee4c70f9aac1b6110b1a2d68750fcf7cc50bcf731bd13bbb0a881eb08c31`),
but no model call exercises that adapter. Kind and matching Docker-node inventory
confirm no experiment resources remain; private config/test databases were
removed, while images and immutable reports remain.

The second independently owned run
`deploy/kubernetes/results/kind_live_memory-leak_20261007T143215Z_bc3f39b5.json`
passes 28/28 in 152.969 seconds including lifecycle. Five current payment-service
retained-allocation gauge samples grow 48, 58, 68, 77, 87 MiB over 20 seconds.
The generic question is classified as memory growth, with 25 evidence objects
and 44 workflow events. Synthetic deployment provenance prevents an executable
rollback; remediation stays advisory-only. Read-only checkpoint inspection
finds no pending node/interrupt; the exact completed snapshot and incident
survive replacement with a new Ready API Pod. All 73 hashes match this worktree
and the CPU run; Kind inventory, exact-node Docker inventory and absence of
both private config files independently confirm cleanup. No model, approval or
action runs. This bounded allocation gauge is not process RSS or an actual
unbounded production memory leak. The two reports prove two cases, not the
complete eight-fault current-revision matrix.

Eighty Kubernetes tests and 54 selected workflow/action/signal tests pass, as do
Ruff/whitespace checks. The full backend/frontend suites were not rerun. This
proves bounded application CPU/memory cases and real graph approval/completed
checkpoint retention, not measured host CPU saturation/RSS, controlled log/span
identity for these cases, all-eight runtime coverage, approved resume/execution, arbitrary
in-flight crashes, semantic/model quality or action efficacy. Original scope
remains open; paid smoke/full model runs still require human authorization.

## Graph-model output budget and offline smoke readiness — 2026-10-07

The graph-model benchmark now explicitly sends a configurable Responses
`max_output_tokens` cap, default 4096 per request. Offline `--plan` includes the
per-request cap and aggregate maximum requested output tokens; continuation
requires both to match and previews its remaining aggregate. Invalid zero,
negative, boolean/string/non-integer caps fail before client/credential access.
The adapter's argument is optional, preserving existing ordinary runtime
behavior; this is not a new general production dollar/input-token guard.

The separately saved credential-free plan
`evals/plans/graph_model_smoke_20261007141516_4c6814bd.json` selects exactly
eval-001/002/003 (pool, downstream timeout, CPU), at most three requests with no
retry, and at most 12,288 requested output tokens. It is marked offline and
execution-not-authorized. Model choice and USD estimate are intentionally
unresolved, not read from `.env.local` or invented. Output limits include hidden
reasoning/formatting tokens, not input tokens; compatible providers must honor
the request contract. The official OpenAI token-counting page was retrieved
before implementing this API change; it is linked in the evaluation README.

Incomplete Responses remain failed generations, never successful abstentions.
When they include usage, the benchmark retains only nonnegative integer
input/output/total token counts, never provider content or arbitrary fields.
Missing/invalid usage stays unknown; failed requests can still be billable and
actual_cost_usd stays null without verified prices. Mock-transport tests run
through the actual compiled graph and prove one attempted generation, stop on
failure, retained measured usage, no semantic score and no provider-content
leak. Continuation-cap drift and CLI preview are tested too.

All 201 evaluation/reasoning tests pass; Ruff/whitespace checks pass. Plan source
fingerprints match the current worktree. No model, token-count API, Kubernetes
cluster or remediation was called in this update. This is harness readiness,
not model quality, expert review or live acceptance of the modified model
adapter. Paid smoke authorization was requested separately from the human;
full 150-case/model comparison, verified USD costs and expert scoring remain open.

## Strict real Kubernetes correlation acceptance — 2026-10-07

The final combined-fix run
`deploy/kubernetes/results/kind_live_pool_20261007T140934Z_c55eefd8.json` passes
28/28 checks in 169.462 seconds including cluster lifecycle. The single controlled
failed checkout request is present in MCP trace search and has an observed
checkout CLIENT → payment SERVER parent relationship. The exact server span ID
matches its completion log, while the pool-error log shares trace/request IDs
and reports 4/4 saturation. Matching data arrives on the tenth bounded probe
attempt. The actual diagnosis retains 25 evidence objects, 44 workflow events
and 20 distributed trace summaries. Its classification/advisory boundary,
real-session exhaustion/recovery and exact new-Pod PostgreSQL state readback
all pass. The source/config/runner fingerprints were independently rechecked
with zero mismatches. Kind inventory shows no clusters and Docker inventory
shows no copilot-live node containers; private configuration/test data were
removed. Cached images and immutable earlier failed reports remain.

Go tests pass with a task-scoped build cache; 50 Kubernetes tests pass; the full
lab module passes 161 tests with three optional dedicated-PG tests skipped and
one existing TestClient deprecation warning. The startup test also passes inside
the newly packaged lab image. Ruff and whitespace checks pass. The whole
backend/frontend regression was not rerun. No paid model, API approval or
suggested remediation was executed (API rollout replacement is a test operation
inside the owned cluster). This proves sampled log/span identity and completed
state recovery for one real pool fault, not all-eight coverage, semantic/root
cause correctness, omission-free evidence, action efficacy, arbitrary
in-flight/approval recovery, deployment chronology, NetworkPolicy enforcement
or production/model quality. Original scope remains open.

## Kubernetes trace-evidence correction and stricter verifier — 2026-10-07

Reinspection of `kind_live_pool_20261007T134228Z_79004dbd.json` found that the
retained trace evidence object's `attributes.traces` is empty and its observed
topology contains zero calls. The v1 check accepted an evidence object's type,
not its payload. Its 23 true checks therefore prove neither delivered Tempo
spans nor log/span identity correlation. Earlier wording below that calls this
live metric/log/trace evidence must be read with this correction. The original
report is preserved, not assigned new checks retroactively.

The v2 runner requires nonempty series/events/traces and a controlled failed
checkout request. A unique sampled W3C trace context and request ID traverse
checkout to the exhausted payment pool. The installed typed MCP client queries
the actual Go gateway for observed client/server parentage, request-scoped logs
and trace summaries. Checks require exact server-completion span identity,
trace/request identity on the saturated 4/4 pool error, and the controlled trace
in search. The inner error span is not asserted equal to the server span. Only
owned background traffic is paused to prevent the fixed target being evicted
from bounded trace samples, then resumed before diagnosis. This is sampled
correlation, not semantic RCA or causal mitigation validation.

The first v2 runtime attempt
`kind_live_pool_20261007T135215Z_e6df1bcd.json` failed at traffic shutdown:
its 30-second Pod-deletion wait collided with Kubernetes's default 30-second
termination grace. Eleven preceding resource checks passed; no controlled
correlation or diagnosis result was produced. The created cluster/private file
were removed. The wait is now bounded at 45 seconds (55-second outer command
limit); the correlation requirements are unchanged. Failed artifacts remain
unchanged. This turn's final stricter runtime result is recorded separately.

The second stricter report `kind_live_pool_20261007T135550Z_0072ea04.json`
completes the run and cleans up, but fails four of 28 checks: controlled
parentage, exact server log identity, controlled trace search and nonempty
diagnosis telemetry. The request-scoped pool error and state restoration pass.
Read-only direct Tempo searches return zero traces for payment-service but
unfiltered search returns spans named `unknown_service:python3.13`. Collector
logs prove receipt; this distinguishes service-identity failure from no export.

Inspection of the actual packaged FastAPI lifecycle finds native telemetry
initializing before the lab's user lifespan. The package is FastAPI 0.142.2 with
OpenTelemetry 1.45.1. Its automatic OTLP setup creates the global provider first;
the lab's later service-specific provider cannot replace that single-set global.
Native server/operation spans also interfere with the intended single manual
server span. This matches the framework's
[documented automatic telemetry behavior](https://fastapi.tiangolo.com/advanced/opentelemetry/).
The lab now disables native automatic configuration, tracing/operation spans,
metrics and logs, retaining its own explicit provider, Prometheus metrics and
bounded Loki log delivery. No orchestrator/MCP security or action gate changes.
A fresh-process startup regression checks resource service identity, one server
span, remote parentage/request ID and exclusion of health/metric probes. It
passes in the new packaged image as well as locally. The old packaged image
fails the regression via the bounded subprocess timeout; that particular test
does not independently score its trace contents. The new image is
`sha256:0a75b5c31f64759cc8d8a0283fed6d9f59be937356c47def0bb2a1e3e920aebf`.

Using that lab image with the old gateway completes another isolated run:
`kind_live_pool_20261007T140329Z_2eb11484.json`. Nonempty diagnostic trace evidence
now passes (20 traces), as do retained observed call identities, diagnosis and
exact state recovery. The fixed failed request is still missing from the
bounded search; three controlled-correlation checks fail, so overall `ok=false`.
Logs prove its precise trace/request identity and 4/4 pool error. The returned
traces/calls instead describe earlier pre-fault requests. The owned cluster and
private file are removed. This report remains unchanged.

Tempo's default search returns the first matching N, not necessarily newest N;
sorting an already truncated result locally cannot recover the missing target.
The Go query now uses the
[official `most_recent=true` hint](https://grafana.com/docs/tempo/latest/traceql/construct-traceql-queries/#retrieve-most-recent-results-experimental),
keeping the same service/time/limit bounds. This is an experimental backend
feature and can increase search work; timeouts remain unchanged, and failures
must not silently fall back to arbitrary old traces. Regression assertions cover
the exact query and both the ordinary 10-trace and observed-parentage 8-trace
limits. The new gateway image is
`sha256:3a11ec69b8389216ecbf52b1fe0ddf5a245bc5c757ba20d4c4173913dc9daddb`.
New verifier reports include Go source/module/Dockerfile fingerprints as well as
the explicit image ID. Source hashes alone do not independently prove the
compiled image contents; the image was built from this worktree in this turn.

## Real Kubernetes pool/telemetry/state-recovery acceptance — 2026-10-07

The opt-in `verify_live_fault_lab.py` was executed in three fresh privately
configured Kind clusters. The first two retained complete diagnoses and passed
resource/telemetry checks but failed their recovery probe with localhost
connection refusal. The second report retained redacted failure diagnostics.
Deployment-level exec during replacement can target an exiting old API Pod;
the verifier now requires exactly one Ready, non-terminating replacement Pod
with a UID absent from the pre-restart set, and probes that explicit new Pod.
Ambiguous/unready/old Pod choices fail closed. Earlier reports were not rewritten.

The final report `deploy/kubernetes/results/kind_live_pool_20261007T134228Z_79004dbd.json`
passes all 23 checks in 131.334 seconds including cluster lifecycle. Sidecar SQL
independently confirms four pool session PIDs; running orchestrator, MCP and lab
Python files match the worktree. Four-service live traffic enters Prometheus,
Loki and Tempo/collector, then actual Go/MCP and authenticated API diagnosis.
Four leases exhaust with real 503 and clear to healthy checkout traffic with
the same session IDs. Pool classification, hypotheses, metric/log/trace evidence,
advisory boundary, no execution and exact readback pass. API rolling replacement
changes UID and reads back exact committed incident state. The retained report
contains 25 evidence objects and 44 trace events, not a recreated summary.

Every recorded config/renderer/runner hash matches the final tree. The final
new cluster and private kubeconfig were removed; independent Kind/Docker
inventory confirms no experiment clusters/node containers. Test databases are
gone; cached images/unique test tags and all three reports remain. Existing user
resources/context were not reused. No paid model or remediation call occurred.
All 34 Kubernetes tests, Ruff and whitespace checks pass; full backend regression
was not rerun. This is real single-family Kubernetes deterministic diagnosis and
completed-state recovery, not model accuracy, log/span identity correlation,
all-eight matrix, independent checkpoint-history audit, arbitrary in-flight
crash/approval recovery, live release provenance, network-policy enforcement,
UI/Grafana acceptance or causal mitigation efficacy. Original scope remains open.

## Live Kubernetes fault-lab preparation — 2026-10-07

`deploy/kubernetes/render_fault_lab.py` now renders the actual backend base plus
four lab services, bounded checkout traffic, real-pool payment/PG sidecar,
Prometheus/Loki/Tempo and collector resources into one Kubernetes List. It leaves
the caller's configuration unchanged and never applies/creates secrets. Backend
images use an explicit fresh tag; model key/model are explicitly empty. The Go
gateway targets namespace-local live stores, not fixtures. Existing lab
telemetry configurations are embedded as ConfigMaps. Tempo exposes OTLP HTTP
4318 as well as query HTTP 3200; MCP permits the current client's explicit origin.
The separate pool database retains Unix-socket isolation, non-root application,
read-only socket mount and no TCP listener. No public endpoint is generated.

All 21 Kubernetes tests pass, including six new renderer checks/invalid-tag
cases. They validate the actual Kustomize base, retained source object integrity,
four-service dependency/traffic configuration, live backend URLs, trace delivery
port, disabled model credentials and absence of generated secrets/NodePorts.
Ruff and whitespace checks pass. No live cluster was started in this preparation
turn, and these tests do not prove actual ingestion, current fault classification,
source/image runtime identity, recovery or network-policy enforcement.

The explicit runtime requirements are in `deploy/kubernetes/LIVE_FAULT_LAB.md`.
The legacy fixture verifier is not weakened to accept stale signals or invent
an approval pause. Real Kubernetes telemetry/diagnosis/recovery acceptance,
current deployment provenance, human-approved execution, all-eight fault matrix,
model/expert scores and causal efficacy remain required in the original scope.

## First real Kind rollout investigations — 2026-10-07

Two new, privately configured Kind clusters completed control-plane startup and
all five backend rollouts. API-auth/RBAC checks progressed, but the overall
legacy fixture verifier failed. A scoped investigation disproved the initial
assumption that incident creation failed: its access log records creation 201
and approval 409; retained state is insufficient evidence with no execution.
See `docs/kind-runtime-investigation-20261007.md` for exact observations and
limits. The fixture generates stale metric samples; no successful full trace,
Kubernetes fault diagnosis or approval/resume result is claimed/reconstructed.

Both uniquely owned clusters were deleted and Kind inventory confirmed none;
their disposable databases and private kubeconfig files were removed. New and
existing cached images are retained. No existing cluster/context was reused.
Application image tags now bind to the explicit cluster name; existing result
files are refused before allocation and exclusive report writes prevent clobber.
Fifteen Kubernetes tests pass, including unchanged caller config and old output
preservation. Bash syntax, Ruff and whitespace checks pass. The full original
scope remains open; rollout success must not be promoted to complete cluster
acceptance, telemetry coverage, model accuracy or causal efficacy.

## Kind verifier context isolation — 2026-10-07

Read-only checks find installed Kind/kubectl but no local Kind clusters or cached
Kind node image. The existing rollout verifier used unqualified kubectl calls
and relied on creation changing the caller's current context. Before invoking
any real cluster mutation, that unsafe targeting dependency was removed.

`verify_kind.sh` now allocates a fresh private kubeconfig path after inventory
and cluster-name checks, passes it explicitly to Kind creation, and routes every
kubectl call through a wrapper bound to both that file and the explicit new
Kind context. Caller configuration/current-context is never the mutation target.
Deletion uses the private configuration too. The private file is removed only
after successful deletion and a successful inventory proving cluster absence;
kept/partial clusters, deletion failures and inventory failures retain it for
manual inspection. Names must be lowercase DNS labels with at most 63 characters.

All 14 Kubernetes manifest/verifier tests pass, including a sentinel caller
kubeconfig unchanged across creation/build/apply/deletion failure paths,
explicit API request targeting and retained credentials when deletion/absence
verification fails. Bash syntax, Ruff and whitespace checks pass. Tests stub
external mutation commands; no real cluster was created, applied to or deleted.
This removes a prerequisite isolation risk, not Kubernetes runtime acceptance.
The full platform, current real-cluster remediation/RBAC behavior, fault matrix,
model/expert scores and causal efficacy remain unverified.

## Merged Compose pool diagnosis acceptance — 2026-10-07

Fresh current-source orchestrator, MCP and Go images built successfully with
unique acceptance tags. The new opt-in `verify_compose_platform.py` merges all
three existing platform/lab/real-pool files with a no-host-ports acceptance
overlay. Image references resolve to local IDs; stores must already be cached.
It uses a fresh UUID project, `/dev/null` env-file, generated test credentials,
required API authentication and explicit empty model credentials. Existing
projects are not reused and no image is implicitly built or pulled at launch.

The first real run retained a failed report because the verifier checked the
wrong remediation field (`action` instead of `tool`). Its diagnostic result was
actually advisory-only. The verifier was corrected against the actual model,
with tests for malformed/approved/destructive alternatives. A new full run in a
different project passes all 21 checks:
`fault-lab/results/compose_platform_20261007T131716Z_c3b86ddc.json`.
Four-service traffic enters Prometheus/Loki/Tempo and Go/MCP, and authenticated
API diagnosis retains metric/log/trace evidence, pool classification and
hypotheses. PostgreSQL checkpoints, Redis delivery and deterministic reasoning
are checked via runtime modes; no paid model call occurs. The synthetic release
catalog yields advisory investigation, not rollback, approval or execution.
Actual pool sessions exhaust and recover with the same backend PIDs. After an
API container restart, retained incident state matches exactly.

Only this generated Compose project's containers/network/volumes were removed;
successful label-filtered inventory checks confirm absence. Its disposable test
database contents are gone, while diagnosis/trace reports and newly built images
are retained. The earlier failed report is immutable. Five new verifier tests
(ten with prior packaged-runner tests) pass; Ruff and whitespace checks pass.
The successful report's config/runner hashes match the current tree.

This is real merged-Compose single-family diagnosis/restart acceptance, not an
all-eight matrix, UI/Grafana rendering acceptance, arbitrary interruption crash
recovery, live Kubernetes, model quality or causal remediation effectiveness.
Full-suite regression and these remaining original requirements remain open.

## Packaged real-pool service acceptance — 2026-10-07

Read-only Docker inspection succeeded; a fresh uniquely tagged fault-lab image
build completed without the prior disk failure. Image ID is
`sha256:2399890479d0215b97cb88dc41896905045406c1e78b3a197e4abd7e28a6ddef`.
The new opt-in `fault-lab/verify_packaged_pool.py` was executed twice, with fresh
owned resources each time. The final report
`fault-lab/results/packaged_pool_runtime_20261007T131143Z_e93e8c82.json`
passes 22 checks, including matching image/worktree application-source hashes,
non-root/read-only operation, read-only shared Unix socket, no published ports,
tmpfs PostgreSQL data and no database TCP listener. Actual four PG sessions are
preserved during exhaustion/recovery; lease counts 0/4 → 4/4 → 0/4 and HTTP
200 → 503 → 200 are checked. Unauthorized injection is rejected and graceful
service shutdown closes every application-pool connection.

Only the two newly allocated containers, internal network and socket volume were
removed; successful Docker inventory listings confirmed exact-name absence.
Existing user resources and the newly built image are retained. The script uses
generated experiment credentials, not `.env.local`; no model or production
action occurs. Five offline tests verify preflight-before-allocation, exclusively
owned failure cleanup, failure/occupied-name absence handling and redacted errors.
Ruff and whitespace checks pass. The earlier 18-check report remains immutable.

This proves isolated packaged payment/PG resource-fault behavior, not the merged
Compose platform, full telemetry/MCP/graph recovery chain, real-cluster operation,
model quality or causal remediation efficacy. Docker packaging is no longer
wholly blocked by the historical build failure; full-platform and Kubernetes
runtime acceptance remain required. The complete backend suite was not rerun.

## Offline conflict-review extension — 2026-10-07

Semantic review preparation now supports explicit `--review-conflicts`, emitting
`graph-semantic-review-v2` worksheets. v1 remains supporting-only. Listed
conflicting citations receive their own verdict/rationale, count, uncertainty
count and support rate; `supported` endorses the citation's counterevidence role,
not the hypothesis. Both roles remain separate. Completed v2 cases require every
retained conflict in original deduplicated order. Unknown IDs, within-hypothesis
role overlap, missing/altered citations, incomplete verdicts and mixed rubric
versions fail validation. No conflicts/reviews means a null conflict rate.

The two-reviewer comparator validates both worksheets, rejects version mismatch
and reports conflict agreement/disagreements separately from supporting citations
and cause labels. It never resolves disagreement, authenticates reviewers or
converts agreement into correctness. CLI tests verify offline operation, source
preservation and exclusive output creation. All 180 evaluation-module tests pass
with the cache located inside the workspace; Ruff and whitespace checks pass.
This is scoped evaluation-harness regression, not a rerun of the complete
PostgreSQL/backend/frontend suite or an actual model benchmark.

No paid model calls, human annotations or operational mutations occurred. Tests
use explicitly synthetic reviewer fixtures. Expert calibration, real model
quality, omitted-counterevidence/whole-response faithfulness, verified causal
diagnosis and mitigation efficacy remain unproven. Prior reports are unchanged;
packaged/live-cluster acceptance and the original full scope remain open.

## Current-state qualification — 2026-10-03

The owned-store native runner now supports `--scenario` for a selected simulated
case and `--real-postgres-pool` only with the pool selector plus isolated-PG
checkpoints. Invalid combinations fail before Docker work. Default omission
still runs eight simulated cases. Selected success is explicitly separate from
full-matrix completion; unique exclusive aggregate names preserve prior runs.
Real-pool aggregation requires the actual PG backend, all five resource checks,
ordinary/recovery checks, integer-zero model calls and no production actions.
Source fingerprints now include every fault-lab Python module as well as the
runner, configs, gateway binary and hashed per-case artifacts.

The exact new CLI was run, not only unit-tested. Aggregate
`fault-lab/results/native_scenario_single_20261003T022707Z_02ec1fa5.json`
passes one selected pool case in 103.329 seconds including owned-stack lifecycle;
child `native_scenario_db-pool-exhaustion_20261003T022839Z.json` passes 24 checks
in 89.917 seconds, with actual four-session exhaustion/recovery and fresh-process
PostgreSQL restoration. The recommendation is advisory, with no rollback dry
run, approval or execution. `selection_mode=single_case`,
`full_matrix_completed=false` and `cleanup_complete=true` are retained. All
source/config and child-artifact hashes were rechecked. Docker inventory shows
no experiment containers/networks; PG activity has zero experiment-pool sessions.
No existing user resources were cleaned or model calls made.

Full isolated-PG regression passes 786 Python tests without skips, one existing
warning; Ruff and whitespace checks pass. Frontend is unchanged from its prior
86-test/build result. This verifies the repeatable single-case lifecycle, not a
fresh eight-case matrix, current packaged/runtime cluster, semantic model score,
expert calibration or causal mitigation acceptance. Original remaining scope is
still open.

Current native real-pool acceptance has been rerun through the full live local
telemetry chain. Report
`fault-lab/results/native_scenario_db-pool-exhaustion_20261003T021520Z.json`
passes 24/24 checks in 90.319 seconds (0.132-second deterministic/no-model
workflow). Four actual PostgreSQL sessions, verified by exact server PIDs, move
from 0/4 to 4/4 reserved leases with direct HTTP 503; explicit test-only clearing
returns 0/4 and HTTP 200 using the original sessions. Actual native services,
live Prometheus/Loki/Tempo, freshly built Go gateway, actual MCP HTTP and compiled
graph recognize the pool failure. Diagnostic sample-time trace retains seven
eligible pool samples and 59 log events without simulation compatibility.
Synthetic deployment provenance makes the recommendation advisory, with no
rollback dry run, human approval or remediation execution. Fresh independent
tool-free inspection restores exact PostgreSQL state/repository and nine
checkpoints. The legacy `approval_interrupt_retained` label passes because
this incident is terminal with no pending graph node, not approval-paused.

Every report source fingerprint was checked against the current tree. Go build
uses `-buildvcs=false` to avoid the existing Xcode/Git license issue; Go tests
pass. Four uniquely owned cached-image/tmpfs observability containers and their
network were closed and then verified absent; a read-only PG query confirms no
remaining application-pool sessions. Existing user Docker data was not touched.
Backend source is unchanged from its prior 772-test isolated-PG regression,
frontend unchanged from the prior 86-test/build pass. No paid calls or production
mutation occurred. This closes current native real-pool/telemetry/recovery
acceptance only: packaged-container/live-cluster, model/semantic/expert and causal
mitigation requirements still remain open.

The compiled graph now projects current primary-service metric/log samples
before generating rule candidates, recording `diagnostic_signal_time_checked`.
Historical/foreign/unverifiable samples remain evidence but cannot supply a
current failure type. No current primary samples means insufficient evidence,
no hypothesis/model call and no action, even with a configured generator.
Current unclassified observations (including insufficient sustained samples)
can still go to the generator for investigation; retained historical context is
still supplied, so this does not establish semantic or whole-response temporal
faithfulness. The Demo legacy summary fallback additionally requires an explicit
local `allow_simulated_signal_summaries` capability and an entirely summary-only
primary set. Provider metadata/source names alone cannot enable it or override
structured samples. Past stored classifications are not rewritten.

New compiled-graph tests cover old/future/untimed/foreign/conflicting signals,
spoofed provider metadata, explicit capability states, retained evidence and
tool-free checkpoint reads. A gapped one-point signal still permits configured
model investigation but cannot provide a rule type. The existing unknown-pattern
positive test now supplies an explicit current sample. Deployment provenance
testing likewise uses current synthetic samples to isolate its independent
guard; the default MCP negative test checks the stale pool samples precisely
(the default memory metric has current healthy samples), abstention and no
approval. Earlier broad zero-eligible-sample test assumptions were corrected
without relaxing production checks.

Final isolated-PostgreSQL regression passes 772 Python tests, no skips, one
existing warning; Ruff and diff whitespace checks pass. Fresh source-bound
reports `workflow_benchmark_action_guard_20261003T020410Z_65c4b2b0.json` and
`workflow_benchmark_20261003T020411Z_d334eb08.json` retain 105 fixed-protocol
and 150 canonical snapshot cases with no contract failures, executions or model
calls. The normal snapshot run retains 75 approval pauses, 45 advisory outcomes
and 30 abstentions. These are not real-model accuracy or live-cluster acceptance.
Frontend is unchanged from its prior 86-test/build result. No paid calls or
production mutations occurred; full semantic/expert/model and packaged/live
acceptance remain open.

Destructive action applicability now projects current retained samples before
profile checking at proposal, pre-intent and pre-dispatch boundaries. Structured
metrics require finite/non-boolean values and explicit aware timestamps in the
last five minutes; chronological uniqueness/conflicting duplicates and the
latest contiguous segment (at most two-minute gaps) prevent old/future/untimed/
duplicate/gapped values from supplying sustained action signals. Log event
time uses the same clock/window. Original evidence remains unchanged, with
evaluation time and eligible counts retained in the trace. A six-minute approval
delay test rejects both intent and existing-intent dispatch, calling only the
original dry run. Summary-only explicit offline compatibility is still marked
simulation-only, never structured freshness proof. This does not refresh tools,
verify cross-metric alignment, or retroactively certify historical classification.
The default MCP synthetic samples are old; positive MCP/Kubernetes-reader/event
checkpoint tests now explicitly provide current synthetic sample timestamps
through actual tool schema/auth/transport, including PostgreSQL writer SIGKILL
and fresh tool-free recovery. A negative actual MCP/graph fixture preserves the
old samples and verifies advisory-only output with no approval interruption.
The initial full run identified five outdated positive fixtures; production
checks were not weakened to retain their former approval behavior.

Final full isolated-PostgreSQL regression passes 758 Python tests, no skips,
one existing warning; Ruff and diff whitespace checks pass. The source-bound
105-case report `workflow_benchmark_action_guard_20261003T014054Z_e2f302bf.json`
has 90 negative rejections, five explicit mismatch rejections, ten controls
paused/rejected and no contract failures or executions. The 150-case clean
report `workflow_benchmark_20261003T014056Z_f1f880d3.json` has zero failures,
75 pauses, 45 advisory outcomes and 30 abstentions. These remain fixed synthetic
protocol/rule measurements, not model semantic/causal accuracy or live-cluster
acceptance. Frontend remains unchanged from its prior 86-test/build result.
No paid calls or production mutation occurred; original model/expert and
packaged/live-cluster requirements remain open.

Offline semantic review now supports comparison of two independently declared
worksheets via `evals/compare_semantic_reviews.py`. Both must pass the existing
source/context/citation integrity and completion contract before overlap is
compared. Mixed reviewer pools or shared normalized declarations are rejected;
pending/omitted cases and failed sources remain explicit in coverage. Normalized
cause and citation verdict agreement are separate, with nominal Cohen's kappa;
empty/undefined denominators remain null and abstentions add no scored items.
Uncertainty counts and exact disagreements/rationales are retained without
merging worksheets or adjudicating them. Agreement is not accuracy, expert
authenticity, causal proof or semantic faithfulness; those quality fields remain
null. CLI tests verify original inputs/output preservation, and real compiled
graph output with mocked model HTTP remains pending without invented scores.
Full isolated-PostgreSQL regression passes 732 Python tests without skips, one
existing warning. Ruff and whitespace checks pass. Frontend/backend runtime
behavior is unchanged from the previous action-gate revision; no paid calls,
cluster mutations, new browser acceptance or actual expert agreement results
are claimed. Live packaging/cluster and model/expert acceptance remain open.

Unrecognized, negated and multiple-family leading causes now fail closed before
restart/rollback dry runs and approval, including offline summary fixtures.
Hypotheses and investigation recommendations remain available. Both pre-intent
and pre-dispatch rechecks reject a previously approved cause changed into these
unresolved states, without replaying execution. A recognized phrase remains a
necessary consistency condition only, not semantic or causal certification;
legitimate unknown paraphrases can be withheld. The new actual-graph protocol
report `workflow_benchmark_action_guard_20261003T010637Z_37edc8f0.json` records
80 cases: 65 invalid/unresolved cases and five explicit mismatches blocked
before dry run, ten controls paused then rejected, zero execution/model calls.
The fresh 150-case snapshot report
`workflow_benchmark_20261003T010643Z_13ef78e3.json` has zero contract failures,
75 approval pauses, 45 advisory outcomes and 30 abstentions. Both retain source
fingerprints and hashed per-case records, without overwriting earlier results.
Full isolated-PG regression passes 717 Python tests, no skips, one existing
warning; Ruff and diff whitespace checks pass. An initial restricted regression
was interrupted after local socket permission failures; the authorized isolated
socket run exposed one obsolete smoke expectation, now covered with unknown
and recognized positive fixtures, and passed on rerun. No model or production
mutation occurred. Frontend is unchanged from its prior 86-test/build pass;
no new browser acceptance is claimed. General semantic/model evaluation and
live-cluster/container acceptance remain incomplete.

The approval area now exposes scoped signal checks, bounded phrase-family
status, exact evidence IDs and explicit unverified semantic/causal/efficacy
warnings. Backend assessment metadata binds the displayed check to the current
leading hypothesis, citation set, service and action profile. Legacy, stale or
malformed bindings display unknown rather than certified. This is display-only;
backend execution rechecks and named human approval remain authoritative.
Current isolated-PostgreSQL regression passes 705 Python tests without skips
(one existing warning). Console typecheck/build and 86 Node tests pass; lint has
zero errors and three existing generated-declaration warnings. Local default
desktop and 390×844 checks with mocked Kubernetes HTTP through actual MCP/graph
fixtures confirm review rendering and repaired narrow-screen command/grid
wrapping, with screenshots retained. No approval or mutation was submitted.
Development hot reload emitted existing multiple-renderer warnings and browser
locators intermittently timed out; screenshot/scroll checks completed, without
claiming those warnings resolved. Earlier benchmark fingerprints predate this
assessment-metadata change; no new model benchmark, paid calls or live-cluster
acceptance is claimed.

An explicit known-family contradiction filter now runs before action proposal,
before execution intent and before dispatch. It fixes the measured CPU-text /
pool-signal mismatch: all five prior exact-family probes are rejected in the
fresh 70-case report `workflow_benchmark_action_guard_20261003T004238Z_38954dd1.json`.
The other 50 negative cases remain blocked and ten positive controls still
pause and are rejected by the benchmark. Five unrecognized-wording probes still
propose actions; negated/ambiguous/unknown text is recorded unresolved rather
than certified. This is bounded phrase contradiction handling, not general
semantic entailment or causal validation. Full isolated-PG regression is 705
tests without skips, one existing warning; the fresh 150-case clean run
`workflow_benchmark_20261003T004325Z_9d433f2e.json` has zero lab-contract failures
or executions. No model requests or cluster mutations occurred. Original live
acceptance and real semantic/model evaluation remain incomplete.

Adversarial workflow evaluation now measures 65 fixed protocol fixtures through
actual LangGraph: 50 negative citation/action cases block before dry run, ten
positive controls pause and are rejected, and five semantic mismatch probes
remain unblocked at the action gate. Matching pool signal citations with the
free-text cause "CPU saturation" still lead to pool-profile rollback proposals.
This is a concrete open semantic-consistency defect, not a safety success; no
mutation is executed because the benchmark rejects all pauses. Report:
`evals/results/workflow_benchmark_action_guard_20261003T003940Z_bacddc93.json`.
Generator is fixed/no-model; all model robustness/faithfulness metrics remain
null. Full isolated-PG regression is 685 tests without skips and one existing
warning. The semantic mismatch and original live/model acceptance requirements
remain open. No paid calls or cluster mutations occurred.

The current actual-LangGraph/BM25 offline benchmark was rerun over 150 canonical
synthetic snapshots after the action gate changes. Final report is
`evals/results/workflow_benchmark_20261003T003502Z_f3b9481a.json`, with 150 hashed
per-case records, runner/fixture/dataset/corpus and all orchestrator/MCP Python
source fingerprints. Rules classify 120/120 fault cases; service and scorer-only
lab-action selection match 150/150; all 30 negatives abstain. All 75 destructive
proposals remain approval-paused then are rejected by the benchmark operator;
45 outcomes are advisory, and execution attempts are zero. These fixtures do
not test malicious citations or prove causal action efficacy. Model root-cause
accuracy, semantic faithfulness and causal efficacy explicitly remain null.
Mean in-memory snapshot workflow latency is 6.88ms, not a live/model diagnostic
latency. Run-specific exclusive artifact creation preserves earlier reports;
an initial same-turn run without action scoring is also retained separately.
Full isolated-PG regression is 672 tests without skips and one existing warning.
No paid calls or cluster mutations occurred.

Action proposals now require the leading hypothesis's cited primary-service
metric/log observations to uniquely reproduce the classified action profile.
Wrong tools, missing/competing signals, ambiguous rank one and unresolved
conflicting citations cannot reach dry run or approval interruption. Applicability
is rechecked before durable intent and before dispatch/receipt generation;
tests mutate cited signals at both boundaries and confirm no dispatch. Legacy
summary-only Demo fixtures have explicit, recorded simulation compatibility;
remote MCP and the snapshot benchmark do not. Full isolated-PG regression is
671 tests without skips (one existing Starlette/httpx warning). This is bounded
signal consistency, not semantic entailment, causal efficacy or live mitigation
acceptance. Current semantic model evaluation and capacity-blocked packaging
remain incomplete. No paid calls or cluster mutations occurred.

Local browser acceptance now verifies the event panel with actual MCP/compiled
graph fixture output and mocked Kubernetes HTTP: default desktop and 390×844
narrow viewports, exact evidence anchor navigation/expansion and unavailable
event-query messaging. Narrow-screen horizontal clipping was found and fixed
by stacking observation fields; event-panel clientWidth equals scrollWidth at
both checked sizes (desktop 881px, narrow 349px). Screenshots are retained under
`docs/screenshots/`. No approval was submitted and fixture HTTP exposes no
mutation endpoint. Typecheck/build and all 78 Node tests pass; lint has zero
errors and three existing generated-declaration warnings. This is a scoped
local-browser acceptance, not live Kubernetes, all screen sizes/browser engines,
human accessibility review or model accuracy evaluation. Backend source is
unchanged, with the prior full isolated-PG result still 660 tests.

The console incident overview now surfaces event first/latest timestamps and
sampled-anomaly-window relationships, with exact evidence links and explicit
non-causal/no-remediation-authority caveats. Status, identity, provenance and
timestamp projection checks fail closed; malformed/ambiguous records do not
become a reliable timeline. Typecheck/build and 78 Node tests pass, including
actual React component HTML rendering and integration checks; lint has zero
errors and three existing generated-declaration warnings. No current browser
visual/layout or live-cluster UI acceptance is claimed. Backend code is unchanged
in this turn; its last full isolated-PG regression remains 660 passing tests.
No paid calls or cluster mutations occurred.

Event context now has real PostgreSQL/fresh-process crash-after-commit acceptance.
Actual MCP/compiled-graph writers are SIGKILLed after committing checkpoints and
repository state; independent tool-free readers recover exactly matching event
evidence, non-causal comparisons, history and approval interrupts. Available and
permission-denied event queries are covered, with Kubernetes HTTP still mocked.
Full isolated-PG regression is 660 tests without skips and one existing
Starlette/httpx deprecation warning. This closes the event-state process recovery
gap, not mid-write failure acceptance, actual cluster discovery, semantic causal
evaluation or capacity-blocked packaged runtime. No paid calls or cluster
mutations occurred.

Optional Kubernetes event observations now flow through GET-only discovery,
MCP field revalidation, cited evidence and checkpointed non-causal anomaly-window
comparisons. Exact Deployment UID/scope, fixed reasons, timestamp/count limits,
message exclusion and conflicting Event UID rejection are tested. Unavailable,
empty and disabled timelines are distinct; missing times stay unknown. Aggregate
interval overlap grants no causal, release-revision or rollback authority.
Full isolated-PG regression is 658 tests without skips, with one existing
Starlette/httpx deprecation warning. Mock event/MCP/graph readback and read-only
RBAC rendering pass, not actual cluster or separate-process event acceptance.
Current paid model evaluation, full release chronology and capacity-blocked
packaged runtime remain incomplete. No paid calls or cluster mutations occurred.

Kubernetes deployment revision discovery now has an opt-in GET-only API reader,
exact owned-resource filtering, bounded complete-list checks, read-time identity
stability, no fixture fallback and independent read-only RBAC overlay. Mock
Kubernetes→real MCP→compiled graph acceptance and configuration rendering pass;
full isolated-PG regression is 622 tests without skips. Rollback dispatch also
checks unique owned targets. This is not actual cluster acceptance, release
chronology, deployment causation or semantic action validation. Those, current
model evaluation and capacity-blocked packaging remain incomplete; completion
is still unproven against the original full-system requirements.

Deployment provenance is now honest end-to-end: the current remote catalog is
synthetic, not live Kubernetes discovery. Unverified/wrong-scope targets cannot
create remote rollback proposals or execute after legacy state/intent recovery;
new/durable dispatch gates preserve the existing approval and intent checks.
Simulation-only demo/snapshot target paths remain explicit. 603 Python tests
pass without skips, plus Go race tests, including actual MCP/audit and mock
observed-contract approval tests. This closes the misleading source/proposal
gap, not live deployment history, semantic causal validation, separate-process
upgrade acceptance or a fresh native matrix. Historical reports are unchanged.
Model evaluation and capacity-blocked packaged runtime remain incomplete.

Opt-in real-pool packaging now preserves local-only targeting via a dedicated
socket-sharing database, with actual Compose merge/Kustomize render tests,
static deployment contracts and a bounded read-only startup probe verified
against isolated PG. Full regression is 589 passing Python tests without skips.
The new Docker image build failed at base-layer registration due to disk
exhaustion; runtime container/non-root/socket and cluster/full-stack acceptance
remain unproven. User Docker data was not cleaned. The standalone Kubernetes
experiment does not replace the original full telemetry/Agent deployment
requirement. Current semantic model evaluation also remains incomplete.

Real PostgreSQL application-pool exhaustion now passes one complete native
telemetry→Go→MCP→deterministic graph experiment, 23/23 checks, including exact
owned sessions, actual 4/4 leases, evidence retained in the incident, 503→200
after explicit clear, approval pause and independent-process PG recovery.
The failed first sampling run remains alongside the successful artifact;
historical simulated matrices are not upgraded. Full regression is 574 passing
Python tests, no skips, plus Go race tests. Zero model calls or remediation
executions. Other real fault modes, new packaging and current model semantic
evaluation are still unproven; full project completion remains unproven.

An opt-in lab mode now exercises actual PostgreSQL application-pool exhaustion,
not merely synthetic pool gauges/delay. Local resource/ASGI tests cover real
sessions, measured leases, acquisition timeout, authorized clear/expiry and
shutdown. Remote/implicit targets and default fault tokens are rejected. This
partially strengthened fault authenticity; at that stage telemetry-to-Agent
acceptance for this mode was unproven. New container/Kubernetes builds,
server-wide exhaustion and other simulated scenarios remain unproven. Existing matrices explicitly stay
simulation and are not retroactively upgraded. Completion remains unproven.

Known input-policy bypasses now include Chinese/full-width/hidden-character
forms, with 35 new tests proving rejection before tools/model/persistence and
preservation of legitimate question text. Full isolated-PG regression passes
546 tests without skips. This is heuristic fixed-case coverage, not proof of
general injection resistance or model unsafe-action rejection; conservative
false positives remain possible. Auth/approval remain independent. No paid
model run was authorized during these continuations, and current semantic
model benchmarking remains incomplete. Full completion is unproven.

Finalized graph-model batches can now continue unattempted cases under exact
revision/data/prompt/model/provider checks, separate paid authorization and a
one-child reservation. Old successes and failures are never silently rerun or
overwritten; offline tests prove call-count accounting and drift/tamper refusal.
This closes finalized-result continuation, not requests ambiguous after SIGKILL;
those still require manual reconciliation. The 122-test evaluation suite passes
without model calls. Current paid semantic evaluation and calibration remain
unfinished; full goal completion is unproven.

Cache-unavailable startup no longer blocks access to canonical incident data.
Bounded Redis probing preserves the original reconnectable client, exposes only
an initial-probe snapshot and keeps configuration/database errors fatal. Tests
using a real absent Redis socket plus isolated PG prove persisted state and full
checkpoint SSE across API lifecycles; a separate owned Redis process started
after API readiness proves real-client reconnect without restart (memory
checkpoint in that test). Full regression is 504 passing tests with no skips.
This closes the Redis-unavailable-startup gap named below, not externally hosted
API crash recovery or a new current live fault matrix. No model calls/actions;
owned Redis was cleaned and PG stopped with data retained. Completion remains
unproven against the original end-to-end requirements.

Committed model-abstention state now survives SIGKILL of its original writer
and exact full-state/history/no-pending-approval inspection in a separate
tool-forbidden reader process against dedicated PostgreSQL. The Responses
adapter uses a local HTTP fixture, not a paid model call. Full regression passes
497 tests with no skips; the database was stopped with data retained. This closes
the new-state PG process-recovery gap stated below, but does not prove generation-
in-flight recovery, real-model abstention quality or current semantic RCA scores.
Broader completion remains unproven; see [verification.md](verification.md).

Semantic evaluation now has offline worksheet preparation, source-bound reviewer
annotations and subset scoring with coverage rather than fabricated full-batch
accuracy. Tests include retained actual-graph outputs and malformed/tampered
reviews. No expert annotations, current-model paid batch, reviewer calibration
or whole-response faithfulness proof exists yet; therefore the original semantic
evaluation requirement is still incomplete. See `evals/SEMANTIC_REVIEW.md` and
[verification.md](verification.md).

The current model schema/prompt permits explicit abstention instead of requiring
at least one root-cause hypothesis. Mock HTTP responses through the real model
adapter and graph confirm `insufficient_evidence`, no action/approval, distinct
abstention trace and checkpoint readback; missing telemetry already avoids the
model entirely. Generation/refusal failures are not silently treated as valid
abstentions. Full isolated-PG regression passes 478 tests without skips. This
is not proof of real-model abstention quality or semantic RCA accuracy; dated
paid results use an older prompt, and new-state PG process recovery remains
unverified. Broader goal completion remains unproven.

Current-model workflow evaluation now has a dedicated actual LangGraph runner
with explicit paid gating, a credential-free 150-case plan, no execution,
per-case durable result files and source fingerprints. All 87 evaluation tests
pass without model calls. This is harness verification, not a paid current-model
benchmark: semantic model RCA/faithfulness remain unscored, healthy/missing-data
model behavior remains unproven, and result-resume is not implemented. The
historical prompt comparison and live deterministic matrix do not fill these
gaps. Completion remains unproven; see [verification.md](verification.md).

The latest response contract makes sanitized delivery-degradation metadata
readable to configured CORS browser origins and documents optional headers in
OpenAPI without changing incident bodies or authorization/approval behavior.
Three contract tests pass; full regression is now 445 Python tests with isolated
PostgreSQL and no skips. This is ASGI HTTP/schema evidence, not a new rendered
browser workflow or runtime fault matrix. CORS remains separate from bearer
authentication. Prior dated runtime/model limitations below still apply; see
[verification.md](verification.md).

Latest production evidence separates committed results from cache delivery:
post-persistence Redis error/timeout returns canonical state with explicit
degraded-delivery headers, no retry or execution replay; persistence errors
still fail. A combined single pool-fault experiment passes 46 checks, including
new diagnoses created during owned Redis pause and loss (HTTP 201 in 2.147/0.106
seconds, exact repository/checkpoint agreement, waiting approval). Full regression
is 442 passing Python tests. No live model/approval/action calls occur and owned
resources are cleaned. Live approval behavior, startup with Redis unavailable,
commit/publish-gap crashes, current semantic model evaluation and broad latest
matrix coverage remain unproven. See [verification.md](verification.md).

Latest evidence adds a two-second cooperative cache-read timeout and actual
owned Redis SIGSTOP/SIGCONT verification. A combined API-crash/Redis-stall/loss
pool probe passes 36/36 checks: full/cursor checkpoint replay takes 2.012/2.011
seconds with explicit timeout/read-only provenance, normal delivery resumes
after SIGCONT, and subsequent SIGKILL fallback also passes. Current regression
is 437 Python tests passing. No model/approval/execution calls occur; owned
resources are cleaned and PostgreSQL stopped with data retained. The hung-cache
I/O gap is now tested for a paused Redis process, not a total HTTP/DB deadline,
uncooperative cancellation, Redis disk recovery, in-flight diagnosis/execution,
approval/resume or model semantic quality. See [verification.md](verification.md).

The latest production API now replays the exact remaining checkpoint trace when
the delivery cache is unavailable, invalid or incomplete, with explicit read-only
source metadata and no cache repair/execution. A real single-scenario Redis
SIGKILL experiment passes all 31 checks: full 42-event replay and exact 20-event
cursor continuation still work through the authenticated API from PostgreSQL.
The incident remains waiting for approval. Current regression is 435 Python
tests passing; no model/approval/action calls occurred and owned resources are
cleaned. This is checkpoint-backed stream availability, not Redis data/disk
recovery or proof of hung-read deadlines, in-flight/execution crash behavior,
approval-resume or model semantic quality. Production API changed; prior matrix
hashes describe its older revision. See [verification.md](verification.md).

Latest evidence extends the single committed-diagnosis API crash test to an
owned Unix-only Redis and authenticated SSE replay: 27/27 checks pass. The new
API retains exact task metadata and all 42 events, and cursor 22 returns exactly
20 remaining events with the expected approval-paused state. PostgreSQL state/
history recovery also passes. Current regression is 430 Python tests passing;
zero model/approval/execution calls occurred and owned resources are cleaned.
This proves Redis task/SSE survival across an API crash while Redis stays
running, not Redis-process restart or disk durability, in-flight diagnosis,
execution, approval/resume or current-model semantic quality. The original
eight-case batch was not rerun through this new mode. Dated evidence and limits
are in [verification.md](verification.md); the following notes are historical.

The latest single pool scenario now proves recovery through a real authenticated
API process crash after committed diagnosis: 23/23 checks pass, including SIGKILL
exit, distinct new API process, full HTTP state/history retention, and independent
PostgreSQL checkpoint/repository/approval-interrupt inspection. It retains
42 trace events, 25 evidence items and eight checkpoints while waiting for
approval, with no model or execution calls. Current regression is 421 Python
tests passing. See the dated result in [verification.md](verification.md).
This is not diagnosis-in-flight, approval-resume, execution, Redis/SSE or model
evaluation proof. The earlier eight-case batch was not rerun through API crash
mode and its verifier hashes do not describe the new optional probe code.

Current native evidence now includes a fully green same-revision PostgreSQL
batch: eight cases and 144 named checks in 801.413 seconds. Every case verifies
real telemetry, exact baseline log/span identity, diagnosis/remediation routing
and separate-process recovery of complete state/history/interrupt position.
Five proposals remain approval-paused; three diagnoses are advisory-only, with
zero model/production action calls. All eight artifact hashes, 36 source/config
hashes and the gateway hash were independently checked against that run. Full
regression is 416 passing Python tests; temporary resources are cleaned and
PostgreSQL stopped with data retained. See
[`native_scenario_matrix_20261002T205334Z.json`](../fault-lab/results/native_scenario_matrix_20261002T205334Z.json)
and [verification.md](verification.md).

The previously missing full latest-version native batch is therefore verified
at this revision. The following chronological notes are historical, not a
contradiction of that dated result. Overall completion remains unproven: this
is deterministic bounded-fault integration with fixture knowledge/metadata,
not current model accuracy/semantic faithfulness, original Compose/API lab
verification, actual live API crash/Redis recovery or approval/resume proof.
All log matches appeared on the first query, so the earlier missing-log cause
and actual outage-delivery guarantees remain unestablished.

Latest evidence: the verifier now uses bounded read-only log visibility polling,
retains attempt/error/shipper diagnostics and requires all exact span pairs,
including multiple spans per service. A fresh memory probe passes 18/18 checks
and preserves its approval pause through PostgreSQL recovery; full regression
is 416 Python tests passing. Its first round already matched, so delayed
visibility and the original missing-log cause are not established by this run.
Full latest eight-case verification and the broader model/production recovery
requirements below remain unproven. See the dated artifact and scope in
[verification.md](verification.md); historical failures remain unchanged.

The August measurements below are historical evidence for that revision, not
completion proof for the current worktree. Current regression evidence and
scope are recorded in [verification.md](verification.md). The current live
canonical fault matrix has been rerun at the workload-corrected revision through independent native workloads
and disposable observability stores, with all eight cases passing. The original
Compose/API/persistent-lab matrix still has not been rerun: project containers
are stopped and the lab PostgreSQL's latest startup failed with
`No space left on device`. No user images, volumes or database data have been
removed to work around this.

The current eight-case native matrix passes 96 named checks in one fresh batch,
with five proposals paused for approval, three read-only diagnoses and zero
model calls/production actions. Source/config/binary and per-case hashes were
independently verified, as were citation-ID contracts and absence of execution
steps. The workload-corrected revision also passes all 368 Python tests with
isolated PostgreSQL, no skips. Full evidence and failed precursor runs are in
[verification.md](verification.md). This is deterministic simulated-fault
integration, not a semantic faithfulness/model-accuracy benchmark or proof of
production causal attribution and live-fault durable recovery.

Subsequently, log identity was corrected to use actual active OpenTelemetry
trace/span IDs, with client request correlation kept separate. A real healthy
native probe verified payment/ledger Loki logs against exact observed Tempo
SERVER spans, while diagnosis correctly abstained. That revision passed 373
Python tests with isolated PostgreSQL, no skips, plus Go race tests. The earlier
eight-case matrix has not been rerun after this identity revision; do not present
its source hashes as matching these later files. See the dated probe and limits
in [verification.md](verification.md).

The subsequent single native connection-pool scenario adds isolated PostgreSQL
checkpoint/repository storage and recovery inspection in a separate process
after closing the writer connection. All 18 checks passed: complete state,
42 trace events, 25 evidence items, eight checkpoints and the approval interrupt
were retained. Current regression is 382 Python tests with no skips. No model
or execution calls occurred. This is a bounded simulated fault, not real database
pool exhaustion or proof of live API crash/approval-resume recovery. The full
eight-case durable matrix and Redis recovery remain unverified. See the dated
artifact and reproduction limits in [verification.md](verification.md).

The preceding full PostgreSQL native matrix completed eight scenarios: every
fresh-process checkpoint/repository/history/interrupt recovery check passed,
but the aggregate is **failed**, with seven passing cases and 143/144 checks.
Downstream timeout lacks the required baseline log/span references; no evidence
was fabricated or requirement relaxed. All source/config/binary and per-case
hashes were independently verified, with zero model/action calls and owned
resources cleaned. Current regression is 394 Python tests passing. Thus full
eight-case recovery inspection is verified, but full eight-case end-to-end
success, live API crash recovery, Redis and approval/resume are not. The lab
shipper's drop-on-HTTP-error behavior is a known unresolved risk, not a proven
cause of that missing reference. See [verification.md](verification.md).

The latest revision adds bounded transient log-push retries, queue/record limits
and explicit loss counters. Eleven new tests cover retries, rejections, overflow
and cancellation; current regression is 405 passing Python tests. A separate
downstream-timeout probe now passes all 18 checks, including exact baseline
log/span identity and complete fresh-process PostgreSQL recovery. It executes
no model or remediation calls. This does not establish the previous missing-log
cause, real-outage delivery or a fully green latest eight-case batch. The earlier
failed aggregate remains unchanged and its hashes are not current shipper hashes.
Live API crash, Redis, approval/resume and current-model semantic evaluation
remain unproven; see [verification.md](verification.md).

The next full batch at the log-retry revision passed all eight log/span and
fresh-process recovery checks, but failed the memory case after a wall-clock
jump prematurely expired the injected fault (seven passing cases, 140/144
checks). Source/config/binary and artifact hashes were verified before the next
edit. Fault TTL is now monotonic; current regression is 408 passing Python tests,
Go race checks and 65 frontend logic tests. A post-fix memory probe correctly
diagnosed growth and retained its approval pause, but failed baseline log/span
correlation (17/18). Thus neither the latest full batch nor the latest single
probe is fully green; missing-reference recurrence remains unresolved. Earlier
successful single reruns do not establish the cause or eliminate this risk.
Full latest-version eight-case verification is still required. Dated artifacts
and limits are in [verification.md](verification.md).

Current changes include timestamped anomaly windows, uncertainty/abstention,
all-hypothesis citation checks, durable citation rejection, partial telemetry
failures, dependency observation queries, impact UI and new PostgreSQL recovery
tests. Their fixture and fresh-connection tests do not replace live MCP/fault
verification. Dependency overlap is observed correlation, not proven causal
direction. Citation-ID grounding is not semantic faithfulness. The historical
model comparison is a prompt/retrieval benchmark, not execution of the current
durable graph; its outputs have a separate rescored artifact. Do not use the
historical percentages below as current end-to-end Agent performance.

The live verifier now reports missing hypotheses/remediation as failed checks,
retains candidate/impact/error metadata and reports primary metric signals
separately from dependency samples. These verifier changes are regression-tested
but await a functioning persistent Compose lab. The separate native matrix now
verifies live gateway/MCP/workflow fault diagnosis without PostgreSQL/Redis.
Full original project completion remains unproven.

A narrower live topology probe was completed on 2026-10-03 local time without
starting the database or clearing Docker data. Actual native fault-lab service
HTTP spans passed through Collector, Tempo, Go, MCP and a memory LangGraph
checkpoint. Both checkout→payment and payment→ledger parentage references were
retained. See the dated live-topology artifact in [verification.md](verification.md).
The probe's other telemetry/reasoning remains synthetic and its memory store
is not durable; it does not replace the full fault matrix, model evaluation,
or production identity-provider integration. No new causal-root claim follows
from observed call direction.

A subsequent single CPU experiment also used actual Prometheus and Loki data,
not Demo telemetry. After fixing bounded query resolution and non-finite
Prometheus sample handling, all seven checks passed in 74.438 seconds and the
workflow stayed waiting for approval. Its failed precursor artifacts are also
retained. See the dated native-live-telemetry evidence in
[verification.md](verification.md). CPU intensity is a synthetic fault gauge;
reasoning remains deterministic and persistence is in memory. This closes a
single-case telemetry integration check, not the full live matrix, current
model evaluation, production CPU attribution or identity integration.

An earlier native-harness iteration supported all eight canonical scenarios but
actually ran only database-pool exhaustion and bounded memory growth, with both
passing all 12 case checks. Baseline/post-fault topology are separate; results
include generic-question classification, hypothesis and remediation checks,
approval/advisory boundaries and separate diagnostic/runtime timings. A failed
memory baseline exposed Tempo's unpadded search trace IDs; boundary-specific
normalization is now tested and the successful rerun deliberately exercises a
leading-zero ID. Future baseline timeouts are saved before injection. See the
dated per-case records in [verification.md](verification.md). Pool/Kafka gauges
and deliberate allocations are bounded simulations; no model, real backend
resource exhaustion or production action was exercised. The current full
Compose/persistent matrix and other original completion gates remain unproven.

## Historical audit — 2026-08-08

Audited on 2026-08-08. This maps the requested portfolio project to executable
implementation and checked-in measurements. Demonstrated behavior is kept
separate from production claims that have not been made.

## Requested incident workflow

| Requirement | Implementation | Verification |
| --- | --- | --- |
| Query logs, metrics, traces, and dependencies | Concurrent collection in `services/orchestrator/incident_copilot/langgraph_workflow.py`; bounded PromQL, LogQL, and TraceQL in the Go gateway | Python integration tests, Go backend tests, live eight-scenario matrix |
| Locate the anomalous interval | Bounded start/end windows; retained sample timestamps, latest values, and peaks | `fault-lab/faultctl.py` and per-scenario `metric_signals` |
| Trace upstream/downstream impact | Service dependency edges plus Tempo trace summaries become cited evidence | Every live scenario has `live_traces=true`, trace evidence, and 32 trace events |
| Retrieve runbooks, history, and technical docs | BM25 index over 18 versioned documents with stable URIs and hashes | 150 cases: 100% Recall@5, 97.9% NDCG@5 |
| Generate root-cause hypotheses | Deterministic diagnostic profiles plus optional strict-schema OpenAI Responses adapter | Credentialed 450-call comparison and structured-output tests |
| Recommend remediation | Evidence-validated restart, rollback, or advisory proposal | Eight-family workflow and MCP integration tests |
| Require approval for high-risk actions | LangGraph `interrupt()`/resume, signed action-bound receipt, scopes, dry-run, RBAC, and idempotency | Five live destructive proposals rejected; zero executed |
| Preserve the complete trace | PostgreSQL checkpoints/audit plus Redis-ordered stream; model and tool call/result events | Restart recovery, SSE tests, and live `tool_trace=true` checks |

Primary live evidence:
[`2026-08-08-agent-scenario-matrix.json`](../fault-lab/results/2026-08-08-agent-scenario-matrix.json).

## Product surface and architecture

- The React console in `app/IncidentConsole.tsx` exposes the incident question,
  timeline, dependency graph, metric panels, evidence citations, structured
  tool-call payloads, and approve/reject controls.
- FastAPI and LangGraph implement one explainable state machine: classify,
  select services, query telemetry, retrieve context, generate hypotheses,
  validate evidence, propose remediation, interrupt for approval, and execute
  or simulate.
- PostgreSQL stores incidents, decisions, checkpoints, idempotency, audit, and
  evaluation records. Redis stores task state and ordered trace events.
- The custom MCP server exposes nine typed tools: `query_metrics`,
  `search_logs`, `query_traces`, `get_service_dependencies`, `search_runbooks`,
  `search_incident_knowledge`, `get_recent_deployments`, `restart_deployment`,
  and `rollback_release`. It enforces generated JSON Schemas,
  authentication/scopes, origin checks, timeouts, retries, rate limits, signed
  approval, idempotency, audit logs, and read/destructive separation.
- The Go gateway owns the telemetry boundary. Models cannot supply arbitrary
  PromQL, LogQL, or TraceQL; validated query names and bounded time ranges are
  mapped to Prometheus, Loki, and Tempo requests.
- Docker Compose runs the local stack. The Kustomize base and Kind verifier
  cover namespace-scoped RBAC, hardened workloads, persistence, network policy,
  restart recovery, and rejection-before-execution.

## Repeatable fault environment

The lab contains four instrumented services, continuous traffic, PostgreSQL,
Prometheus, Loki, Tempo, OpenTelemetry Collector, and Grafana. It injects eight
bounded faults: database pool exhaustion, downstream timeout, CPU saturation,
memory leak, deployment regression, Kafka consumer lag, retry storm, and cache
penetration.

The 2026-08-08 run diagnosed all 8/8 from generic questions. All scenario checks
passed, including service and root-cause classification, remediation choice,
live metrics/logs/traces, retrieval, signal and tool traces, metric-panel data,
and rejection-before-execution where applicable. Mean diagnostic latency was
103.32 ms and nearest-rank p95 was 143.67 ms in this local deterministic Agent
run.

## Evaluation and guardrails

- Dataset: 150 synthetic incidents spanning all eight fault families.
- Retrieval: 100% Recall@5 and 97.9% NDCG@5.
- Model comparison: 450 credentialed `gpt-5.6-terra` calls. Root-cause Top-1 was
  19.3% for single prompt, 64.0% for RAG, and 81.3% for the validated Agent;
  grounded-response rates were 0%, 99.3%, and 100%.
- Safety: all benchmark variants rejected unsafe execution, and the live matrix
  rejected 5/5 proposed destructive operations with zero execution.
- Cost per incident: $0.00318, $0.00389, and $0.00468 respectively. Mean model
  request latency: 2.63 s, 2.41 s, and 2.79 s respectively.

The guardrail layers cover prompt injection and authorization at input,
schema/scope/rate/dry-run/idempotency checks at tools, evidence-ID grounding at
output, and signed human approval plus Kubernetes RBAC at execution.

Model evidence:
[`model_benchmark.json`](../evals/results/model_benchmark.json) and
[`model_benchmark_cases.jsonl`](../evals/results/model_benchmark_cases.jsonl).

## Honest boundary

This is a repeatable synthetic/local engineering system, not proof of
production accuracy. The model result covers one dated model snapshot. The
Kubernetes deployment was verified in local Kind, not a managed cluster with
public ingress, TLS, or managed secrets. No diagnostic latency or token-cost
reduction is claimed without a separate optimization experiment.
