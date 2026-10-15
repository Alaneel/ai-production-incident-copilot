# OpsPilot — AI Production Incident Copilot

OpsPilot is an evidence-first incident diagnosis and guarded remediation system
for engineering teams. It correlates metrics, logs, distributed traces, service dependencies,
deployments, runbooks, and past incidents, then interrupts before any production
change until a named human approves the exact operation.

The default demo answers:

> 为什么 payment-service 今天延迟突然升高？

with a reproducible database connection-pool exhaustion scenario introduced by
release `payment-v142`.

The same workflow is profile-driven rather than hard-coded to that demo. It
classifies eight incident families from the current metric/log evidence,
retrieves incident-specific knowledge, proposes a matching remediation, and
routes advisory-only outcomes around the approval interrupt.

## Verification status

Current source update (2026-10-08): authenticated diagnosis now has a
node-boundary SSE route that rereads LangGraph checkpoints and writes canonical
incident progress before delivery. Disconnect cancels its owned graph iteration,
without background restart or automatic POST replay. Approval requires an actual
retained human interrupt, not a proposal's early `waiting_approval` status;
premature decisions are rejected before claiming an operation. Development
console credentials are memory-only and split Reader/Operator scope, with exact
backend trust and no automatic anonymous/demo downgrade. This is not production
SSO or per-user identity. See [API authorization](../docs/api-authorization.md).

Current local console acceptance exercises the real HTTP diagnosis stream, from
eight provisional events to explicit completion with 46 retained events. A wrong
operator actor cannot submit a decision; a matching operator rejects the simulated
proposal, retaining one decision and no execution events. The browser backend
used memory stores and deterministic fixture telemetry, not live MCP/Kubernetes
or a model. An explicit `npm run test:stream-contract` additionally passes real
Python/compiled-graph/Pydantic output through the frontend parser and fragmented
fetch-body decoder; EOF without complete remains unconfirmed. The frontend's
209 pure tests, typecheck and build pass separately. See the
[current verification scope](../docs/verification.md#2026-10-08-durable-diagnosis-stream-and-console-contract).

Bounded cross-service candidate RCA keeps
the complaint service separate from the candidate source, retains directed path
provenance and local/symptom citations, and remains advisory-only for remote or
unresolved investigations. Primary classification and action checks reject
explicit wrong metric units/impossible ratios; excluded tool responses cannot
supply a rollback target. The
[32-case cross-service run](../evals/results/cross_service_benchmark_20261007T164402Z_be5fa187b20c48189ff819ca80373e32.json)
passes all structural contracts with stable source fingerprints and zero model
or mutation calls. This measures synthetic service/family candidate localization,
not semantic faithfulness, live RCA accuracy or causal efficacy. See the
[architecture](../docs/architecture.md#bounded-cross-service-investigation) and
[completion audit](../docs/completion-audit.md) for verification scope and remaining
gaps. Fresh current images now pass CPU (35 checks), actual PostgreSQL pool
exhaustion (42), downstream timeout (35) and bounded memory growth (35).
The [four-case aggregate](../deploy/kubernetes/results/kind_fault_matrix_stream_four_20261007T1715Z_4e2e91.json)
accepts their same 76 source inputs/four image IDs with `full_fault_matrix=false`;
the remaining cases are still in progress. These runs make zero model/approval/
suggested-action calls and verify owned cleanup, not causal repair efficacy or
multi-namespace isolation. Current all-eight acceptance and approved action
efficacy remain open.
The same source's
[150-case deterministic run](../evals/results/workflow_benchmark_20261007T164404Z_f5bb6a04.json)
has zero contract failures, model calls or executions; it does not measure model
quality or HTTP streaming. Previous reports below retain their original scope.

Previous source update (2026-10-07): execution acknowledgement no longer implies
`resolved`. A durable read-only rollout/SLI recovery gate, an operator-only
manual recheck, cross-worker PostgreSQL observation locks and the console's
recovery-evidence panel are implemented. Advisory-only proposals no longer claim
a successful dry run that did not happen. Final joint regression passes 1348
tests with 17 dedicated-PG skips; a separate private Unix-only UTF8 PostgreSQL
run passes 34 tests without skips, including recovery across real saver
connections (Kubernetes remains mocked). Frontend typecheck/build and 118 tests
pass; Go tests and Ruff pass. That revision's
[150-case snapshot run](../evals/results/workflow_benchmark_20261007T154339Z_dcae347e.json)
has no failures or execution attempts, but measures synthetic classification/
policy, not model RCA or semantic faithfulness. Recovery-gate images built and
the subsequent isolated
[real-pool acceptance passed 42/42 checks](../deploy/kubernetes/results/kind_live_db-pool-exhaustion_20261007T154949Z_10b0addc.json):
actual four-lease PostgreSQL exhaustion, controlled log/span identity, live Go/MCP
telemetry and exact PostgreSQL/checkpoint recovery in a new Ready API Pod.
The run retained 25 evidence objects/44 events, made zero model/production-action
calls and completed cleanup. Its offline
[single-case aggregate](../deploy/kubernetes/results/kind_fault_matrix_recovery_pool_20261007T160500Z_716e1bfd.json)
accepted that revision with `full_fault_matrix=false`. Those 75 source inputs
predate today's 76-input cross-service source set, so neither that pool nor the
CPU artifact below qualifies the changed current images/full matrix. Recovery
cleared the injected fault, not an Agent-selected remediation.

Prior-source real Kubernetes acceptance (2026-10-07): the target-bound v3
[CPU case passes 35/35 checks](../deploy/kubernetes/results/kind_live_cpu-spike_20261007T151549Z_b4c73a48.json)
using newly built MCP/orchestrator images. The installed MCP read-token client
submits two real server-side dry runs with the same key, gets fresh timestamps,
and rejects a forbidden namespace and missing Deployment. Independent resource
snapshots match target UID/template/configuration and retain the same template,
generation and operation markers. The diagnosis keeps that identity in its
human-approval interrupt; a new Ready API Pod restores the exact PostgreSQL
checkpoint, pending node and interrupt. No approval, model or suggested
remediation runs. The report retains 23 evidence objects/42 events and 74 current
source fingerprints; independent Kind/Docker/private-config inventory confirms
cleanup. Runtime is 155.981 seconds including cluster lifecycle, not diagnosis
latency. CPU remains a bounded busy loop/lab gauge, not a host stress benchmark.
The upgraded v2 [offline aggregate](../deploy/kubernetes/results/kind_fault_matrix_preflight_cpu_20261007T151619Z_d3df17c2.json)
accepts this one case with `full_fault_matrix=false`; old v1 artifacts cannot be
promoted. This is prior-source single-case preflight/approval-checkpoint evidence,
not all-eight coverage, human-approved execution, mitigation efficacy, current
model quality or packet-level proof that no ordinary PATCH request was sent.
Fault recovery clears the injection, not an Agent-selected action.
Final joint backend/evaluation/Kubernetes-verifier regression passes 1138 tests
with 14 dedicated-PG skips; Kubernetes checks comprise 280 of those tests.

Prior source-only update (2026-10-07): orchestrator/MCP now share v3 signed approvals
that bind namespace and a canonical Kubernetes target context. Real backend dry
runs submit `dryRun=All`, retain Deployment UID/configuration/template identity
and owned rollback ReplicaSet identity, and do not persist the patch. Execution
rechecks identity before marker replay, rejects changed non-replay templates or
rollback targets, and signs/forwards the same context through checkpoint,
interrupt, intent and read-only status. Simulation is explicitly labeled; legacy
proposals/receipts cannot silently acquire live authority. The rollback preview
now describes container-image restoration, not full `kubectl rollout undo`.
MCP regression passes 263 tests with two optional dedicated-PG skips; one full
joint orchestrator/MCP/evaluation/Kubernetes-verifier run passes 1016 tests with
14 dedicated-PG skips; 49 focused actual graph/MCP/receipt checks also pass.
The console labels simulation/server/unknown preflight, displays scoped target
identity and disables approval for unknown/legacy/inconsistent records while
preserving rejection. Typecheck/build and 97 Node tests pass; lint has no errors
and three existing generated-declaration warnings. Lab regression passes 161
tests with three optional dedicated-PG skips. These are not live-browser or
new PostgreSQL durability/cluster acceptance claims.
No model or actual remediation runs. This is local mocked/protocol acceptance,
not live Kubernetes admission, mitigation efficacy, current full-eight fault
acceptance or current model-quality evidence. Images/reports have not been
rebuilt/reaccepted for this source update; historical results below stay unchanged.

Previous source update (2026-10-07): signed approval receipts used v2 and bound
namespace explicitly for rollback as well as restart. Wrong-namespace and legacy
receipts are rejected before cache replay/backend dispatch; fresh-key reuse
cannot bypass scope. The real compiled workflow signs the exact remediation
namespace. Joint orchestrator/MCP/evaluation/Kubernetes-verifier regression passes
875 tests with 14 dedicated-PostgreSQL tests skipped; 25 focused graph/MCP
transport tests also pass. No model or real suggested remediation runs.
Kubernetes images were not rebuilt/reaccepted after this source change.
The historical CPU/memory/lag results below remain unchanged and cannot certify
the modified current worktree. The new offline
[matrix consistency verifier](../deploy/kubernetes/verify_fault_matrix.py) requires
eight unique successful cases with current source hashes and matching images;
it [rejects those three stale artifacts](../deploy/kubernetes/results/kind_fault_matrix_stale_receipts_20261007T144300Z_f6ca9627.json)
rather than promoting them into current coverage. Its 78 adversarial tests pass.
At that revision backend preflight/UID binding, current all-eight acceptance,
approved execution, causal efficacy and current model quality remained open.

Historical generalized Kubernetes acceptance (2026-10-07, before v2/v3): the
[CPU case passes 28/28 checks](../deploy/kubernetes/results/kind_live_cpu-spike_20261007T142832Z_a2e80b10.json).
A generic risk-engine question is classified from live telemetry as CPU
saturation. Its restart proposal stops at human approval; no approval/action or
model call runs. A separate DB-enforced read-only process verifies the actual
LangGraph checkpoint, pending node and action/evidence-bound interrupt. The exact
checkpoint ID, interrupt and incident survive replacement by a new Ready API
Pod. Runtime is 162.187 seconds including cluster lifecycle, not diagnosis
latency. The run retains 23 evidence objects and 42 workflow events. All 73
recorded source fingerprints match; cluster/private config are removed and
independent Kind/Docker inventory confirms absence. Eight scenario contracts
are now supported by the runner, with fresh stores per invocation; this one-case
report is not an all-eight matrix. CPU uses a bounded busy loop and lab gauge,
not a host-CPU stress measurement. Nonempty traces are required, but the pool
case's controlled log/span identity is not asserted here. Eighty Kubernetes
tests and 54 selected workflow/action/signal tests pass; full suites/model quality,
approved resume/execution and causal efficacy remain unverified.

The same revision's
[memory case also passes 28/28](../deploy/kubernetes/results/kind_live_memory-leak_20261007T143215Z_bc3f39b5.json)
in a separate fresh cluster (152.969 seconds including lifecycle). Its last
five allocation-gauge samples grow 48 → 58 → 68 → 77 → 87 MiB. The generic
payment-service question yields a memory-growth diagnosis; synthetic release
provenance correctly leaves it advisory-only with no graph interrupt/action.
Its 25 evidence objects, 44 workflow events and exact completed checkpoint
survive API replacement. All source hashes match and cleanup is independently
confirmed. These are two current-revision cases, not the complete eight-case
matrix, and the memory gauge measures bounded retained lab allocations, not RSS.

Before the receipt-v2 change, the
[consumer-lag case passed 28/28](../deploy/kubernetes/results/kind_live_kafka-consumer-lag_20261007T143715Z_9e605f43.json)
in a fresh cluster (137.032 seconds including lifecycle), retaining 23 evidence
objects and 42 workflow events. It paused at restart approval and preserved the
exact graph checkpoint/interrupt after API replacement. No approval/action/model
ran; cleanup is independently confirmed. The lag is a synthetic lab counter,
not an actual Kafka broker/consumer fault. This is historical evidence only.

Latest 2026-10-07 Kubernetes v2 acceptance passes
[28/28 checks](../deploy/kubernetes/results/kind_live_pool_20261007T140934Z_c55eefd8.json).
The controlled failed checkout request now has exact Go/MCP client/server
parentage, a matching server-completion log, 4/4 pool-error trace/request identity
and its trace in search. Diagnosis retains 25 evidence objects, 44 workflow trace
events and 20 actual distributed traces, not an empty envelope. Two real defects
were fixed: FastAPI native automatic telemetry preempted the lab's service-bound
provider, and default Tempo search could omit recent failures after its result
limit. The lab keeps its manual telemetry; Go now requests newest-first TraceQL.
Exact PostgreSQL state restoration in a replacement API Pod and scoped cleanup
still pass. Runtime is 169.462 seconds including cluster lifecycle, not model
diagnostic latency. No paid model or remediation ran. This remains one fault
family, sampled correlation and completed-state recovery, not complete fault
coverage, semantic/causal accuracy, in-flight approval recovery or action efficacy.
Go tests pass; 50 Kubernetes tests pass; 161 lab tests pass with three optional
dedicated-PG tests skipped. The complete backend/frontend suite was not rerun.

Reinspection of the 2026-10-07 Kubernetes report found an empty `traces` array
and zero observed calls. Its old check accepted a typed trace evidence envelope,
so 23/23 does **not** prove delivered Tempo spans. The report remains unchanged.
The v2 verifier now requires nonempty metric/log/trace payloads and a uniquely
identified failed checkout request: Go/MCP-observed client/server parentage,
the exact server completion log, pool-error trace/request identity, and the
same trace ID in MCP trace search. The successful stricter revision is linked
above; do not reuse the old trace-ingestion claim.

2026-10-07 real Kubernetes single-family acceptance now passes
[23 checks](../deploy/kubernetes/results/kind_live_pool_20261007T134228Z_79004dbd.json):
actual PG pool exhaustion/recovery, independent session identity, Python image
source matches, live metrics/logs through Go/MCP, authenticated
deterministic diagnosis, and exact incident restoration in a new Ready API Pod
with a different UID. The report retains 25 evidence objects and 44 trace events.
The new cluster/private config were removed and inventory-confirmed absent.
Two earlier recovery-probe failures remain unchanged. No paid model or suggested
remediation ran; deployment metadata stays synthetic/advisory-only. All 34
Kubernetes tests pass. This is not all-eight fault coverage, model/expert quality,
log/span correlation, in-flight/approval recovery, live release chronology,
network-policy/UI/Grafana or causal mitigation acceptance.

2026-10-07 merged-Compose acceptance now passes
[21 checks](../fault-lab/results/compose_platform_20261007T131716Z_c3b86ddc.json):
four-service live traffic/telemetry reaches Go/MCP and the authenticated API,
which diagnoses actual PostgreSQL pool exhaustion and preserves exact incident
state after its container restarts. Runtime uses PostgreSQL checkpoints, Redis
and deterministic/no-model reasoning. Synthetic deployment metadata remains
advisory-only. The fresh UUID project has no published ports and uses generated
test credentials, never `.env.local`; its owned resources were removed and
confirmed absent. The first run's verifier-field error remains in a separate
failed report. This closes one merged-platform fault-family acceptance, not
all-eight faults, UI rendering, Kubernetes, in-flight crash, model quality or
causal remediation efficacy. Ten packaged/Compose verifier tests pass.

2026-10-07 isolated packaged-service acceptance: a fresh fault-lab image now builds
successfully. [22 runtime checks](../fault-lab/results/packaged_pool_runtime_20261007T131143Z_e93e8c82.json)
verify current application-source hashes inside the image, non-root/read-only
operation, an isolated Unix-socket PostgreSQL server with no TCP/host ports,
four real connection leases, exhaustion/recovery and shutdown closure. Owned
containers/network/socket volume were removed and confirmed absent; the image
and existing user resources were retained. This is not merged-Compose,
full-platform telemetry/Agent, Kubernetes or model-quality acceptance.
Offline review now has an explicit v2 conflicting-citation rubric; all 180
evaluation-module tests and five packaged-verifier tests pass. No paid calls or
actual expert annotations were made in this update. Historical full-suite and
native results below retain their dated scope.

Current regression (2026-10-03): 786 Python tests pass with isolated PostgreSQL.
The [self-contained native single-run command](../fault-lab/README.md)
now launches fresh owned stores, runs the selected fault and verifies cleanup.
Its [real-pool aggregate](../fault-lab/results/native_scenario_single_20261003T022707Z_02ec1fa5.json)
passes with 24 child checks and verified source/artifact hashes, explicitly
marking single-case completion rather than claiming all eight scenarios.
The [current native real-pool experiment](../fault-lab/results/native_scenario_db-pool-exhaustion_20261003T021520Z.json)
passes 24 checks: four actual PostgreSQL application-pool leases exhaust and
recover (503 → 200), live telemetry reaches the current Go/MCP/graph path, and
fresh-process PostgreSQL recovery preserves state/history. The current signal
window is checked, but the synthetic deployment catalog leaves remediation
advisory-only. No model, approval or execution occurs. This is native local
acceptance, not a new packaged runtime or Kubernetes/semantic/model acceptance.
The [current adversarial protocol-fixture run](../evals/results/workflow_benchmark_action_guard_20261003T020410Z_65c4b2b0.json)
blocks 90 invalid/unresolved cases and five explicit known-family mismatches,
and preserves ten positive controls. Unrecognized, negated or multi-family
causes now remain advisory-only rather than reaching restart/rollback approval.
This conservative prerequisite is **not general semantic validation** and can
withhold actions for legitimate paraphrases outside the bounded detector.
Earlier reports remain unchanged. No model or mutation runs in this benchmark.
The [current offline workflow run](../evals/results/workflow_benchmark_20261003T020411Z_d334eb08.json)
covers 150 canonical synthetic snapshots: 120 fault cases and 30 negatives,
with zero failures or execution attempts. This measures deterministic lab
classification/action policy, not model root-cause accuracy or faithfulness.
Reports are now run-specific and hash-bound rather than overwriting history.
The [offline semantic review protocol](../evals/SEMANTIC_REVIEW.md) now includes
two-reviewer comparison: exact disagreements, overlap coverage and nominal
agreement/kappa, without automatic adjudication or inferred correctness.
Actual independently calibrated expert review remains pending.
The [signal-to-action applicability gate](../docs/action-applicability.md) checks
the leading hypothesis's cited primary-service failure signals before dry run
and again at execution boundaries. Mismatched/conflicting signals stay advisory;
matching rules do not establish causal correctness or action efficacy.
Structured action samples must now be timestamped, finite, current within five
minutes and contiguous within two-minute metric gaps. Every execution boundary
rechecks retained sample age; delayed approval cannot override expired evidence.
Compiled-graph diagnosis now applies the current-window check to the primary
service before rule classification too. With no current samples it abstains
without a model call, retaining historical evidence; current unclassified
signals may still be investigated by the model. The Demo summary exception
requires an explicit local provider capability and cannot override structured
samples. This is not live refresh, semantic validation or retroactive
certification of historical diagnoses. Default stale MCP fixtures abstain.
Console typecheck/build and 86 Node tests pass; lint has no errors and three
existing generated-declaration warnings. Event observation tables retain UTC
times, evidence links and non-causal limitations. Component HTML rendering is
tested; local desktop/narrow browser checks cover event rendering, evidence
navigation and unavailable-query messaging. The approval area now shows scoped
signal checks, phrase-family status and evidence references, while explicitly
leaving semantic/causal/efficacy validation unresolved. Stale or legacy bindings
display unknown; this display cannot authorize execution. Local desktop/narrow
checks cover that review and command wrapping. The current offline reports are
hash-bound to the current backend revision. Live-cluster acceptance remains unverified.
The remote deployment catalog is explicitly synthetic, not live Kubernetes
history. Unverified rollback targets now produce advisory-only investigation;
execution rechecks provenance/scope even for restored legacy proposals/intents.
An opt-in [Kubernetes API revision reader](../docs/kubernetes-deployment-discovery.md)
now exists with owned-resource checks, no fixture fallback and a read-only RBAC
overlay. Optional UID-bound Kubernetes event observations now provide separate
first/latest timestamps and non-causal comparisons with sampled anomaly windows.
Available and denied event contexts survive a committed-writer SIGKILL and
fresh, tool-free PostgreSQL recovery with the approval interrupt retained.
Mock API/MCP/graph and rendering tests pass; actual cluster acceptance and
complete release chronology remain unverified. Simulation-only
demo/snapshot providers retain their explicit offline rollback path.
Opt-in real-pool Compose and isolated Kubernetes packaging configurations now
render and pass startup/contract tests. An earlier fresh image build failed on
Docker disk exhaustion. The new isolated service build/runtime acceptance above
supersedes that blocker only for the lab image; full-platform packaged runtime
acceptance remains unverified.
One new real PostgreSQL application-pool experiment passes 23 checks through
live telemetry, Go, MCP and the deterministic graph, including 4/4 leases,
503→200 recovery, approval pause and separate-process checkpoint restoration.
Deployment metadata remains fixture-backed; this is not a model RCA benchmark.
Delivery-degradation headers are exposed to configured CORS origins and documented
in OpenAPI; incident bodies and approval authorization are unchanged.
Redis unavailable at startup no longer blocks the API: a two-second cache probe
can degrade while keeping canonical incident persistence authoritative. Health
reports `event_cache_startup` as an initial snapshot, not live cache readiness;
the original Redis client can reconnect on later requests without a restart.
Malformed Redis configuration and database setup failures still fail startup.

A dated native PostgreSQL batch passes eight scenarios and 144 checks in one
fresh run, including exact log/span identity and separate-process state recovery.
A later single pool-fault experiment passes 46 checks covering authenticated
API crash, Redis pause/loss, exact full/cursor SSE replay and availability of
committed diagnoses during cache failure. Five proposals in the matrix remain
approval-paused; three diagnoses are advisory-only. No live approvals/actions run.

These are deterministic bounded-fault results, not current LLM accuracy,
Redis disk recovery, in-flight/execution crash or approval/resume proof. Individual
artifacts are revision-bound; the older matrix is not a fresh run of this worktree.
See the [dated results and remaining gaps](../docs/verification.md).

## What is implemented

- Engineering console with incident timeline, metric signals, dependency blast
  radius, cited evidence, workflow trace, and approve/reject interactions.
- Authenticated node-boundary diagnosis streaming, with durable progress,
  bounded heartbeat delivery, disconnect cancellation and explicit completion.
  Existing event GET supports checkpoint replay; neither stream reconnects nor
  read-only refreshes automatically resume a diagnosis or replay an approval.
- Bounded cross-service investigation separates the complaint service from
  candidate fault sources. Two-hop directed paths, local/symptom evidence and
  overlapping current windows are retained; remote candidates and unresolved
  dependency diagnostics remain advisory-only, never implicit action authority.
- A compiled LangGraph `StateGraph` with durable `thread_id` checkpoints,
  concurrent read-only telemetry queries, evidence grounding, a real
  `interrupt()` approval node, and `Command(resume=...)` recovery.
- An optional OpenAI Responses API reasoning adapter with strict structured
  output. Model hypotheses may cite only retained evidence IDs; provider,
  model, response ID, and token usage are written to the incident trace.
- PostgreSQL-backed LangGraph checkpoint history plus canonical incident,
  approval, idempotency, audit, and evaluation tables. Redis retains task state
  and ordered trace events for the SSE endpoint.
- Custom MCP Streamable HTTP tool server exposing eleven incident tools with
  generated JSON Schemas, bearer-token scopes, origin validation, timeouts, rate
  limits, signed human-approval receipts, idempotency, and JSON audit events.
- An orchestrator MCP client with initialization, typed structured-content
  parsing, timeouts, bounded exponential retries, and separate read/operator
  credentials. Metric responses must match the exact requested service/metric;
  quarantined out-of-scope evidence cannot support hypotheses or rollback targets.
- Go telemetry gateway wired behind the MCP server with allow-listed live
  Prometheus queries, bounded Loki and Tempo searches, strict JSON decoding,
  authentication, rate limiting, request tracing, timeouts, and deterministic
  offline fixtures.
- A repeatable four-service fault lab with eight bounded fault modes,
  Prometheus, Loki, Tempo, an OpenTelemetry Collector, a
  provisioned Grafana dashboard, continuous traffic, and a machine-verifiable
  injection script.
- Evidence-driven diagnosis profiles for database pool exhaustion, downstream
  timeout, CPU saturation, memory leak, deployment regression, Kafka consumer
  lag, retry storm, and cache penetration. Destructive restart/rollback
  proposals interrupt for approval; low-risk advice finishes as diagnosed.
- A Kustomize deployment base with hardened workloads, disruption protection,
  namespace network policy, and a Kubernetes remediation adapter. Production
  restart/rollback calls patch the Kubernetes API only after scope checks and a
  signed, action-bound human approval.
- A repeatable local Kind overlay and rollout verifier. The checked-in run
  brought PostgreSQL, Redis, the Go gateway, MCP, and orchestrator to Ready,
  proved remediation RBAC was namespace-bound, rejected a proposed mutation,
  and deleted the ephemeral cluster afterward.
- A deterministic BM25 incident-knowledge index over 18 checked-in synthetic
  runbooks, historical incidents, and technical documents. Results carry stable
  URIs, matched terms, update timestamps, and SHA-256 content fingerprints for
  reproducible citations. Its checked-in 150-case retrieval run measured 100%
  Recall@5 and 97.9% NDCG@5; this is a local retrieval metric, not an LLM
  quality claim.
- A credentialed three-variant model benchmark runner with an explicit API-cost
  confirmation gate, configurable current pricing, per-case response/usage
  traces, and revision-bound resume so completed calls are not purchased twice.
- Tests for process-restart recovery, workflow interruption, approval resume, prompt-injection
  rejection, signed approval binding, idempotent dry-run behavior, gateway
  authentication, query validation, MCP-to-Go normalization, Kubernetes RBAC
  boundaries, and API trace streaming.
- Post-execution rollout/SLI verification is distinct from action acceptance.
  A durable recovery interrupt can resume read-only observations without another
  restart/rollback. Simulation and missing/stale/contradictory evidence remain
  explicitly unverified; observed recovery is not causal efficacy proof.

## Architecture

Input screening checks an NFKC-normalized inspection view, removes hidden format
and non-whitespace control characters, and recognizes known English/Chinese
policy-bypass patterns. The original question remains unchanged. This is a
conservative heuristic with possible false positives (including quoted or
negated instructions), not a complete prompt-injection detector. Bearer/scoped
tool permissions and explicit human approvals remain the security boundary;
retrieved material stays untrusted even when a question passes screening.

```text
┌──────────────────────── React / vinext console ────────────────────────┐
│ Incident question · telemetry · evidence · trace · approval decision  │
└───────────────────────────────┬────────────────────────────────────────┘
                                │ HTTP / authenticated diagnosis SSE
┌───────────────────────────────▼────────────────────────────────────────┐
│ Python + LangGraph orchestrator                                        │
│ classify → select → correlate → retrieve → hypothesize → validate     │
│ → propose → INTERRUPT → execute/simulate → verify recovery            │
└──────────────┬──────────────────────────────┬──────────────────────────┘
               │ MCP tools                    │ checkpoints / audit
┌──────────────▼──────────────┐   ┌───────────▼─────────────────────────┐
│ Guarded MCP server          │   │ PostgreSQL / Redis                  │
│ read scopes · signed receipt│   │ incidents · trace · task state     │
└──────────────┬──────────────┘   └─────────────────────────────────────┘
               ├──────── authenticated bounded queries ────────────────┐
               │                                                       │
┌──────────────▼────────────────────────┐  ┌────────────────────────────▼┐
│ Go telemetry gateway                 │  │ Kubernetes API               │
│ Prometheus · Loki · Tempo · catalog  │  │ approved restart / rollback  │
└───────────────────────────────────────┘  └─────────────────────────────┘
```

The default lightweight stack uses deterministic telemetry fixtures behind the
real orchestrator → MCP → Go gateway network path. The full fault-lab override
switches the same gateway to live Prometheus, Loki, and Tempo backends without changing
the Agent or MCP interfaces.

## Run the console

Requires Node.js 22.13 or newer.

```bash
npm install
npm run dev
```

Open `http://localhost:3000`.

Set `NEXT_PUBLIC_ORCHESTRATOR_URL` to the backend URL only. The initial incident
is a labeled static demo, not a live diagnosis. For a protected development API,
trust the exact endpoint and enter Reader/Operator credentials in the console;
they are kept only in page memory. Never place a token in a public environment
variable. Explicit unauthenticated demo mode is HTTP-loopback-only and does not
activate automatically after a 401/403. Production SSO/proxy identity is still
unfinished; see [connection details](../docs/api-authorization.md).

## Run the services

The complete local stack is defined in Compose:

```bash
cp .env.example .env
docker compose up --build
```

Services:

- orchestrator: `http://localhost:8080`;
- Go telemetry gateway: `http://localhost:8081`;
- MCP server: `http://localhost:8082/mcp`;
- PostgreSQL and Redis remain internal to the Compose network.

Run the full observable microservice lab:

```bash
docker compose -f docker-compose.yml -f compose.fault-lab.yml up --build
python fault-lab/faultctl.py verify db-pool-exhaustion
python fault-lab/verify_agent_scenarios.py \
  --output fault-lab/results/agent-scenario-matrix.json
```

Grafana is available at `http://localhost:3001`. See
[`fault-lab/README.md`](../fault-lab/README.md) for the distinction between
telemetry verification and the full Agent workflow matrix.

To enable model-backed hypothesis generation, explicitly set both values:

```bash
OPENAI_API_KEY='<credential>'
OPENAI_MODEL='<chosen-model-id>'
```

No model is selected implicitly. If either value is absent, the runtime reports
`reasoning=deterministic` and makes no model request. The checked-in 2026-08-08
credentialed benchmark ran 150 cases per variant with `gpt-5.6-terra`: Top-1
root-cause accuracy was 19.3% for single prompt, 64.0% for RAG, and 81.3% for
the validated Agent; grounded-response rates were 0%, 99.3%, and 100%. See
[`evals/results/model_benchmark.json`](../evals/results/model_benchmark.json).

Before authorizing any paid calls, preview the exact request/checkpoint plan:

```bash
python evals/run_model_benchmark.py --plan --limit 150
```

The reviewed model choice, dated price inputs, smoke gate, and full-run approval
sequence are documented in
[`docs/model-evaluation-plan.md`](../docs/model-evaluation-plan.md).

Render the Kubernetes production base:

```bash
kubectl kustomize deploy/kubernetes/base
```

The example secret is intentionally excluded from Kustomize. Image publishing,
secret creation, observability endpoints, and environment overlays must be
configured before applying it to a cluster; see
[`deploy/kubernetes/README.md`](../deploy/kubernetes/README.md).

Python development uses Python 3.11 or newer:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e 'services/orchestrator[dev]' -e 'services/mcp-server[dev]'
```

## Validate

```bash
npm test
# Requires the backend .venv/dependencies; no model, DB or MCP configuration is inherited.
npm run test:stream-contract
(cd services/telemetry-gateway && go test ./...)
.venv/bin/pytest services/orchestrator/tests services/mcp-server/tests \
  evals/tests fault-lab/app/tests -q -p no:cacheprovider
python evals/generate_dataset.py
python evals/run_benchmark.py
python evals/run_retrieval_benchmark.py
```

The regression suite runs all eight incident families through the workflow and
the real MCP HTTP schemas with deterministic telemetry fixtures. The separate
fault-lab verifier above uses generic questions without fault labels against
live Prometheus, Loki, and Tempo. The fixed 2026-08-08 run passed all eight
scenarios, retained metric/log/trace evidence plus tool-call traces, rejected
all five destructive proposals before execution, and completed the three
advisory incidents as `diagnosed`.

## Security model

Read tools require least-privilege telemetry or runbook scopes. `restart_deployment`
and `rollback_release` default to dry-run. Execution additionally requires:

1. `production:execute` scope;
2. a signed, short-lived receipt bound to incident, actor, tool, service, and
   target;
3. a unique idempotency key.

The MCP server implements the `2025-06-18` JSON-RPC protocol revision and does
not support JSON-RPC batching. The local static-token authenticator is an
explicit development adapter; production deployment should validate
audience-bound OAuth 2.1 access tokens.

## Repository map

```text
app/                         incident console
services/orchestrator/       Python workflow and API
services/mcp-server/         custom guarded MCP tool server
services/telemetry-gateway/  Go telemetry/query gateway
fault-lab/                   instrumented microservices and observability stack
evals/                       generated fault benchmark and evaluator
deploy/kubernetes/           hardened Kustomize base and deployment guide
docs/                        architecture and delivery notes
```

## Verified persistence behavior

The historical local integration test intentionally stopped the orchestrator at the
approval interrupt, restarted it with a new process, loaded the same incident
from PostgreSQL, and resumed the graph with a signed approval. The run finished
with `status=resolved`, 12 LangGraph checkpoints, one durable approval record,
and 21 ordered Redis trace events. That old `resolved` label established only a
tool acknowledgement, not recovery. Current code requires the separate rollout/
SLI gate; real PostgreSQL restoration is now additionally tested across the
recovery interrupt with a mocked Kubernetes backend. See [verification notes](../docs/verification.md).

For a demo script, measured evidence table, honest limitations, interview
talking points, and resume-ready bullets, see the
[portfolio brief](../docs/portfolio.md).

## Remaining roadmap

- Complete production console identity/SSO, role mapping, incident navigation
  and ongoing post-diagnosis event streaming; local development token entry is
  not human identity. Diagnosis streaming does not yet stream individual tokens
  or resume an interrupted read workflow automatically.
- Complete current-image cross-service diagnosis and the eight-case fault
  matrix; rebuild/reaccept when source changes. Historical reports cannot certify
  changed source, and Kafka/cache/CPU fault fidelity is not uniform.
- Validate an explicitly human-approved live operation and post-operation
  recovery without claiming that correlation alone proves causal efficacy.
- Scope telemetry by namespace/environment before supporting multiple tenants.
- Add OpenTelemetry spans across model, checkpoint, and approval events (the
  fault-lab services already export OTLP traces).
- Run the 150-case benchmark against single-prompt, RAG, and validated-agent
  model configurations; publish measured latency, quality, and token cost only.
- Build and publish versioned images, supply managed secrets and a production
  environment overlay, then promote the Kind-verified base to a managed cluster.
