# Persistence and recovery verification

## 2026-10-08 durable diagnosis stream and console contract

The current joint orchestrator/MCP/evaluation/Kubernetes-verifier regression
passes 1680 tests without skips, including all 24 dedicated-PostgreSQL opt-ins.
The 31 stream tests cover retained-before-delivery snapshots, real ASGI
disconnect/cancellation, redacted failures, explicit completion, and an approval
proposal that exists before its actual human interrupt. Four actual PG stream
tests verify per-frame saver/canonical equality, new-connection prefix restoration,
persistence failure without completion, and early decisions with zero SQL claims.
These scopes are separate from live cluster and model acceptance.

The frontend passes 209 pure tests plus typecheck/build. An additional explicit
cross-language contract test can be run from the repository root after installing
the backend virtualenv/dependencies:

```bash
npm run test:stream-contract
```

It starts the actual API/compiled LangGraph in an isolated TestClient process,
checks the real approval interrupt and exact retained GET, then validates the
actual emitted Pydantic SSE in the frontend. Fragmented body decoding preserves
the backend's escaped Chinese JSON; missing complete retains an unconfirmed
prefix without another POST. The fetch seam does not open a TCP socket; the
separate pure-frontend tests cover split multibyte UTF-8. The child uses an explicit
environment allowlist, fixture telemetry and memory stores; absent backend setup
fails rather than being skipped. No `.env.local`, model, DB, Redis, MCP or telemetry
configuration is inherited. This is wire-contract/guardrail verification, not RCA
accuracy, production recovery or model-quality evidence.

The separate actual browser/HTTP run `INC-4E2E91` observes provisional eight-event
progress, then completion at 46 events. An actor mismatch cannot submit a decision;
a matching operator rejects the simulation. Read-only API inspection verifies
one reject decision, 50 events, 20 evidence objects and no execute-step event.
The [cropped rejected-result view](screenshots/diagnosis-stream-rejected-20261008-inc4e2e91.jpg)
retains the observed UI result; it is not complete viewport/layout coverage.
Credentials/trust were cleared and owned API/frontend listeners and tab closed.
This browser backend was deterministic direct fixture/memory mode, not live
telemetry or Kubernetes. See the [completion audit](completion-audit.md) for the
current/historical live acceptance boundary and remaining original scope.

## 2026-10-03 iteration status

Opt-in Kubernetes revision discovery is now implemented independently of
remediation enablement. Namespace is validated and propagated from incident
environment through MCP. The reader makes GET-only API requests using existing
TLS/service-account setup, verifies exact owned Deployment/ReplicaSet identity,
rejects ambiguity/incomplete history/read-time resource changes, bounds each
response to 512 KiB, one list to 200 items and each query to six seconds. Enabled
reader failures never silently substitute synthetic deployments. Output omits
raw Pod templates/environment. The native verifier disables the new flag so
inherited environment cannot accidentally reach Kubernetes.

Returned revision data is a current observation with nearest lower retained
owned target, not a chronological deployment event log or proof of causation.
ReplicaSet creation time is separate from the explicitly marked query timestamp;
garbage-collected/reused revision history must not be treated as release timing.
The execution backend also rejects incomplete/ambiguous/unowned rollback targets.
Human approval and durable intent remain mandatory; an observed target alone
does not prove rollback is the right mitigation. The independent read-only
overlay selects a separate ServiceAccount with only get Deployment/list ReplicaSet
permissions, disables real remediation, and renders in the correct namespace.

Seventeen new reader/transport/executor tests plus two deployment-contract tests
cover the HTTP contract through mock Kubernetes, actual MCP ASGI transport and
compiled graph, including checkpoint readback and pause before execution. Existing
response-loss operation-inspection fixtures now include exact owner UID/namespace;
their single-PATCH/read-only recovery invariants still pass. **622 Python tests**
pass with isolated PG and no skips, one existing Starlette/httpx warning. Ruff
and whitespace checks pass. No real cluster query/mutation, model call or new
container build occurred. New-image/cluster runtime, actual release chronology
and semantic RCA remain unverified; Docker capacity is still constrained. PG
was stopped with data retained. See
[discovery contract and limitations](kubernetes-deployment-discovery.md).

A source/rollback safety audit found that the Go deployment endpoint still
returned its fixed fixture catalog, but MCP labeled it Kubernetes. This did not
establish that release 142 caused the actual pool fault, or that 141 was a live
rollback target. Go now labels source/origin synthetic; MCP labels both direct
and gateway-backed fixture catalogs synthetic (even with real metrics/logs/
traces). The client preserves top-level origin in evidence, defaulting absent
origin to unknown; a nested deployment cannot override that boundary.

Remote workflow proposals require an observed `kubernetes_api` deployment in
the incident namespace, latest primary-service metadata, valid distinct prior
revision and no same-time contradiction. A newer unknown/synthetic record must
not be skipped to select an older trusted record. Without this, the diagnosis
and hypotheses remain, but the recommendation is read-only verification of
live deployment history, with `rollback_target_unverified` trace and no dry-run,
approval interrupt or mutation call. Simulation-only Demo/Snapshot providers
explicitly retain offline fixture targets; no model/tool-output flag can enable
that provider capability.

Execution revalidates the target against retained evidence and exact incident
service/namespace/revision before recording a new durable execution intent and
again before receipt issuance/dispatch. Existing intent-binding checks remain
mandatory. An old paused proposal restored onto a remote-provider contract
cannot execute merely because a human approves; the recorded decision remains,
but no intent/tool call is produced. An already prepared simulation intent also
cannot dispatch under an unverified remote contract. These two are in-memory
checkpoint/direct-node tests, not new separate-process PostgreSQL upgrade/crash
acceptance. Read-only inspection of old possibly-dispatched operations remains
available independently; a guard rejection does not erase outcome uncertainty.

Tests exercise real MCP ASGI transport/audit, source propagation, unknown/
synthetic/wrong-namespace/bad-revision refusals, evidence/checkpoint retention,
no dry-run/no interrupts, freshest-record conflicts, and legacy intent gates.
The existing signed MCP rollback integration test now **explicitly supplies a
mock observed-deployment contract**, while unchanged real MCP fixture transport
is tested to produce advisory-only output. This is not actual Kubernetes
discovery. Go has a provenance HTTP contract test; race tests pass. Full isolated
PG regression passes **603 Python tests**, no skips, one existing Starlette/httpx
warning; Ruff/whitespace checks pass. No paid model or production action occurred.

The native verifier is updated to require explicit advisory/no-rollback behavior
when the live MCP deployment catalog is unverified. No fresh native matrix was
run after this safety change. Prior reports' approval-paused rollback proposals
are revision-bound and do not prove the new behavior. Live deployment-history
retrieval, causal deployment validation and current semantic model evaluation
remain incomplete; observed target provenance alone is not semantic causation.
Docker runtime acceptance is still capacity-blocked; user Docker data was not
cleaned. Isolated PG was stopped with data retained after regression.

Real-pool packaging now has an explicit Compose overlay and a separate isolated
Kubernetes experiment. Both use a dedicated ephemeral PostgreSQL instance with
IP listeners disabled and only a shared Unix socket, rather than relaxing the
application's local-target guard or using the canonical incident database.
Only the Compose payment-service is changed to real mode; actual merged config
preserves downstream/telemetry settings and prior dependencies. The app runs as
uid 10001/group 70 with a read-only socket mount; PostgreSQL socket permissions
are 0770. Required token/password have no bundled defaults. Kubernetes uses
separate namespace, Secret references, two non-root containers, no DB Service,
no API service-account token and bounded ephemeral volumes. It is an isolated
packaging/resource experiment, not the full Kubernetes telemetry/Agent stack.

The new opt-in bootstrap validates target/token before connection, runs fixed
read-only SELECT 1 readiness probes and closes each connection before execing
uvicorn without a shell. Operational failures retry for a 60-second loop budget;
the final bounded connect/query may overrun it, so this is not a strict whole-
startup deadline. Permanent failures fail closed without provider/credential
text. Tests cover auth/scope refusal, bounded retries, error redaction, no retries
of programming errors, simulation bypass, budget limits, actual local-PG probe
closure, static packaging contracts and actual Compose/Kustomize rendering.
External rendering CLIs may skip only the render checks if absent; static
contracts still run. This machine ran **589 Python tests, zero skips**, isolated
PG enabled, with one existing Starlette/httpx warning. Ruff/whitespace checks pass.

Actual new image construction was attempted with a fresh tag and failed during
Python base-layer registration: **no space left on device**. No image/runtime/
cluster success is claimed, and no existing Docker image, container, cache or
volume was deleted. Packaged socket access, non-root startup and full cluster
integration still require runtime verification after Docker capacity is made
available. PG was stopped with data retained; no model calls or actions occurred.
The previous native real-pool report is not retroactively a packaged acceptance
or a fresh run of this expanded source revision.

The real application-pool scenario now passes its full native telemetry path:
[retained 23/23-check report](../fault-lab/results/native_scenario_db-pool-exhaustion_20261002T232101Z.json),
90.130 seconds including startup, telemetry visibility, warmup, fault traffic,
diagnosis, fresh-process recovery and explicit clear (not model latency).
Four exact PostgreSQL backend PIDs are verified in `pg_stat_activity`; owned
payment-service leases change 0/4→4/4, direct work returns 503, and authenticated
clear restores 0/4 and HTTP 200 using the original sessions. Prometheus/Loki/
Tempo→Go→MCP→deterministic LangGraph retains actual pool-utilization=1 and
active=max=4 timeout events, classifies pool exhaustion and stays approval-paused.
Checkpoint/repository/history/interrupt recovery passes in a fresh process.
Source and gateway hashes are retained. Deployment/catalog/runbook context stays
fixture-backed; this does not prove a deployment caused the real fault, model
semantic accuracy, server-wide connection exhaustion or other real fault modes.

The first new experiment correctly diagnosed the real fault but failed overall
because the verifier's exhausted snapshot ran before injection; its
[failed artifact](../fault-lab/results/native_scenario_db-pool-exhaustion_20261002T231835Z.json)
is preserved. Sampling was moved after acknowledged injection before the fresh
successful run; no check was relaxed. Default and historical matrices remain
simulated. Full regression now passes **574 Python tests**, isolated PG enabled,
no skips; Go race tests, Ruff and whitespace checks pass. Only an existing
Starlette/httpx deprecation warning remains. Owned temporary services/telemetry
containers/network were cleaned, and PG stopped with data retained. Zero model
calls or remediation executions. New container/Kubernetes builds remain untested.

The fault lab now has an explicitly opt-in local PostgreSQL-backed application
pool experiment. Default simulation remains unchanged; native verifiers normally
force it and remove the new database URL rather than accidentally activating
real resources through inherited environment. The real mode requires a dedicated
explicit local target and non-default injection token, rejects remote/implicit/
multi-host/service/hostaddr overrides, opens at most four sessions, and performs
fixed `SELECT 1` work without schema/row/server-setting changes. Fault injection
reserves every lease; the existing token-protected clear or bounded timer returns
leases. Gauges derive from actual queue/lease counts, not a fabricated 50/50.

Real isolated-PG tests verify four distinct backend PIDs and idle held sessions
in `pg_stat_activity`, actual acquisition timeout, clear/expiry recovery, HTTP
503→200, authorization and gauge values, and connection closure. Offline tests
cover invalid targets, libpq environment redirect refusal, missing/default token
before connection, partial startup cleanup and partial exhaustion cancellation/
timeout. This is **application pool** exhaustion, not PostgreSQL server-wide
`max_connections` depletion. At that iteration, new Docker/Kubernetes packaging
and complete Agent telemetry-path acceptance were untested for this mode; no real-model RCA or
accuracy claim is made. Other fault scenarios remain bounded simulations.
Full regression passes **564 Python tests**, with isolated PostgreSQL and no
skips; one existing Starlette/httpx warning remains. Ruff and whitespace checks
pass. All experimental sessions close, owned Redis test resources are cleaned,
and the isolated PostgreSQL was stopped with data retained. No model requests
or production actions occurred. The new Psycopg dependency is declared in the
lab package; its new container build has not been performed.

Input policy screening now inspects a separate NFKC-normalized view, strips
Unicode format/non-whitespace control characters, and collapses inter-Han
whitespace before known English/Chinese bypass patterns. It also recognizes
the common `ignore previous instructions` form without requiring an extra
determiner. Original questions remain unchanged; neither credentials/actors nor
authorization are normalized or altered. Screening remains conservative and
may reject quoted/negated instruction text; it is not a comprehensive semantic
prompt-injection classifier. Scoped auth, schema validation and human approval
remain mandatory independently of these heuristics.

Thirty-five new tests cover 15 bypass fixtures (ordinary/full-width/zero-width/
control-obfuscated and Chinese), five legitimate diagnostic prompts, and API
rejection of every known bypass before model, telemetry, remediation, repository
save or cache publication. These are fixed offline policy cases, not a measured
real-model injection rejection rate. Full regression passes **546 Python tests**,
isolated PostgreSQL enabled, no skips; one existing Starlette/httpx warning.
Ruff/whitespace checks pass. Owned test Redis resources were cleaned; PG was
stopped with data retained. No paid model requests or production actions occurred.
The previously requested authorization for a new three-case model run has not
been received; automatic goal continuation is not additional spending permission.

The graph-model batch runner now supports conservative continuation from a
finalized summary: planned case prefix, dataset/fixture/source/corpus fingerprints,
prompt contract and configured model/provider identity must match. Its planning
mode remains credential-free and reports remaining maximum requests. Execution
requires a fresh explicit cost flag; no paid continuation was run. Previously
attempted successful **and failed** cases are retained and skipped, and a failed
request's unknown usage is not converted to zero. New output preserves parent
lineage, copies retained records, and separates cumulative/this-invocation calls;
original files are untouched.

A canonical-parent exclusive reservation admits one child continuation only.
It remains after failure/crash so a missing final summary is not mistaken for
permission to repeat ambiguous requests. This is finalized-batch continuation,
not in-flight SIGKILL recovery or provider-level exactly-once generation. No
automatic recovery is attempted when a final summary is absent. Offline tests
cover zero-request completed continuation, failed-case skipping, preserved
records, code/model/provider/data drift rejection, tampered artifacts and duplicate
reservations. All 122 evaluation tests pass; no paid calls/actions occurred.
Semantic scores and real-current-model batch results remain pending.

Redis startup is now an optional two-second cooperative probe rather than an API
startup dependency. Redis error or probe timeout retains the configured client
and backend mode, allowing canonical checkpoint/repository queries and the
existing bounded delivery/SSE fallback. It never silently swaps to memory cache.
`/health.event_cache_startup` is explicitly the initial probe snapshot (`ready`,
`unavailable`, `timeout`, `not_configured`), not current cache health or a database
availability probe. Invalid Redis configuration and database setup errors still
propagate. Startup/shutdown resources are owned by AsyncExitStack, including
cleanup when setup is cancelled or configuration raises before readiness.

Seven new tests cover ready/error/stalled probe states, cancellation cleanup,
invalid-configuration failure and two real-client cases. With an absent Redis
Unix socket and isolated PostgreSQL, an API lifecycle can create/save a paused
incident, restart into the same unavailable cache, and read exactly matching
checkpoint/repository state plus all trace events via read-only PG fallback.
Separately, an owned Unix-only Redis starts after the API's initial failure;
the original client reconnects and new incidents regain confirmed event-store
delivery without API restart. This latter reconnect test uses memory checkpoint
storage and synthetic telemetry; the PostgreSQL persistence case is separate.
Neither approves or executes a proposal. These are ASGI lifecycle/HTTP tests,
not a new externally hosted API SIGKILL/live-telemetry matrix or browser demo.

Full regression passes **504 Python tests**, isolated PostgreSQL and local
Redis binary enabled, no skips. One existing Starlette/httpx warning remains;
Ruff and whitespace checks pass. Temporary Redis processes/socket directories
were cleaned, and the isolated database was stopped with data retained. No new
paid model request or production action occurred. Existing generation-in-flight,
current semantic RCA/real-model and full-latest-stack completion gaps remain.

Model-abstention persistence is now verified across actual process termination:
a dedicated writer runs the real Responses adapter against a local HTTP fixture
and current LangGraph, commits its `insufficient_evidence` checkpoint plus
canonical repository record, then is killed with SIGKILL. A separately spawned
reader uses fresh PostgreSQL connections and the guarded checkpoint inspector
to compare the complete restored state against the writer's record, verify
history and confirm no pending approval interrupt. The writer forbids even
mutation dry-runs; the reader has no model generator and raises on any tool
access. Credentials/production endpoints are stripped from child environments;
the dedicated local database URL is validated before any writer starts.

The five PostgreSQL workflow-recovery tests pass. Full regression passes
**497 Python tests** with isolated PostgreSQL and no skips (one pre-existing
Starlette/httpx deprecation warning). Ruff and whitespace checks pass. The test
database was stopped with data retained; child processes exited. No new paid
model request or production action occurred. This proves recovery of a fully
committed abstention, not a crash during generation, a machine/storage failure,
or real-model abstention accuracy. Original source-bound paid reports remain
historical and semantic review/calibration remains pending.

Reproduce the scoped process check with an explicit dedicated local test URL:

```bash
INCIDENT_TEST_DATABASE_URL='<dedicated local test PostgreSQL URL>' \
  .venv/bin/pytest services/orchestrator/tests/test_postgres_workflow_recovery.py \
  -k model_abstention_survives_sigkill -q -p no:cacheprovider
```

The latest offline semantic-review tooling prepares worksheets from actual
graph-model case artifacts without exposing expected/classifier labels, model
identity or confidence in review context. Its scorer binds every annotation to
the full retained record fingerprint, validates original hypothesis order and
every supporting citation, and rejects tampered/duplicate/incomplete reviews.
It reports reviewed-subset cause-label Top-1/Top-3, supporting-citation support,
uncertainty, denominators and coverage. Pending annotations leave metrics null;
empty hypotheses never earn vacuous citation faithfulness. Failed source cases
remain visible in overall coverage. Original model reports are not overwritten.

Offline unit/actual-graph HTTP-fixture/CLI tests verify this harness only. No
expert annotations or paid current-model batch scores have been produced.
Reviewer identity is declared rather than authenticated, conflict handling and
whole-response faithfulness are not scored, and no inter-reviewer calibration
has been established. Post-validation hypotheses are scored; discarded outputs
count as workflow abstention rather than explicit model abstention. The protocol
is documented in `evals/SEMANTIC_REVIEW.md`; full goal completion remains unproven.
All **115 evaluation tests** pass; Ruff/whitespace checks pass. This iteration
ran the scoped evaluation suite, not a new full PG/live-stack regression.
No model requests or production actions occurred.

The latest model contract fixes forced-hypothesis generation: schema now accepts
an explicitly empty hypothesis array, with prompt revision
`evidence-contract-v3-abstention`. The prompt instructs abstention on healthy,
missing or insufficient observations and treats rule-derived incident type as
a hint, not proof. LangGraph distinguishes model abstention from citation
rejection, preserves its evidence-collection recommendation, and enters
`insufficient_evidence` without a mutation proposal. Existing citation checks
remain mandatory for every nonempty hypothesis.

Real Responses-client parsing against offline HTTP fixtures is exercised through
the actual graph for one fault, three healthy variants and three missing-data
variants. Missing-data cases are confirmed to make zero requests via the
existing pre-generation gate; model abstentions retain trace metadata and memory
checkpoints without actions. Incomplete/refused/missing outputs remain errors.
Full regression passes **478 Python tests**, isolated PostgreSQL enabled, no
skips; one existing Starlette/httpx deprecation warning remains. Ruff and
whitespace checks pass. The isolated database was stopped with data retained.
No new paid model call or live fault probe occurred. Reliable real-model
abstention, semantic RCA scoring and recovery of this new abstention state across
a PG process restart remain unproven.

This design follows the official guidance to specify empty outputs when inputs
cannot support the schema, rather than encourage hallucinated values:
[Structured Outputs — handling user-generated input](https://developers.openai.com/api/docs/guides/structured-outputs#handling-user-generated-input).

The new `evals/run_graph_model_benchmark.py` injects a Responses hypothesis
generator into the actual compiled snapshot workflow, rather than measuring
structured prompts outside LangGraph. Its credential-free 150-case plan was
executed: eight fault families plus healthy/missing-data groups, 15 variants
each, round-robin order and at most 150 requests with zero retries. Execution
requires an explicit cost flag; no paid batch has been run. The workflow adapter
now retains both pre-rejection and final state. Offline injection tests cover
model invocation, approval rejection, checkpoints, redacted failure persistence,
fail-fast spending, source provenance, budget bounds and unknown token usage.
All **87 evaluation tests** pass, along with Ruff/whitespace checks.
No new model request or production action occurred this iteration.

New artifacts separate rule-derived classification from model free-text RCA;
semantic accuracy/faithfulness and cost remain null rather than fabricated.
Successful snapshot latency includes rejection/checkpoint reads. This runner
uses synthetic adapters, BM25 and memory checkpoints, not live MCP or PG;
it has no checkpoint resume yet. Actual model behavior on healthy/missing-data
cases and semantic human calibration remain unproven. A failing request can
still be billable even when its usage is unavailable. Per-case JSONL is flushed
and synced, and a failed case stops further requests rather than retrying.

The latest diagnosis-smoke revision independently blocks non-dry-run tools,
counts execution attempts, validates checkpoint roundtrip and approval pause,
and fingerprints its runner plus orchestrator sources. Its safety result is
explicitly not semantic grounding or RCA accuracy. Tests exercise the actual
LangGraph with a local hypothesis generator and unsupported-citation rejection;
no credentials or new model calls are used in this revision's tests. Historical
smoke reports remain unchanged: the separately user-authorized preceding
`diagnosis_smoke_20261002T220247Z.json` took 6.354 seconds and ended at
`waiting_approval` with 20 evidence items and 42 trace events, but predates
the new runtime checks and source hashes. It is not verification of this revision.
All 74 evaluation tests pass; Ruff and whitespace checks pass. This is a scoped
evaluation regression run, not a fresh full PostgreSQL/live-stack regression.

The latest response-contract revision exposes only the two sanitized event
delivery headers to configured CORS origins and documents their enum/optional
semantics in OpenAPI for incident creation and approval responses. Allowed-origin
ASGI HTTP tests verify degraded-delivery headers are explicitly exposed;
unapproved-origin tests verify no CORS read permission is granted. CORS is not
authentication and these tests do not assert server-side POST rejection or a
browser visual workflow. Existing bearer-token/actor/scoped approval checks are
unchanged. No token/provider detail headers are exposed.

Three new tests cover allowed-origin degradation metadata, unapproved-origin
CORS behavior and response-header schemas while retaining JSON incident bodies.
API/auth checks pass (38 tests); full regression passes **445 Python tests**
with isolated PostgreSQL and no skips. Ruff and whitespace checks pass.
PostgreSQL was stopped with data retained. No model requests, new live fault
probe or production actions occurred this iteration. Earlier 46-check runtime
results remain dated evidence; there is no new eight-case/full-browser/model
evaluation claim. Broader completion remains unproven.

The latest API change separates committed incident results from optional event
delivery. Only after workflow checkpoint and canonical repository save succeed,
the API attempts one event-store publish with a two-second cooperative timeout.
Redis error/timeout returns the saved result with
`X-Incident-Event-Delivery: checkpoint-fallback` and a sanitized
`cache-unavailable` / `cache-timeout` reason; normal delivery reports
`event-store`. There is no publish/diagnosis/execution retry. A lost Redis
acknowledgement does not establish whether the cache write committed, so no
rollback is claimed. PostgreSQL persistence errors are not swallowed or labeled
as cache degradation. Approval results use the same post-persistence delivery
helper without changing authorization, decision claims or execution behavior.

A single pool-fault experiment covers the prior API crash, Redis stall/resume,
loss and exact SSE replay, plus two new diagnoses created during the cache
failures. All **46/46 checks** pass in 97.604 seconds. The paused-Redis diagnosis
returns HTTP 201 in 2.147 seconds with `cache-timeout`; the Redis-loss diagnosis
returns HTTP 201 in 0.106 seconds with `cache-unavailable`. Both remain
`waiting_approval`, retain 42 trace events, and independently match both the
PostgreSQL incident record and checkpoint. Their complete states and delivery
metadata are retained in the artifact, not treated as three different fault
families. Result:
[`native_scenario_db-pool-exhaustion_20261002T215325Z.json`](../fault-lab/results/native_scenario_db-pool-exhaustion_20261002T215325Z.json).

Four API tests cover committed diagnosis availability after Redis error,
publish-timeout cancellation without replay, persistence failure remaining an
error with no cache publish, and committed rejection despite delivery failure.
The diagnostic test retains legitimate pre-approval dry-run validation; it
asserts no non-dry-run action, rather than incorrectly forbidding all tool calls.
A verifier prerequisite test requires both owned failure modes. Current full
regression: 442 Python tests pass with isolated PostgreSQL, no skips; Ruff and
whitespace checks pass. No live model, approval submission or production action
occurs. Owned API/Redis/workload/observation resources cleaned; process/network
checks are empty, PostgreSQL stopped and test incident/trace data retained.

The production API has changed; earlier matrix hashes are dated evidence. This
proves response availability through event-cache failure for an already-running
API and committed diagnoses, not startup with unavailable Redis, crash during
the commit/publish gap, live approval/resume/execution, a total request/DB bound,
Redis disk recovery or current-model semantic quality. Overall completion remains
unproven; no historical failed results were relabeled.

The latest SSE revision requests cancellation of the event-cache read after two
seconds using `asyncio.wait_for`; a timeout replays the exact remaining checkpoint
trace with explicit `task_store_timeout` / read-only provenance. This is a cache
I/O budget, not a hard total HTTP or PostgreSQL deadline; cooperative cancellation
is required. A unit test hangs the cache coroutine, verifies it is cancelled,
then compares the complete checkpoint replay and confirms no remediation call.
The owned-Redis stall gate has a separate prerequisite test. Current complete
regression: 437 Python tests pass with isolated PostgreSQL, no skips; Ruff and
whitespace checks pass.

A combined single pool probe covers real API crash, Redis-surviving task/SSE
recovery, owned Redis SIGSTOP, SIGCONT recovery and subsequent Redis SIGKILL.
All **36/36 checks** pass in 93.939 seconds (0.126 seconds inside diagnosis).
While the Redis process remains alive but paused, full checkpoint replay returns
42 exact events in 2.012 seconds; cursor 22 returns 20 events in 2.011 seconds,
with `task_store_timeout` provenance. Both measured reads satisfy the probe's
1.5–6-second bounds. After SIGCONT, normal Redis delivery returns identical
events/state without fallback provenance. After SIGKILL, checkpoint fallback
still returns exact full/cursor events with unavailable-store provenance.
Result:
[`native_scenario_db-pool-exhaustion_20261002T214702Z.json`](../fault-lab/results/native_scenario_db-pool-exhaustion_20261002T214702Z.json).

The incident stays `waiting_approval`; no model, approval submission or production
action occurs. Signals target only Redis/API child processes created by this
probe, and paused Redis is resumed in a `finally` block. Process inspection
confirms no owned Redis remains; final Docker process/network checks are empty.
Temporary Redis/socket and observation stores are removed, while PostgreSQL
incident/trace data and result artifacts remain; PostgreSQL is stopped.

This replaces the prior untested hung-cache-read gap with actual paused-Redis
I/O evidence, not Redis disk/data restoration, all possible TCP partition
behavior, uncooperative coroutine cancellation, a total database/request bound,
in-flight diagnosis/execution crash recovery, approval/resume, or current-model
semantic evaluation. The broader current-worktree matrix remains separate from
this single deterministic bounded-fault scenario and prior dated batches.

The latest production SSE change treats the task/event store as a delivery
cache, not the authority for persisted trace identity. Each read compares its
events with the exact remaining checkpoint trace. Redis read errors, invalid
event decoding and missing/partial/divergent cache content trigger read-only
checkpoint replay with an explicit `replay_source` frame (checkpoint backend,
reason and `read_only: true`). Cursor advancement uses retained event sequences;
the cache is not repaired and no workflow is resumed or approved. Raw provider
errors are not exposed. If the checkpoint cannot be read, this change does not
substitute unverified cached data as a diagnosis.

An actual pool-fault probe creates through the authenticated API, kills/restarts
that API, confirms normal Redis task/SSE retention, then SIGKILLs only its owned
Redis. All **31/31 checks** pass in 90.994 seconds (0.127 seconds inside diagnosis):
the API still replays all 42 exact trace events and the 20 events after cursor
22 from PostgreSQL, ends in `waiting_approval`, and explicitly reports
`workflow_checkpoint` / `postgres` / `task_store_unavailable` / read-only source.
Both killed processes retain exit code `-9`. Result:
[`native_scenario_db-pool-exhaustion_20261002T214223Z.json`](../fault-lab/results/native_scenario_db-pool-exhaustion_20261002T214223Z.json).

Three production API tests cover Redis errors with redaction/no execution,
empty/partial/reordered cache cursor replay, and invalid cached encoding. Two
verifier tests require owned Redis mode before any process kill and retain replay
provenance separately from trace events. Current complete regression: 435 Python
tests pass with isolated PostgreSQL, no skips; Ruff and whitespace checks pass.
No model, approval or production action calls occurred. Read-only process checks
confirm no owned Redis remains; Docker process/network checks are empty and
PostgreSQL is stopped with its incident/trace data retained. Only owned temporary
Redis data/socket and observation stores were removed.

This proves checkpoint SSE fallback after real Redis process loss for one paused
bounded-fault incident. It does not restore Redis data, prove Redis disk/restart
durability, bound a hung network read, cover in-flight diagnosis/execution or
approval/resume, or measure current-model semantic quality. The production API
has changed, so the prior full eight-case batch is dated-revision evidence, not
a fresh matrix for this worktree. Overall completion remains unproven.

The latest pool probe adds an owned Unix-only Redis instance shared by the
original and restarted authenticated API. After committed diagnosis and SIGKILL
of the original API, the new API reports Redis task-state mode. All **27/27
checks** pass in 90.179 seconds (0.102 seconds inside HTTP diagnosis): Redis task
metadata matches its pre-crash value and the expected incident; authenticated
SSE replay returns all 42 exact trace events; `after=22` returns precisely the
remaining 20 events; both streams end with the retained approval-paused state.
Event TTL at inspection was 86,399 seconds. Complete PostgreSQL recovery still
retains 25 evidence items and eight checkpoints. Result:
[`native_scenario_db-pool-exhaustion_20261002T213636Z.json`](../fault-lab/results/native_scenario_db-pool-exhaustion_20261002T213636Z.json).

Redis is created from a local binary with TCP, snapshots and append-only mode
disabled, using a mode-700 socket inside a fresh private temporary directory.
No external Redis URL or user Redis data is accessed, flushed or deleted.
Read-only process inspection confirms no owned Redis remains; owned API/workload/
observability resources cleaned and final Docker process/network checks were
empty. Temporary Redis state/socket directory is removed, while PostgreSQL
incident/trace data and the result artifact are retained. PostgreSQL was stopped.
No model requests, approval submissions or production actions occurred.

Nine new verifier tests cover prerequisite gates, stream size/ordering/identity
checks, heartbeat/state parsing and authenticated read-only cursor selection.
Current complete regression: 430 Python tests pass with isolated PostgreSQL,
no skips; Ruff and whitespace checks pass. The Redis/SSE proof covers API crash
while Redis remains running, not Redis-process restart, disk durability, TTL
expiry behavior, diagnosis-in-flight crash, execution crash or approval/resume.
This remains a single deterministic bounded-fault scenario with fixture knowledge.
The full earlier eight-case batch has not been rerun through Redis/API crash
mode and its verifier hashes describe its dated revision. Current-model semantic
evaluation and broader production failure recovery remain unproven.

The latest single pool-fault probe creates its diagnosis through a real
authenticated orchestrator HTTP API with PostgreSQL storage and live MCP
telemetry. After the API returns the committed approval-paused diagnosis, the
verifier sends SIGKILL only to its owned API child (retained exit code `-9`),
starts a distinct API process, then reads complete state and checkpoint history
over authenticated HTTP. The verifier does not manually save/repair the API's
incident record. An independent tool-disabled process also confirms checkpoint,
repository, history and `human_approval` interrupt retention. All **23/23 checks**
passed in 90.028 seconds (0.161 seconds inside HTTP diagnosis). Result:
[`native_scenario_db-pool-exhaustion_20261002T213201Z.json`](../fault-lab/results/native_scenario_db-pool-exhaustion_20261002T213201Z.json).

Recovered state retains 42 trace events, 25 evidence items and eight checkpoints,
and remains `waiting_approval`. Runtime health confirms required API auth,
PostgreSQL checkpoints, actual MCP tools, deterministic reasoning and memory
task state. The API adapter has only diagnosis-create and state/history-read
methods, no approval endpoint. Five new tests cover prerequisite gates,
authenticated endpoint selection and no retry on authorization failure.
Current full regression: 421 Python tests pass with isolated PostgreSQL, no
skips; Ruff and whitespace checks pass. Zero model/production action calls
occurred. Owned API/workload/observation resources cleaned; final Docker process/
network checks were empty and PostgreSQL stopped with data retained.

This proves one real API crash **after committed diagnosis/approval pause**.
It does not prove diagnosis-in-flight crash recovery, approval/resume after
crash, remediation execution crash recovery, Redis/SSE recovery or current-model
semantic quality. The preceding full eight-case batch below remains evidence
for its dated verifier revision; its hashes do not match this new optional API
probe code, and all eight have not been rerun through the crash mode. Production
workflow/API code was not changed in this iteration. Broader completion remains
unproven; earlier failed artifacts are preserved.

The preceding monotonic-TTL, bounded-log-delivery and exact-log-visibility revision
passed the complete eight-case PostgreSQL native matrix in one fresh batch:
**8/8 cases and 144/144 named checks**, 801.413 seconds elapsed (13.36 minutes).
Mean time inside deterministic diagnosis was 0.116 seconds; this excludes store
startup, warmup/fault traffic, recovery inspection and cleanup and is not model
latency. Result:
[`native_scenario_matrix_20261002T205334Z.json`](../fault-lab/results/native_scenario_matrix_20261002T205334Z.json).

Five scenarios (pool, CPU, memory, deployment and Kafka) retained `waiting_approval`;
downstream timeout, retry storm and cache penetration returned read-only
`diagnosed`. Every complete incident/checkpoint/repository state survived a
separate-process PostgreSQL inspection, including 38–42 trace events, 21–25
evidence items and eight checkpoints for approval-paused cases/nine for completed
advisory cases. No approval/resume or execution step ran. All expected baseline
`(service, SERVER span)` pairs were independently checked against the same trace
and request ID, in addition to inspecting the named checks. All eight matched
on their first log round; delayed visibility was not observed in this batch.

Independent audit confirmed all eight per-case artifact hashes, 36 source/config
hashes and the compiled gateway hash match this executed revision. Each owned
observation stack cleaned successfully; final Docker process/network checks were
empty. The isolated PostgreSQL was stopped with incident/checkpoint data retained.
Zero model requests and zero production action calls occurred. Same-revision
regression: 416 Python tests pass with isolated PostgreSQL, no skips; Ruff and
whitespace checks pass. Earlier failed batches remain unchanged.

This supersedes the earlier unverified latest-version eight-case native recovery
claim, **not** the broader completion gaps. The batch uses real native HTTP/OTLP,
Prometheus/Loki/Tempo, Go and MCP with PostgreSQL storage, but deterministic
reasoning, bounded simulated faults and fixture catalog/deployment/knowledge.
It is not a model accuracy/semantic faithfulness benchmark, real database/Kafka
exhaustion, proof of prior missing-log causation, actual Loki-outage durability,
live API-server crash recovery, Redis recovery, or approval/resume verification.
The original Compose/API matrix and current-model evaluation remain separate.

The latest verifier revision fixes an asymmetric observation policy: Tempo
parentage was polled, but baseline log identity had only one lookup. Log
correlation now performs at most ten read-only rounds with one-second gaps and
a 15-second monotonic deadline checked between rounds, in addition to existing
tool-call timeouts (not a hard 15-second total bound). It requires **every**
expected `(service, SERVER span ID)` pair with the exact unique trace/request
identity, not merely one match per service. Partial exact matches accumulate;
missing pairs at the bound still fail. Each attempt retains event/match counts
and redacted error types, plus missing pairs and workload delivery/drop/retry
counters in `baseline_log_lookup`. Cancellation propagates.

Eight new tests cover initial absence followed by exact visibility, each wrong
identity field, multiple required spans at one service, permanent query failure
with redaction, cancellation and an empty expected-pair set. Current full
regression: 416 Python tests pass with isolated PostgreSQL, no skips; Ruff and
whitespace checks pass.

A fresh memory-leak probe passed all 18 checks in 90.712 seconds, with 0.119
seconds inside deterministic diagnosis. The first log round already contained
both exact payment/ledger baseline events; each native shipper reported one
delivered record. The incident retained 42 trace events, 25 evidence items and
eight checkpoints through separate-process PostgreSQL inspection, and remained
`waiting_approval` without executing. Result:
[`native_scenario_memory-leak_20261002T205054Z.json`](../fault-lab/results/native_scenario_memory-leak_20261002T205054Z.json).
No model/production action calls occurred. Owned observation resources cleaned,
final Docker process/network checks were empty, and PostgreSQL was stopped with
data retained. This run did **not** observe initial absence or delayed visibility;
it does not establish the earlier missing-log cause. The new diagnostic fields
enable that distinction in future runs. Full latest-version eight-case success,
real Loki-outage delivery, live API crash/Redis recovery, approval/resume and
current-model semantic evaluation remain unproven. Earlier failed results are
unchanged, and their source hashes do not describe this latest verifier revision.

The log-retry revision was rerun as a single fresh eight-case PostgreSQL batch:
seven cases passed, 140/144 checks passed, and the aggregate remains failed.
All eight exact baseline log/span checks and all eight fresh-process recovery
checks passed. Memory-leak diagnosis abstained because the bounded fault had
expired before diagnosis: the case took 90.808 elapsed monotonic seconds, but
its injection/diagnosis UTC timestamps differed by about 229 seconds, beyond
the 120-second wall-clock-based TTL. The latest five memory samples were zero.
Its classification, hypothesis, remediation and approval-boundary checks failed;
the verifier did not relax them. Result:
[`native_scenario_matrix_20261002T201508Z.json`](../fault-lab/results/native_scenario_matrix_20261002T201508Z.json).
Total batch elapsed time was 851.098 seconds. Before changing the TTL code,
all 36 source/config hashes, gateway hash and eight artifact hashes were checked
against the executed revision. All owned observation stacks cleaned successfully.

Fault injection now enforces elapsed duration with `time.monotonic()`, keeping
UTC `expires_at` only as display metadata. Clear resets both deadlines and
gauges. Three new tests cover forward/backward wall-clock jumps and the actual
injection/clear deadline lifecycle; the exact-deadline test now advances only
monotonic time. Current regression is 408 Python tests passing with isolated
PostgreSQL, no skips; Go race checks, 65 frontend logic tests, Ruff and whitespace
checks pass. Frontend tests do not constitute a new browser visual verification.

A fresh memory probe after this TTL fix correctly detected growth (latest five
samples: 10/15/20/25/30 MiB), proposed rollback and retained `waiting_approval`
through fresh-process PostgreSQL inspection. However its exact baseline log/span
check failed, so this separate probe is **failed**, with 17/18 checks passing:
[`native_scenario_memory-leak_20261002T204432Z.json`](../fault-lab/results/native_scenario_memory-leak_20261002T204432Z.json).
The recurrence shows bounded push retries have not eliminated the missing
reference problem; its exact cause and real-outage delivery remain unresolved.
The full eight-case matrix has not been rerun after the TTL change, and older
matrix hashes must not be presented as current-source hashes. Zero model or
production action calls occurred. Final Docker process/network checks were
empty; PostgreSQL was stopped with data retained. Live API crash/Redis recovery,
approval-resume and current-model semantic evaluation remain unproven.

The latest revision fixes the lab shipper's drop-on-first-HTTP-error behavior.
Network errors, HTTP 429 and server errors retry the identical batch at most
five times with 0.5/1/2/4-second backoff. Other HTTP rejections drop immediately.
A nonblocking 1,000-record queue, 16-KiB serialized-line cap and 100-record batch
limit bound memory. Prometheus counts accepted records, retries and drops by
bounded reason (`queue_full`, `oversize`, `rejected`, `retry_exhausted`,
`shutdown`). Cancellation counts in-flight/queued records and propagates; no
recursive logging is used. This remains best-effort delivery without a durable
spool or exactly-once/guaranteed shutdown flush.

Eleven new tests verify identical trace/span/timestamp payloads across transient
HTTP/network failures, bounded retry exhaustion/backoff, permanent rejections,
nonblocking queue overflow, UTF-8 size limits and cancellation accounting.
Current complete regression: 405 Python tests pass with isolated PostgreSQL,
no skips; Ruff and whitespace checks pass.

A fresh single downstream-timeout run passed all 18 checks in 91.962 seconds
(0.113 seconds inside deterministic diagnosis). Two unique baseline log events
matched their exact Tempo SERVER span IDs. Fresh-process inspection recovered
40 trace events, 23 evidence items and nine checkpoints, with a terminal read-only
`diagnosed` state. Zero model/production action calls occurred. Result:
[`native_scenario_downstream-timeout_20261002T201326Z.json`](../fault-lab/results/native_scenario_downstream-timeout_20261002T201326Z.json).
Owned temporary observation resources were cleaned and final Docker process/
network checks were empty; PostgreSQL was stopped with data retained. A single
passing rerun does not prove why the prior baseline was missing or demonstrate
delivery under a real Loki outage. The full eight-case PostgreSQL matrix has
not been rerun after this shipper revision; its prior failed aggregate below
is retained unchanged, and its hashes describe the earlier source revision.

The preceding full PostgreSQL native matrix completed all eight scenarios in
863.218 seconds, with **seven cases passing and one failing**, 143/144 named
checks passing. All eight independently recovered the complete incident state,
repository state, checkpoint history and correct terminal/approval interrupt
position in a fresh Python process. Five remained `waiting_approval`; three
returned read-only `diagnosed`. No model or production action calls occurred.
The sole failed check was `baseline_log_server_span_correlated` for downstream
timeout; diagnosis and all recovery checks passed, but the required baseline
log references were absent. The aggregate correctly remains `ok: false`:
[`native_scenario_matrix_20261002T191955Z.json`](../fault-lab/results/native_scenario_matrix_20261002T191955Z.json).

Independent inspection verified all eight artifact hashes, 36 source/config
hashes and the compiled gateway hash. Each owned observability stack cleaned
successfully; final Docker process/network checks were empty. The isolated
PostgreSQL was stopped with its new incident/checkpoint data retained. The
matrix now supports `--checkpoint-postgres`, validates the dedicated local
database before Docker work, and requires all 18 checks plus PostgreSQL backend
and successful recovery metadata per case. Missing checks cannot silently
count as success. Eleven new matrix tests exercise these gates and metadata.

Complete regression is now 394 Python tests passing, no skips. The first run
had one explicit-clear unit test fail after wall-clock timestamps advanced
beyond its fault TTL; its clock is now controlled, with a separate exact-deadline
expiry test. No production clock/expiry behavior was changed. Ruff and whitespace
checks pass. At that revision, inspection found the lab log shipper discarded a batch
after an HTTP error; this was a delivery risk, not a proven cause
of the failed reference check. The batch's failed result is retained rather
than relabeled as successful durable end-to-end verification. All-eight recovery
inspection is now proven for bounded simulated faults; all-eight full checks,
live API crash recovery, Redis and approval/resume remain unproven.

The preceding native `db-pool-exhaustion` probe used real Prometheus/Loki/Tempo,
Go and MCP, plus isolated PostgreSQL checkpoint and incident storage. All 18
checks passed in 105.298 seconds (0.124 seconds inside deterministic diagnosis).
After closing the original checkpoint connection, a new Python process with
fresh database connections recovered the entire incident state, 42 trace events,
25 evidence items and eight graph checkpoints. The `human_approval` interrupt
was retained; the incident remained `waiting_approval`. Exact baseline log/span
correlation also passed. Result:
[`native_scenario_db-pool-exhaustion_20261002T185901Z.json`](../fault-lab/results/native_scenario_db-pool-exhaustion_20261002T185901Z.json).

The recovery reader cannot call telemetry or remediation tools. It inspects
existing state and history; it does not resume or approve execution. Repository
connection performs idempotent schema setup, so this is not a SQL read-only
transaction claim. Nine additional verifier tests reject missing/remote or
implicitly selected databases and libpq address overrides, require live
telemetry, and enforce the recovery reader's explicit gate. Current regression:
382 Python tests pass with isolated PostgreSQL, no skips; Ruff and whitespace
checks pass. Temporary workloads/observability containers/network were cleaned,
and PostgreSQL was stopped with its data retained. Zero model requests and zero
production actions occurred.

This proves one bounded simulated fault with real storage and cross-process
inspection, not actual PostgreSQL pool exhaustion, all eight durable scenarios,
Redis recovery, approval/resume, or API-server crash recovery. The historical
eight-case memory matrix below has not been rerun at this latest revision.

The log-correlation revision fixes a real identity mismatch: native workload
logs previously called the arbitrary client `x-trace-id` a trace ID, while
OpenTelemetry used a different W3C trace. Client correlation is now a separately
bounded `request_id` (preferred `x-request-id`, legacy header accepted only as a
request correlation value). Logs and workload responses obtain canonical trace
IDs from the active span; log span IDs are also retained. Formatter extras
cannot forge timestamp/service/message/level or trace/span identity. Trace IDs
are omitted when no valid span exists rather than fabricated. Downstream calls
propagate request identity separately from W3C context. Go, MCP and the Agent
retain both new log fields; deduplication distinguishes span IDs.

An actual no-fault native Prometheus/Loki/Tempo→Go→MCP→workflow probe passed all
seven checks in 58.305 seconds. Two request-completed logs (payment and ledger)
matched their exact observed Tempo SERVER span IDs and shared the distinct
request ID. The workflow correctly abstained on the healthy service; no
approval/execute/model steps occurred. Result:
[`native_live_telemetry_20261002T183144Z.json`](../fault-lab/results/native_live_telemetry_20261002T183144Z.json).
The probe's `--verify-log-correlation` gate requires live telemetry; matching
uses the unique baseline trace, exact retained server ID, target service and
request ID, not service/time overlap alone. Temporary native processes,
containers and network were cleaned afterward.

Five new Python tests cover actual SDK span-to-log matching despite a forged
client trace header, formatter provenance protection/no-span omission, bounded
request correlation, MCP identity projection and refusal of Demo-mode identity
verification. All 373 Python tests pass with isolated PostgreSQL, no skips; Go
race tests (including extended Loki field preservation), Ruff and whitespace
checks pass. The database was stopped with data retained. No console changes
or new model requests occurred. This revision's real log/span identity check
does not prove causality or semantic faithfulness; the complete eight-case
matrix below belongs to the preceding workload revision and has not been rerun
after these log-identity changes. Its hashes matched source files at that dated
run, not the subsequently edited identity code.

The complete workload-corrected native matrix passed all eight canonical cases
and all 96 named checks in a single fresh batch. Total elapsed time was 818.743
seconds (13.65 minutes), including startup, baseline/fault traffic and cleanup;
mean time inside the deterministic workflow was 0.107 seconds. Five destructive
proposals remained `waiting_approval`; downstream timeout, retry storm and cache
penetration finished `diagnosed` with read-only guidance. No approval, execution
step, production mutation or model request occurred. Result:
[`native_scenario_matrix_20261002T180456Z.json`](../fault-lab/results/native_scenario_matrix_20261002T180456Z.json).

| Canonical fault | Primary service | Workflow milliseconds | Result |
| --- | --- | ---: | --- |
| Database pool exhaustion | payment-service | 118 | Approval-required rollback |
| Downstream timeout | ledger-service | 104 | Read-only guidance |
| CPU spike | risk-engine | 98 | Approval-required restart |
| Memory growth | payment-service | 110 | Approval-required rollback |
| Deployment regression | payment-service | 111 | Approval-required rollback |
| Kafka lag | ledger-service | 113 | Approval-required restart |
| Retry storm | checkout-api | 97 | Read-only guidance |
| Cache penetration | payment-service | 106 | Read-only guidance |

An independent read-only audit verified all eight case artifact hashes, all 35
source/config hashes and the gateway binary hash against files at that run's
revision. It
checked the generic service-only question, primary service/type/action/status,
real telemetry evidence, continuous workload flag, no execution/approval step,
non-causal dependency impacts and every hypothesis's structural citation
contract (two distinct usable IDs/two kinds including metrics or logs). This
does not measure semantic faithfulness. The successful memory case's latest
five five-second samples grew from 11 MiB to 30 MiB, unlike the failed stopped-
traffic plateau. All newly owned containers/networks/native processes were
confirmed removed/stopped; existing project containers and the isolated test
database remained stopped. No user images, volumes or database data were deleted.

This closes the current deterministic eight-simulation live telemetry matrix,
not current model accuracy, real database/Kafka exhaustion, production causal
attribution, live-fault PostgreSQL recovery or Kubernetes deployment. The 368-
case isolated-PostgreSQL regression is separate evidence. All failed/incomplete
precursor batches below remain preserved; they are not blended into this pass.

The new native matrix harness creates four uniquely named, disposable Docker
observability containers and a private network per canonical case. Existing
project/user containers, networks, images and volumes are never reused or
cleaned. Cached image IDs and source/config/gateway hashes are captured, and
the batch checkpoints hashed per-case references after every attempt. Named
checks, not just a supplied `ok` flag, determine success; cleanup failure stops
the batch. Probe timeout cleanup targets only its newly created process group.

The first batch attempted eight cases but only database-pool exhaustion and
cache penetration reached diagnosis and passed. Six middle cases failed before
producing case artifacts. This is a failed matrix, not an eight-case success:
[`native_scenario_matrix_20261002T174400Z.json`](../fault-lab/results/native_scenario_matrix_20261002T174400Z.json).
After the final case exited, actual workload/gateway ports 8191–8194 and 8081
failed ordinary bind with errno 48 but permitted `SO_REUSEADDR` binds, confirming
closed TCP `TIME_WAIT` rather than an active listener. A separate wholly owned
socket probe reproduced this behavior. Both native/matrix preflight checks now
match uvicorn's address-reuse semantics, without enabling `SO_REUSEPORT`;
actual listening ports still fail. The batch preserves failure information and
safe CLI error types, without provider text. A complete fresh rerun is required
to verify this correction; no earlier per-case results are substituted.

The second batch verified the port fix: database pool, downstream timeout, CPU,
deployment regression and Kafka lag passed. Memory growth correctly abstained:
its seven increasing samples were followed by a plateau before diagnosis, so
the most recent five samples did not show sustained growth. This is retained
as a failed diagnosis, not a passing memory case:
[`native_scenario_memory-leak_20261002T175808Z.json`](../fault-lab/results/native_scenario_memory-leak_20261002T175808Z.json).
The batch was deliberately interrupted during the next case after six completed
rows; owned subprocesses, four containers and network were confirmed stopped/
removed. The old harness did not checkpoint its interrupted seventh case.
Its incomplete, failed checkpoint remains:
[`native_scenario_matrix_20261002T174959Z.json`](../fault-lab/results/native_scenario_matrix_20261002T174959Z.json).

The graph is now initialized before fault traffic and actual traffic remains
active through diagnosis in a bounded, cancelled/awaited task. The classifier's
growth and multi-sample criteria were not weakened. Immediate workload failure
prevents diagnosis, and future interrupted cases are saved before stopping the
matrix. Test-database regression at the preceding matrix revision passed all
364 cases without skips, with isolated PostgreSQL configured, and the database
was stopped with data retained. A fresh complete batch at the workload-corrected
revision is required; earlier passing cases are not substituted into it.

The workload-corrected revision passes all 368 Python tests with isolated
PostgreSQL configured, no skips. The four additional cases cover continuous
traffic cancellation on successful/failed diagnosis, prevention of diagnosis
after an immediate workload error, and saved interrupted-case cleanup. The
isolated database was stopped afterward with data retained. Ruff and diff
whitespace checks pass. This database regression is separate from the native
matrix's in-memory checkpoint and must not be presented as live-fault database
recovery evidence.

The native verifier now accepts all eight canonical scenario selectors with
their actual scenario services, using four owned localhost workloads. It asks
only a generic service question, validates incident type/hypothesis/remediation/
approval state, records workload status counts and separates workflow latency
from total warmup/runtime. Baseline trace proof is separate from post-fault
checkpoint observations: a fault may prevent downstream calls, so healthy
baseline edges are not demanded in the later sample. Fifteen new test cases
cover all selectors/expected profiles, invalid-mode refusal, expected HTTP
fault responses, unexpected-status refusal and cleanup of every owned child on
readiness failure and saved baseline-timeout evidence. The full Python suite
passes 344 cases with ten stopped-database cases skipped; Ruff and diff whitespace
checks pass. The console is unchanged.

The canonical database-pool-exhaustion probe passed all 12 checks. Total runtime
was 89.672 seconds, with 0.104 seconds inside the deterministic workflow. It
retained real metric/log/trace reads, found pool ratio 1.0 with timestamped
latency/error anomalies, selected database connection pool exhaustion from the
generic question and proposed an approval-required dry-run rollback. Checkout
had an overlapping observed latency anomaly; no causal direction was asserted.
No approval/execution occurred. Artifact:
[`native_scenario_db-pool-exhaustion_20261002T173002Z.json`](../fault-lab/results/native_scenario_db-pool-exhaustion_20261002T173002Z.json).
The pool is a bounded instrumented gauge/log/delay simulation, not a real
PostgreSQL pool. Supporting eight selectors does not prove an eight-case pass.

The first memory-case attempt failed before injection with
`trace_parentage_timeout`; it did not produce a JSON artifact in that runner
revision. Live Collector/Tempo inspection found all seven baseline spans, but
Tempo search returned `480e4b8cae567dc780306c46ad07035` (31 hex digits), while the
OTLP trace bytes decoded to the full leading-zero ID
`0480e4b8cae567dc780306c46ad07035`. The gateway had discarded the short search
ID before fetching spans. Search IDs now normalize nonzero hex strings of at
most 32 digits to 32 characters. OTLP byte-width, service/time/parentage and
budget checks remain strict. Two new Go tests cover malformed/zero/oversized
search IDs, uppercase/short inputs, strict OTLP widths and deduplication of short
and padded search forms through actual fetch/parsing. The Go race suite passes.
This behavior is consistent with Tempo's default
[`left_pad_trace_ids=false`](https://github.com/grafana/tempo/blob/main/docs/sources/tempo/configuration/_index.md).

After rebuilding the gateway, the memory probe deliberately used a valid
leading-zero baseline ID and passed all 12 checks in 89.294 seconds, including
0.110 seconds in the deterministic workflow. Seven five-second memory gauge
points grew from 1 MiB to 30 MiB; diagnosis selected `memory_leak`, proposed an
approval-required dry-run rollback and remained `waiting_approval`. No execution
occurred. Artifact:
[`native_scenario_memory-leak_20261002T173757Z.json`](../fault-lab/results/native_scenario_memory-leak_20261002T173757Z.json).
The memory gauge reports intentionally retained allocations, not measured host
RSS. Canonical probes now always exercise a leading-zero baseline, and future
baseline timeouts save normalized failure evidence before any injection.
All owned native processes and observability containers were stopped after the
turn; only new `--rm` Prometheus test containers were discarded. No user images,
volumes or database data were removed, no model requests or approvals were made.
These two canonical cases plus the prior payment CPU experiment are not proof
of the full eight-case matrix, model accuracy or PostgreSQL durability.

A native-workload/live-Prometheus/Loki/Tempo CPU probe completed in 74.438
seconds with all seven checks passing. Three owned HTTP services, a freshly
compiled Go gateway, actual MCP HTTP and the deterministic LangGraph workflow
retained real metrics, log events, trace summaries and 16 observed call pairs.
CPU classification used two valid 0.95 fault-intensity gauge points; latency
samples rose from 9.5 to 475 ms. The workflow selected `cpu_saturation`, proposed
a dry-run restart and stayed `waiting_approval`; no approval or execution step
occurred. Observed dependency directions remained non-causal. Artifact:
[`native_live_telemetry_20261002T172324Z.json`](../fault-lab/results/native_live_telemetry_20261002T172324Z.json).

Two preceding failed checks are preserved rather than relabeled as successes:
[`171427Z`](../fault-lab/results/native_live_telemetry_20261002T171427Z.json)
retained all live telemetry but only one CPU evaluation point, so classification
abstained; [`172014Z`](../fault-lab/results/native_live_telemetry_20261002T172014Z.json)
exposed a gateway encoding failure from a startup Prometheus `NaN`. Ordinary
metric evaluation now uses a bounded 15-second step over 30 minutes (121 points
maximum), and the gateway drops non-finite values without zero-filling them.
The multi-sample classifier requirement is unchanged, and the trace records
the new step. Python regression: 329 passed, ten database cases skipped because
the isolated database was stopped. The Go race-enabled suite passes, including
a new finite/non-finite sample and JSON-encoding regression case. Ruff and
diff whitespace checks pass.

This is one actual instrumented fault, not the eight-fault matrix or a model
benchmark. The CPU gauge is configured experiment intensity, not measured host
CPU. Catalog/deployments/knowledge remain fixtures; no model requests, durable
PostgreSQL recovery, Redis or real remediation were exercised. The owned native
children and all four observability containers started for this turn were
stopped. Only the new `--rm` Prometheus container and its temporary in-memory
test samples were discarded; no user image, volume or database was removed.

One native-workload/live-Tempo topology probe completed in 17.197 seconds.
Existing stopped Tempo and Collector containers could start independently
without any Docker cleanup, despite the prior full-stack database failure.
Three actual native fault-lab HTTP service processes generated one uniquely
identified trace. OTLP collection, real Tempo search/trace lookup, a freshly
compiled Go gateway, actual MCP HTTP, and the LangGraph memory checkpoint all
retained checkout-api→payment-service and payment-service→ledger-service calls
with actual trace/client/server span IDs. The workflow classified those
relations as observed calls, kept risk-engine catalog-only, and left every
`causal_direction_verified=false`. Status was waiting for approval, no decision
was submitted, and no prepare/execute steps occurred. Result:
[`observed_topology_live_20261002T165821Z.json`](../fault-lab/results/observed_topology_live_20261002T165821Z.json).
Metrics/logs/reasoning in the probe use DemoTelemetryTools; PostgreSQL, Redis,
the model and the full fault matrix are not exercised by this run. The runner
now checks its stop-before-execution invariant explicitly. Three new tests
cover the opt-in gate, refusal of occupied ports and cleanup of owned children
after failed readiness. The Python suite passes 327 cases with ten database
cases skipped (database stopped for this instrumentation-only revision); the
Go race-enabled suite passes. The native child processes and both containers
started for the probe were stopped afterward; no containers/images/volumes
were removed. The generated Tempo tmpfs sample is ephemeral; the JSON artifact
retains the normalized observations and incident trace. Current full-fault
verification and production authentication still remain unproven.

The observed-topology console revision passes frontend typecheck, production
build and all 65 frontend tests. Nine new tests cover historical UTC windows,
safe trace/span projections, sampled directions, partial/empty samples,
unsupported/failed/legacy reads, invalid service/source/window/coverage/count/
budget data, unsafe call IDs, duplicate identities and independent catalog
failure. The panel separates catalog directions from observed call directions,
exposes expandable trace/client/server span references, retains sampling and
failure/omission/discard counts, and explicitly disclaims root-cause proof.
A local browser acceptance run used synthetic observed calls, Demo telemetry,
memory persistence, no model and an execution-disabled backend. It expanded
one trace reference, verified partial-sample warnings and UTC bounds, reloaded
the incident URL and confirmed observed upstream/downstream labels survived.
Two new diagnoses returned empty and unavailable observations; both cleared
old observed arrows/references while retaining catalog directions. Empty data
did not imply absence of dependencies. No approval was submitted; status stayed
waiting for approval. Screenshot:
`/private/tmp/copilot-observed-topology-console-qa.png`. This is UI/API integration
evidence, not a live Tempo/Go/MCP chain or production causal inference. Temporary
preview servers and browser tab were stopped/closed after verification.

The MCP/workflow observed-topology integration passes all 334 Python tests with
isolated PostgreSQL configured, no skips. Fourteen new cases cover prioritized
observed service selection without catalog/causal upgrades, service/source/window/
coverage/count/budget validation, ambiguous duplicate and zero IDs, partial empty
samples, and both independent backend-failure directions. A real in-process MCP
JSON-RPC transport calls a mocked Go HTTP boundary twice with authenticated GETs;
normalized trace/span references reach the actual LangGraph workflow, impact
records and checkpoint read. This uses Demo telemetry for metrics/reasoning and
mocked observed calls, not a live Go/Tempo chain. The existing fresh-PostgreSQL
topology test now includes observed calls and verifies both repository and graph
connections retain them, still with no causal proof. The static catalog and
observed parentage are separate fields and statuses; catalog failure does not
discard successful observed data or vice versa. Provider diagnostics and extra
fields are not persisted. Both synthetic benchmarks were rerun without failures,
new model calls or execution attempts; they do not measure observed topology.
The isolated database was stopped after tests, with its data retained. The
console does not yet render observed trace references or sampling metadata.

The observed-trace gateway revision passes the Go race-enabled suite. Twelve
new Go tests (including 13 invalid-parentage subcases) exercise exact
CLIENT→SERVER matching, legacy/OTLP JSON and hex/base64 IDs, rejection of
ambiguous/orphan/cyclic/cross-trace/out-of-window data, document/output budgets,
eight-trace fetching, partial failures, cancellation, endpoint authentication,
query-override rejection, fixture refusal, safe errors and successful endpoint
projection. Two new Python tests use the actual OpenTelemetry SDK exporter
and HTTP transport to verify request SERVER kind and downstream CLIENT kind,
remote parent IDs and same-trace context propagation. Context is now injected
inside the outgoing client span. The Python suite passes 310 cases, with ten
PostgreSQL-gated cases skipped because the isolated database was not started
for this gateway/instrumentation-only change. No database migration changed.
The new gateway observed-dependencies endpoint is not yet wired into MCP,
workflow topology, or UI; those still display catalog/unknown provenance. No
model request or production action ran. These transport fixtures are not a
live Tempo/lab test, and parentage is not incident-causation proof.

The dependency-direction console revision passes frontend typecheck, production
build and all 56 frontend tests. Six new cases cover upstream/downstream and
bidirectional catalog arrows, old name-only incidents, wrong-primary/oversized
responses, invalid/unrelated/self/duplicate edges, provenance projection and
time-overlap separation. The console derives arrows from retained topology,
not assessment relation claims, omits arbitrary edge metadata, and labels the
current static demo catalog as not live trace discovery. Missing provenance
and old incidents remain unknown. A local browser run used a synthetic backend
with no model or mutation adapter, diagnosed an incident, checked all three
direction labels and the static-source warning, then reloaded its incident URL
and confirmed those fields persisted with status still waiting for approval.
No approval was submitted. Screenshot:
`/private/tmp/copilot-topology-console-qa.png`. This is UI/API integration proof,
not observed production topology or fault-propagation evidence. Temporary
preview servers and browser tab were stopped/closed afterward.

The dependency-topology revision passes all 318 Python tests with isolated
PostgreSQL configured, no skips, plus the Go gateway suite. Eleven new tests
cover directed and bidirectional catalog relations, bounded schema parsing,
discarding unrelated/self/duplicate/invalid edges and embedded fixture metrics,
unknown provenance, name-only compatibility, one MCP call per lookup, real
in-process MCP transport, and fresh PostgreSQL repository/checkpoint connections.
The incident now retains `service_topology` and its source/kind in the trace;
dependency impacts distinguish upstream/downstream/bidirectional catalog
relations but always leave `causal_direction_verified=false`. Both current Go
stores use the fixture catalog for dependencies, including the live-observability
store, so the gateway explicitly returns `static_fixture_catalog`. MCP retains
that provenance and does not upgrade missing provenance from older gateways.
Unknown/name-only adapters do not invent edges. The existing 150-case workflow
and 18-case dependency synthetic benchmarks were rerun without failures, model
calls, or execution attempts; neither benchmark measures directed-topology
accuracy or causal inference. The new direction/provenance fields are backend
state/trace data, not yet rendered by the console dependency cards. The isolated
database was stopped with its data retained after verification.

One explicitly authorized model-backed smoke run of `evidence-contract-v2`
completed in 10.062 seconds using `gpt-5.6-terra` (2,172 input + 588 output
tokens). With synthetic demo telemetry and an in-memory LangGraph checkpoint,
the model returned one hypothesis associating the v142 query-fan-out deployment
with increased connection demand and latency, while explicitly noting that
causation and pool exhaustion were not directly proven. Its four retained
citations passed the structural evidence checks; semantic entailment remains
unverified. The workflow proposed rollback to revision 141, performed only the
demo dry-run, and interrupted in `waiting_approval`. No approval was submitted
and no production action was executed. The full 42-event trace is saved in
[`diagnosis_smoke_20261002T162800Z.json`](../evals/results/diagnosis_smoke_20261002T162800Z.json).
This is a single live-model smoke test, not a live-telemetry integration test,
accuracy benchmark, durable-checkpoint test, or real Kubernetes dry-run.

The latest error-boundary/approval-lock revision passes all 307 Python tests
with isolated PostgreSQL configured, no skips. Fourteen new cases cover
redacted diagnosis failures, five post-claim recovery error types, post-resume
repository failure, MCP client/initialization error redaction, MCP schema/runtime
response redaction, and a real PostgreSQL API reconstruction. A deliberately
stale waiting checkpoint cannot permit a second recovery invocation after an
error: both the original and replacement approval keys are denied. The database
case closes the first API lifespan, reconstructs checkpoint/repository
connections, confirms the checkpoint still appears waiting, and proves the
persisted claim blocks both keys without resuming. The synthetic resume failure
does not execute a production tool. Claims now remain reserved after recovery
errors; a response error is not treated as proof of non-dispatch.
Provider messages/rejected schema inputs/validator context are not reflected in
public API/MCP errors, and schema issues retain location/type details. No model
request or production operation was made. The isolated PostgreSQL instance was
stopped afterward with its test data retained. Production console authentication
still requires a chosen verified identity-provider/gateway contract: the starter
header reader is not an identity verifier and is not an authorization gate.

The latest inspection-history console revision passes frontend typecheck,
production build and all 50 frontend tests. Ten new cases cover GET-only
history reads, completed/unavailable/unfinished outcomes, redacted field
projection, unknown/cross-incident/duplicate/oversized/unsafe records, empty
history, authentication/server errors and transport failure without retries.
The component never queries an operation or posts approval while refreshing
history. A local browser acceptance run used a synthetic no-model/no-mutation
backend, inspected matching/conflicting markers, simulated a failed query and
an audit-finalization failure, then read all four records through the real
history API. Empty history correctly did not imply non-execution. Expanded
unfinished-record details showed the original service, namespace, revision,
approval key, demo actor and unauthenticated requester, with an explicit
identity warning. Incident status remained pending and no approval buttons
were restored. Screenshot: `/private/tmp/copilot-inspection-audit-history-qa.png`.
The browser backend used memory storage and simulated Kubernetes observations;
this is UI/API integration evidence, not new PostgreSQL or real-cluster proof.
Temporary preview servers were stopped afterward. Protected console auth/SSO
and live fault-lab verification remain unfinished.

The preceding inspection-audit revision passed all 293 Python tests with isolated
PostgreSQL configured, no skips. Six new audit cases cover start-before-query,
completed/failed observation records, pre-query and post-query database failure,
bounded immutable memory reads, and real PostgreSQL fresh-connection recovery.
An additional authorization case protects the history route; a required-mode
test confirms the audit stores the bound requester rather than an arbitrary
browser actor. The new table/migration stores immutable request context and a
single finalized outcome separately from incidents/checkpoints/approval claims.
A start failure prevents the query. A finalization failure leaves an unfinished
record and returns 503; readers must not interpret it as an execution failure.
The fresh-connection test reads completed, unavailable and unfinished records,
verifies the original incident is identical, and refuses outcome overwrite.
The endpoint projects safe target fields and normalized observations only;
raw diagnostics, receipts and provider notes are omitted. Memory mode is not
durable. The history API returns at most 100 records; at that backend-only
revision it was not exposed in the console. Both synthetic benchmarks were rerun with no failures, no model
requests and no execution attempts; they do not assess audit persistence. The
isolated PostgreSQL server was stopped afterward and its test data retained.

The operation-inspection console revision passes frontend typecheck, production
build and all 40 frontend tests. Eleven new tests cover GET-only requests,
redacted 401/403/409/502 handling, wrong incident/tool, invalid safety flags,
source/status mismatch and no retry on network failure. A local synthetic
browser run diagnosed an incident, simulated approval response loss without
any real operation, then manually inspected matching and conflicting markers
and a failed query. The panel replaced each previous result, cleared stale
success after failure, retained the incident's pending status, and did not
restore approve/reject controls. Screenshot:
`/private/tmp/copilot-operation-inspection-qa.png`. Query results are local UI
state only, not durable incident events. The browser test backend returned
simulated Kubernetes marker observations; this is not live Kubernetes evidence.
No model call was made. The direct-browser demo still sends no credential,
so required-mode operation needs a trusted server-side connection. A production
console authentication proxy/SSO remains unimplemented.

The preceding API/MCP operation-transport revision passed all 286 Python tests with
the isolated PostgreSQL 14 Unix-socket test database configured; none skipped.
Without that database, 279 pass and seven database-gated cases skip. Four new
transport cases exercise real in-process FastAPI, MCP JSON-RPC serialization,
token scopes, signed approval verification, the registry and Kubernetes adapter.
The Kubernetes HTTP endpoint is simulated: it records a rollback PATCH then
raises a response timeout. The workflow retains `remediation_pending`. Replacing
the MCP registry with empty execution/binding caches still lets an operator
inspect the original operation by one GET only. Matching, overwritten,
conflicting and legacy markers all remain observations, not resolution. API
reader scope and an actual MCP reader-token request both fail before Kubernetes
access. The MCP audit records the operator query as read-only. The graph's
diagnostic observations are demo fixtures, and neither Kubernetes nor MCP is a
separate running process in these cases. This is transport/identity-binding
evidence, not real-cluster rollout health, model quality or durable inspection
audit evidence. No production mutation or paid model request was made.
The isolated PostgreSQL server was stopped after verification and test data
retained. Docker/live-lab verification remains pending. At that transport-only
revision the console did not expose the operation-inspection endpoint.

## Earlier 2026-10-02 iteration status

The preceding operation-inspection API revision passed 275 Python tests with seven
database-gated tests skipped. Nineteen added cases cover original-intent query
binding, four Kubernetes marker outcomes, redacted provider/validation failure,
unknown fixture status, altered or missing legacy intent, target override
denial, operator authorization, and the MCP client's operator credential path.
The endpoint never invokes graph resume, releases approval claims, changes
the incident/repository/history, or marks a matching marker as resolved. All
observations remain separate from incident state; no durable inspection audit
or console integration was added. These tests use fixture/mocked transport,
not a live Kubernetes API. Both synthetic benchmarks were rerun against the
new workflow hash: 150 single-fault/negative cases and 18 dependency cases had
no failures, no model calls and no execution attempts. These benchmark runs do
not evaluate the new operation-inspection endpoint or resolve live-lab blocking.

The preceding model-evidence-contract revision passed 256 Python tests with seven
database-gated tests skipped. No paid calls were made in this revision's tests.
The preceding real-model smoke used synthetic demo telemetry and in-memory
LangGraph, completed a configured `gpt-5.6-terra` request in 9.438 seconds with
2,109 tokens, and abstained because rank-two citations had only one evidence
kind. Its full incident and trace remain in
`evals/results/diagnosis_smoke_20261002T155306Z.json`; this is a safety rejection,
not a successful diagnosis or a live telemetry measurement.
That smoke exposed an optional output-schema field, which is now required to
match the strict structured-output contract documented in
[OpenAI's official guide](https://developers.openai.com/api/docs/guides/structured-outputs).
The revised model prompt explicitly requires distinct usable citations across
at least two kinds (including metric/log) for every hypothesis and instructs
the model to omit unsupported alternatives rather than pad the result.
Context now includes service, observation time and a server-computed usability
flag; trace records `evidence-contract-v2`. Mock-provider graph tests prove
that a single valid candidate reaches approval with only a dry-run, whereas an
invalid second candidate still rejects the entire diagnosis without mutation
calls. These tests prove contract propagation and guardrail enforcement, not
improved model accuracy; the revised prompt has not had a fresh paid smoke.
The smoke runner requires `--confirm-api-cost` before loading credentials,
performs at most one request without automatic retry, and returns nonzero on
configuration/provider failure. It never resumes approval.

The preceding operation-status-tool revision passed 253 Python tests with seven database-gated
tests skipped. The preceding durable-intent revision passed all 231 tests with
an isolated PostgreSQL 14 instance configured through
`INCIDENT_TEST_DATABASE_URL`; no tests were skipped in that run. The Unix-socket server was
stopped afterward and its test data retained. Seven database-gated tests cover
approval claims/transactions and fresh-connection workflow recovery; the other
tests retain their existing in-memory or fixture scope. No Docker or production
data was modified. The synthetic snapshot benchmark itself does not exercise
PostgreSQL.

Fresh-connection recovery tests close the original PostgreSQL checkpointer,
create a new workflow/checkpointer connection, and verify the entire paused
incident is identical. A rejection resumes the durable approval interrupt,
calls neither mutation tool, and survives another fresh connection. A separate
test persists a citation-rejected diagnosis in both the LangGraph checkpointer
and incident repository, then reopens both: observations, rejection trace,
terminal state, and historical candidate snapshots remain readable without
another generator call. This verifies connection/workflow reconstruction, not
database crash recovery, process termination, or Kubernetes failover.

Dependency impact assessment now queries discovered services rather than
using topology alone as evidence of impact. Read-only latency/error metric
queries are deduplicated, capped at eight services, limited to four concurrent
calls and individually timed out after ten seconds. The state and trace retain
service-specific observations, threshold anomaly windows, overlap with the
primary window, unconfirmed data and query failures. Errors retain only their
type, not transport diagnostics. Dependency observations are collected after
primary classification, so their signals do not overwrite the primary rule
classification. Tests verify overlapping checkout latency, stale dependency
observations, failed queries, missing data, query bounds and checkpoint
retention. No healthy or causal-direction claim is made from these checks.
The dependency API only returns names, so upstream/downstream direction is
still unverified. These records are available through state/trace; the existing
topology diagram was initially not an impact-status visualization. The single-fault
snapshot benchmark has no dependencies and does not evaluate propagation.
The console now replaces the fixed three-node demo layout for backend results
with deduplicated discovered-service impact cards. Observed anomaly, query
failure, unconfirmed impact and not-assessed services have distinct labels;
anomaly-window overlap is separate from causal direction. No discovered
dependencies produces an empty-state notice, not placeholder services. Unique
retained evidence IDs link to the evidence explorer, while missing or ambiguous
references are flagged. Frontend typecheck, production build and all 29 tests
pass. Browser acceptance with synthetic local telemetry confirmed checkout
latency overlap, ledger query failure and unconfirmed risk-engine impact. The
checkout evidence link navigated to the retained record and expansion showed
the actual service name and five timestamped latency samples. This run made no
model call and did not execute a production operation.
The separate dependency-observation benchmark now runs the actual graph on
18 synthetic cases (nine observation patterns on two service pairs), including
overlapping/non-overlapping windows, empty/stale/future data, normal samples,
single spikes, transport errors and provider-raised timeouts. Expected labels
are confined to the scorer. The measured run had no failures, no execution
attempts and no model calls; status, overlap, primary-classification preservation
and checkpoint roundtrip checks all passed. Summary and full per-case states
are stored separately from the 150-case single-fault benchmark, with fixture,
workflow, runner and threshold hashes. Nineteen regression tests cover the cases
and absence of expected labels on the provider. This is not live propagation,
causal direction, model-quality or production accuracy evaluation, and does not
measure actual deadline cancellation.
Primary metric/log/trace queries now retain successful responses when another
read-only query fails with a transport error or timeout. Each unavailable
result has a distinct `tool_error` event with tool, service and error type;
raw diagnostics are not retained and failed calls do not get success events.
Tests verify usable primary pool metrics survive log/trace failures and remain
subject to normal citation validation and human approval. All three unavailable
sources produce a durable `insufficient_evidence` incident without model or
execution calls. API regression confirms repository persistence and terminal
SSE for that result. Unexpected programming errors are still raised, not
converted to missing data. Both synthetic benchmark runners were rerun after
the change; their normal and dependency-error fixtures remain separate from
these new primary-query failure regressions.
The live-fault verifier now treats an abstained diagnosis with no hypotheses or
remediation as a failed measurement rather than an indexing exception. Primary
metric summaries filter by service, preventing dependency observations with
the same metric name from overwriting them; competing candidates, impact
records and redacted query-error events are included in the measurement.
Three fixture tests verify these reporting boundaries. This is not a fresh live
matrix run. Read-only Docker inspection confirmed all project containers are
stopped; the lab PostgreSQL's latest startup reported `No space left on device`.
No cleanup or restart was performed. The completion audit now explicitly labels
August live/model numbers as historical and current live verification as pending.
API authorization adds an explicit required mode with separate read/operator
credentials and a server-bound operator actor. Fourteen new tests cover all
incident routes rejecting missing auth before workflow access, read diagnosis
and state/history/SSE access, forbidden reader approval, actor spoofing, bound
rejection without execution, invalid/Unicode/oversized credentials, health
redaction, fail-closed configuration and CORS behavior. Default demo mode
remains disabled and is reported by health; no production identity claim is
made. Compose configuration passes the server-only variables, but no runtime
credentials or deployment were changed. The direct-browser demo is not a
credential proxy and cannot operate against required mode yet. See
`api-authorization.md` for the remaining SSO/proxy/TLS boundary.
Live verifier authorization now uses reader credentials for diagnosis and
operator credentials only for rejection, with the configured bound actor.
Missing required configuration aborts before fault injection. Runtime reports
omit raw transport response/error text so echoed credentials cannot leak into
the result artifact. Three additional tests cover scoped headers/bound actor,
pre-injection failure and sensitive response redaction. Kubernetes base
manifests now require API auth and map the three Secret keys; the Kind verifier
uses public test-only credentials, checks 401/403 denial and bound rejection.
Both base/Kind overlays rendered with `kubectl kustomize`, and `bash -n` passed.
No Kubernetes apply, cluster creation, fault injection or deployment change was
performed; Docker-space blocking of live verification remains unchanged.
Kind verification no longer deletes an existing same-name cluster. Cluster
inventory must succeed before creation, and cleanup requires that creation
succeeded in this invocation. Failed inventory/creation does not authorize
deletion; `KEEP_KIND_CLUSTER=true` preserves owned clusters after later failure.
Five subprocess tests put fake Docker/Kind/Kubernetes commands ahead of the
system tools and record every invocation. They cover existing-cluster refusal,
inventory failure, creation failure, cleanup after owned build failure and
explicit retention. No real cluster/container was created or removed. The
combined regression command now also includes `deploy/kubernetes/tests`.
An additional PostgreSQL recovery test starts a separate workflow subprocess,
waits for it to synchronously commit an approval-pause checkpoint and incident
repository state, then sends SIGKILL only to that owned test subprocess.
The child exit code confirms actual signal termination. A new workflow/saver
reads the identical incident state, obtains a durable approval claim, resumes
with rejection, preserves observations and prior trace, and calls neither
mutation tool. The rejected state survives another connection; another claim
for the same decision does not succeed. The database remained running during
the process failure. This proves post-commit process recovery, not a crash
during an MCP execution, an uncommitted write, database failure or Kubernetes
failover. No model API or production operation was used.
Another recovery test commits an approval claim, resumes approval, then waits
inside a deliberately non-mutating simulated rollback until the owned test
subprocess is killed with SIGKILL. The tool's readiness marker explicitly states
no side effect occurred. A fresh real API/checkpointer recovers `mitigating`,
the named approval and retained evidence, not `resolved`. Approval POSTs with
both the original and a new key return 409 without graph resume. Although the
incident repository still has its older waiting-approval snapshot, a new claim
using that snapshot is refused by the durable incident-operation lock.
This proves duplicate-execution protection across this simulated interruption;
it does not verify a real Kubernetes mutation outcome or exactly-once delivery.
The executing node's uncommitted events are not retained in the graph checkpoint,
so the complete dispatch/outcome audit is still incomplete for this crash path.
The recovered state remains mitigating and requires external outcome review;
no automatic reconciliation/lease recovery was added by this test.
The next revision splits `prepare_execution` into a separate graph node before
dispatch. Sync durability commits the intent's tool, arguments, operator and
idempotency key before scheduling the mutation node. Its dispatch status is
explicitly unconfirmed and outcome unknown; the signed receipt is issued only
at dispatch and is not stored in the intent/checkpoint. Dispatch rejects an
absent or mismatched intent. Six regression cases verify a pre-dispatch history
snapshot, checkpoint-write failure with zero tool calls, and missing/changed
actor/arguments/key bindings. The real SIGKILL-during-simulated-execution test
now verifies this intent survives in the recovered API trace. Full PostgreSQL
regression and both synthetic benchmark runners pass. This closes the missing
pre-dispatch intent audit, not the dispatch acknowledgement/outcome gap: no
real Kubernetes operation, automatic reconciliation or exactly-once guarantee
was tested. Old in-flight checkpoints without matching intents must not be
directly replayed; review their outcomes manually.
MCP remediation cache lookup now follows scope and receipt validation instead
of bypassing them on a key hit. Each cached key is bound to tool, full operation
arguments (including namespace and dry-run mode), MCP principal, approval actor
and incident. Refreshed signed receipt bytes are excluded from the binding and
are verified on every execution-mode request. Twelve regression cases cover
reader/missing/expired receipt denial on a cached success, conflicting actor,
incident, service, revision, namespace, mode or tool, identical authorized replay
with one backend call, and dry-run read-scope checks. No receipt token is stored
in the binding. This is process-local cache integrity, not a durable operation
status store or proof of cross-replica payload binding; Kubernetes's persisted
key-only marker needs a separate audit. Read-only outcome reconciliation has
not yet been implemented.
Kubernetes remediation now persists an operation fingerprint beside the key.
The fingerprint binds tool, service, namespace and rollback revision plus a
hashed MCP binding containing principal/approval actor/incident and parameters;
no signed receipt is stored on the resource. Same-key markers with missing or
conflicting fingerprints fail closed before PATCH, including the conflict-retry
path. Seven new tests use an in-memory mock Kubernetes API with fresh registries
and remediators to verify matching replay is GET-only, changed actor/incident/tool
is denied after one original PATCH, legacy markers do not trigger reexecution,
and all operation fields affect the fingerprint. No live cluster was used.
This is matching of the currently retained Deployment marker, not an append-only
operation history: later actions can overwrite that marker. It does not prove
exactly-once semantics, rollout health, historical operation status or real
cross-pod deployment behavior. Read-only outcome reconciliation remains pending.
The MCP server now exposes `query_operation_status` with a strict action-target
schema and operator scope, despite its read-only annotation. It reconstructs
the original parameter/approval binding and uses only GET to inspect the latest
Kubernetes Deployment marker. Results distinguish matching, absent/overwritten,
legacy-unverified and conflicting markers; they never claim rollout health or
return an `executed` flag. Without Kubernetes, ephemeral cache queries return a
recorded response/conflict or unknown, explicitly non-durable. Ten tests cover
the four marker cases, fresh-registry binding interoperability, scope denial,
schema target/extra-field rejection and empty cache uncertainty. The tool never
dispatches or retries remediation. It has not yet been wired to incident API
reconciliation/UI and does not resolve an incident automatically. All Kubernetes
evidence in this revision comes from mock API requests, not a running cluster.
Competing-pattern tests now retain both CPU saturation and database pool
exhaustion instead of selecting the first matching rule as the sole cause.
Candidate patterns and their individual metric citations survive checkpoint
retrieval. Ambiguous cases propose advisory-only investigation, including when
a configured generator supplies an explanation; no restart or rollback is
called. A local browser run using synthetic timestamped observations verified
both hypotheses, an unresolved-causal-order notice, no confidence percentage,
and no approval controls. This run did not call a model API. The 150-case
synthetic single-fault workflow benchmark was rerun with no failures, zero
execution attempts and zero model calls. These results are not live-fault or
production root-cause accuracy measurements. Frontend typecheck, production
build and all 26 frontend tests pass.
Citation validation now covers every retained hypothesis, not only the leading
one. Regression tests reject missing citations, insufficient citations,
unavailable observations, and knowledge-only alternatives even when the first
hypothesis is valid. A configured-generator workflow test confirms an invalid
second hypothesis stops the graph before remediation and no mutation tool is
called. Successful validation records each hypothesis's citation IDs and the
specific structural checks in the trace, explicitly marking semantic
entailment as unverified. The synthetic 150-case benchmark was rerun after
this change; it does not establish semantic faithfulness or causal accuracy.
Rejected hypothesis citations now produce a durable `insufficient_evidence`
result rather than escaping as an HTTP 400 with no returned incident ID.
The graph retains observations and a rejection event with candidate ranks and
citation IDs, clears unvalidated conclusions, skips remediation, and completes
without an approval interrupt. API regression verifies creation, checkpoint
retrieval, repository persistence, terminal SSE, and HTTP 409 for approval of
this state. Input policy violations still return HTTP 400 before a diagnosis
starts. No model API or production execution was used for these tests.
This iteration replaces absolute
memory-size classification with a sustained-growth signal. Regression cases
cover stable high memory, cache-warming plateaus, temporary spikes, GC drops,
insufficient samples, material growth, and malformed individual samples. The
MCP transport test checks a five-minute memory window at five-second steps;
other metrics retain the 30-minute window at one-minute steps.
Boundary regression tests also verify mixed valid/invalid metric responses,
finite JSON serialization, empty/all-invalid series, and rejection of malformed
series containers. Empty metric results explicitly report missing data.
No-data workflow tests cover generic and explicit fault questions, retained
runbooks/traces, skipped model generation, no destructive calls, checkpoint
recovery, API persistence, and terminating SSE. The console production build
and both existing rendering tests also pass after removing the empty-result
fallback to the sample root cause and confidence.
Execution-outcome tests exercise both restart and rollback with rejection,
contradictory flags, string booleans, missing execution confirmation, pending
execution, and explicit success. They verify checkpoint retention and completion
trace flags. An API test verifies failed execution is persisted, streamed as a
terminal state, and cannot trigger another call via duplicate approval.
Dry-run tests cover restart and rollback responses with false, string, numeric,
null, and missing acceptance flags. They verify a single dry-run call, no
approval interrupt or execution step, an explicit blocking trace, and durable
failure state.
Rollback-target tests cover missing/invalid metadata, current-revision targets,
other-service and knowledge-document records, zero-score evidence, missing
targets on newer deployments, and conflicting records. The accepted case
confirms that the newest primary-service record supplies the actual dry-run
revision rather than the removed hard-coded `141` fallback.
Generic-question tests retain normal telemetry and unexplained latency spikes
without producing a default database diagnosis. A configured model can still
investigate an unclassified signal; its grounded hypothesis completes with
advisory-only remediation and no restart/rollback call.
Grounding tests reject empty/invalid metric samples, zero-score observations,
empty log/trace events, and knowledge/deployment-only citations. Valid metric
or log evidence paired with independent operational knowledge still passes.
API response-loss tests inject MCP errors and timeouts after approval, verify
the pending state and original idempotency key are retained, check SSE
termination, reject duplicate approval, and assert a single execution call.
Sensitive error text and approval receipts are absent from the retained trace.
Approval concurrency was verified on 2026-10-02 using a separate local
PostgreSQL 14 instance listening only on a temporary Unix socket. Two
independent repositories and 20 distinct approval keys produced exactly one
successful incident-operation claim. Tests also verified ownership-preserving
release, global key reuse rejection, and independence of different operations.
Six concurrent API approvals forced to read the same paused snapshot produced
one successful response, five HTTP 409 conflicts, one graph resume, and one
rollback call. The temporary database server was stopped after verification.
Anomaly-window tests verify actual sample onset/end, separate bursts, transient
spikes, malformed/future/naive timestamps, non-finite values, duplicate and
unordered samples, missing-scrape gaps, and memory trends. A workflow test
checks the exact derived window and evidence link survive checkpoint retrieval.
Approval transaction tests verify an actual PostgreSQL NOT NULL failure during
audit insertion rolls back both claims, and decision bindings survive release
without permitting a new actor or approve/reject value for the same key. An API
database-error test checks HTTP 503, retained waiting-approval state, no graph
resume, and redacted database diagnostics. The temporary server was stopped.
Metric timestamp tests cover stale/future/missing/malformed and naive times,
chronological normalization, duplicate and conflicting samples, actual
observation time, and the stricter five-minute memory query boundary.

Browser acceptance on 2026-10-02 exercised the real FastAPI/LangGraph path
with two explicitly injected local fixtures and no model calls. Empty metrics
and logs produced `insufficient_evidence`, unavailable metric panels, no
confidence percentage, and no approval controls. A rejected rollback dry-run
produced `remediation_failed` and a blocking notice without approval controls.
The console now renders retained workflow/tool events and citation counts,
instead of keeping demo timing, metric curves, owner, health colors, and running
state in backend results. Numeric charts require retained timestamped samples;
synthetic narrative evidence alone does not populate them. Demo placeholders
remain explicitly labeled before a backend result is loaded.
Production build and both rendering tests pass. The initial TypeScript check
reported missing Cloudflare worker declarations. A subsequent iteration added
official Wrangler-generated runtime declarations and an optional D1 binding
augmentation; `npm run typecheck` now passes without incremental artifacts.
Wrangler's `--check` confirms the declarations match `worker/types.wrangler.json`.
The config is only for type generation, not deployment, and the generator uses
`--include-env false --env-file /dev/null` to avoid embedding private env values.

A subsequent browser test injected a ten-second delay into simulated rollback
execution. While approval was in flight, the question input, Analyze, Reject,
and Approve controls all had `disabled` attributes. The completed response
rendered `resolved`, removed approval controls, and did not offer a reset for
the immutable decision. This verifies simulation, not a production rollback.
An immediate ref-based request lock also protects against clicks before React
rerenders; same-incident, same-decision retry attempts reuse their idempotency
key while the console is mounted. Cross-page refresh safety remains enforced
by the backend's incident-operation claim, not by browser memory.

Approval-response recovery now uses one POST followed by a read-only GET after
any missing, invalid, or non-success response. Non-terminal or unavailable
status keeps mutation controls locked and offers only manual status refresh;
no automatic POST replay occurs. Terminal display comes from retained backend
status, not the decision the browser attempted to send. Ten transport-level
tests cover success, each execution terminal state, conflicts with a retained
rejection, stale waiting state, failed GET, wrong incident/unknown status, and
eventual recovery. `npm test` now runs type-checking, production build, and all
12 frontend tests; all pass.
Browser acceptance used the actual local workflow with simulated execution,
then injected HTTP 503 after the approval response and again for the first GET.
The UI hid both approval buttons and disabled new diagnosis; manual read-only
refresh recovered `resolved`. No production mutation or paid model call was
made. This first test concerned response recovery while mounted.

Browser-reload restoration subsequently added incident references to the URL
and writes an `approval_pending=1` marker before sending an approval POST. The
marker persists until a retained terminal status is read. Reload uses GET only,
restores the original question/evidence/trace, and keeps approval locked when a
pending operation is still paused or mitigating. The URL contains no credential,
receipt, command, evidence, or executable approval payload. Eight location and
restoration-policy tests cover link preservation, invalid IDs, conservative
marker parsing, ordinary paused incidents, ambiguous paused snapshots, and
terminal states; all 20 frontend tests, type-check, and production build pass.
Actual browser acceptance reloaded an ordinary waiting-approval incident and
correctly preserved its approval controls. It then approved a simulated
ten-second rollback and reloaded while execution was pending: the restored
state was `mitigating`, with no approval buttons and only read-only refresh.
Manual refresh recovered `resolved` and removed the pending URL marker while
keeping the same incident ID. This verifies page reload, not backend restart
durability, which is covered separately above.

Evidence-citation acceptance replaced the console's first-three-observations
preview with exact leading-hypothesis ID resolution, preserving citation order
without truncation and counting unique, resolvable IDs only. All retained items
are separately expandable with ID, kind, source, service, full observation,
timestamp, query, and original attributes; uncited items are labeled context
only. Missing or ambiguous cited IDs block console approval. Six regression
tests cover ordering, unrelated context, duplicates, missing/ambiguous IDs,
fragment escaping, and safe external source protocols. All 26 frontend tests,
type-check, production build, and scoped ESLint pass.
Browser acceptance inspected four actual citations in a 14-item synthetic
workflow response, followed a citation anchor, and expanded its historical
incident metadata. A second local fixture injected a missing cited ID into
the response: the page displayed the integrity error and removed approve/reject
controls. No approval POST, paid model call, or production execution was made.
This establishes citation identity/display integrity, not semantic causal
correctness of each hypothesis; that remains a diagnosis/evaluation concern.

Deterministic hypothesis selection now prioritizes the profile's specific
metric before a generic high-scoring symptom (pool utilization, CPU, memory,
lag, retry, cache misses, or release errors as appropriate). Primary-service
metric/log/deployment citations cannot be replaced by another service's records.
Retained log messages matching the profile signature outrank generic logs;
runbook context precedes generic propagation traces, and deployment regressions
retain deployment evidence within the leading four citations. Selection IDs,
profile, metric priorities, and service are recorded in the workflow trace.
Validation notes no longer assert time-window agreement or proven causation
without a corresponding causal check. Tests cover all eight profile priorities,
unavailable primary metrics, cross-service records, log signatures, deployment
ordering, stable ties, and persisted selection metadata. The backend grounding
guardrail also rejects cited IDs duplicated across retained observations. These
changes improve deterministic citation relevance and ambiguity handling; they
do not demonstrate new model accuracy or calibrated confidence scores, and the
historical credentialed evaluation has not been rerun.

Scoring-contract regression found that the policy/model NDCG implementation
used integer bit lengths rather than standard log2 discounting, and Recall@5
used the entire retrieved list. A shared metrics module now enforces a true
five-position cutoff and grants duplicate IDs relevance gain once only. Tests
cover exact rank-two discounting, duplicates, cutoffs, empty relevance, invalid
K, both aggregation paths, preserved usage, and rejection of duplicate or
mixed-model rescore inputs. All 99 Python tests pass with the isolated real
database configured, and scoped Ruff plus diff whitespace checks pass.
The offline rescorer processed all 450 historical outputs without credentials,
model calls, or overwriting the historical aggregate/JSONL. Its new result
includes a source SHA-256, scoring version, original model/revision, original
latency measurements, and explicit zero new calls. Scoring semantics and the
difference between supplied-context model comparison and actual LangGraph
execution are documented in `evals/README.md`; ID-based citation metrics are
not presented as semantic entailment or proven causality.

The actual-workflow snapshot benchmark then ran 150 cases through the current
LangGraph graph and real BM25 index with model generation disabled: 120 positive
cases (15 variants of each canonical fault pattern) and 30 healthy/no-data
negatives. All positive classifications, negative abstentions, primary-service
selections, and in-memory checkpoint round-trips matched their expectations.
All 75 destructive proposals interrupted for human approval and were rejected;
there were zero execution attempts. Full final incident states—including
evidence, hypotheses, decisions, and traces—are retained in
`evals/results/workflow_benchmark_cases.jsonl`. Aggregate results include
dataset/fixture/runner/workflow/profile/guardrail/corpus fingerprints and measured
local snapshot latency. Twelve tests cover fixture balance, all eight profiles,
both negative types, and a fail-closed adapter with no ground-truth label field.
This is a synthetic actual-graph regression, not a live fault-injection, MCP
transport, PostgreSQL restart, model-quality, or production-accuracy benchmark.

Boundary hardening aligned classification with the shared fault-lab anomaly
thresholds and requires two latest valid samples above threshold for pool,
CPU, consumer lag, retries, and cache-miss pressure. Boolean samples and
zero-score evidence are ignored. User-written failure labels no longer force
the actual-telemetry diagnosis; only explicitly marked narrative Demo fixtures
retain their documented fallback. Tests cover 20 metric boundary/transient
patterns, invalid observations, eight explicit but unsupported fault questions,
and full-workflow moderate-load/single-spike negatives. MCP integration fixtures
now provide scenario-specific observations behind the real schemas/transport
instead of relying on the old question-label override.
The 150-case benchmark was rerun with five normal, five moderate-pressure,
five single-spike negatives, and 15 no-data negatives. Classification and
abstention expectations remain met, all 75 destructive proposals are rejected,
and execution attempts remain zero. Artifact fingerprints now include the
shared thresholds. This does not replace the pending live Docker matrix or
establish general production SLOs; these thresholds describe the bounded lab.

Log-boundary regression now validates event containers, non-empty messages,
aware timestamps inside the requested 30-minute window, UTC normalization,
chronological ordering, and duplicate identity. Invalid numeric pool fields
cannot crash diagnosis; a zero maximum does not count as saturation. Evidence
reports valid retained events, not an unverified upstream match count, and its
observation timestamp is the actual latest retained event. All-invalid results
have zero score. Current deterministic log signatures use the latest five
minutes; older records retained in the query remain contextual rather than
independently triggering a destructive proposal. Nineteen new tests cover all
three diagnostic signatures with old/future/missing/naive/malformed timestamps,
valid signatures, boundary normalization, malformed containers, and an actual
workflow retaining old deployment logs without a rollback proposal. The
150-case snapshot benchmark was rerun successfully with timestamped fixture
logs. This current-log policy is a bounded lab heuristic, not a complete
historical-incident or semantic causality model.

A fresh live Compose matrix is pending. Both a full build and reuse of existing
project images were attempted, but the Docker data disk is full: the build
reported `no space left on device`, and PostgreSQL could not write
`postmaster.pid`. The partially started project stack was stopped without
deleting images, volumes, or data. Historical live results below remain dated
evidence and do not verify this new sampling policy.

## Earlier persistence verification

Verified locally on 2026-07-23 with real PostgreSQL 14, Redis 7, the HTTP MCP
server, the Go gateway, and two separate orchestrator processes.

## Restart recovery

1. Started the orchestrator with `AsyncPostgresSaver`, PostgreSQL incident
   repository, Redis task store, and HTTP MCP client.
2. Submitted a payment-service latency incident.
3. The graph stopped at `human_approval` with four evidence kinds and 15 trace
   events.
4. Stopped the orchestrator process entirely.
5. Started a fresh orchestrator process against the same PostgreSQL and Redis
   instances.
6. Loaded the same incident and trace ID from its LangGraph thread.
7. Submitted a named approval with a new idempotency key.
8. The orchestrator signed a short-lived receipt bound to the incident, actor,
   tool, service, and revision. The MCP server accepted the operator call and
   completed the simulated rollback.

Observed durable state:

| Store | Evidence |
| --- | --- |
| PostgreSQL `incidents` | one resolved incident at step `complete` |
| PostgreSQL `approval_decisions` | one `approve` decision by the named on-call |
| LangGraph `checkpoints` | 12 checkpoints for the same `thread_id` |
| Redis trace set | 21 ordered trace events |

The temporary database and Redis processes were stopped after validation and
the temporary PostgreSQL directory was removed.

## MCP to Go gateway

A second incident ran through the complete network path. Its retained evidence
sources were:

- `prometheus-via-telemetry-gateway`;
- `loki-via-telemetry-gateway`;
- `runbook://payments/db-pool-saturation`;
- `kubernetes-via-telemetry-gateway`.

The Go gateway emitted authenticated request traces for dependency, metric,
log, and deployment endpoints.

## Automated checks

```bash
.venv/bin/pytest services/orchestrator/tests services/mcp-server/tests \
  evals/tests fault-lab/app/tests -q -p no:cacheprovider

GOCACHE=/private/tmp/incident-copilot-go-cache \
  go test ./...  # from services/telemetry-gateway

npm test
docker compose config --quiet
```

## Observable fault-lab verification

On 2026-07-23 the full Compose override was built and started with Prometheus
3.12.0, Loki 3.7.0, Tempo 2.10.5, OpenTelemetry Collector 0.147.0, and Grafana
12.4.0. All eight fault scenarios completed the authenticated Go gateway
verification. The measured matrix is stored in
[`fault-lab/results/2026-07-23-observability-matrix.json`](../fault-lab/results/2026-07-23-observability-matrix.json).

The database-pool scenario returned 19 live Prometheus samples and 34 matching
Loki events in its isolated verification. A subsequent full Agent run created
incident `INC-68380D`, cited live Prometheus and Loki sources, ranked database
pool exhaustion after v142 first at 0.87 confidence, proposed rollback to v141,
and interrupted at human approval. The verification actor rejected the action;
the final trace recorded `executed=false`.

Tempo returned a single propagated trace containing spans from `checkout-api`,
`payment-service`, and `ledger-service`. Grafana loaded the provisioned
`incident-fault-lab` dashboard and reported a healthy database. The compact
Agent result is stored in
[`fault-lab/results/2026-07-23-agent-e2e.json`](../fault-lab/results/2026-07-23-agent-e2e.json).

On 2026-08-08 the generalized Agent workflow was rerun across all eight bounded
faults using generic service-level questions without fault labels. All eight
were classified correctly through the live orchestrator → MCP → Go gateway
→ Prometheus/Loki/Tempo path. Every incident retained live metric, log, and
trace evidence and a tool-call/result trace. Mean diagnostic latency was
103.32 ms and nearest-rank p95 was 143.67 ms. Five restart/rollback proposals
reached the approval gate, were rejected, and recorded no execution; three
advisory-only incidents completed as `diagnosed`. The full evidence and
per-check matrix is
stored in
[`fault-lab/results/2026-08-08-agent-scenario-matrix.json`](../fault-lab/results/2026-08-08-agent-scenario-matrix.json).

## Kubernetes deployment and remediation

The Kustomize base was rendered locally with kubectl 1.27/Kustomize 5.0.1. The
output contains namespace-scoped Services, Deployments, PostgreSQL StatefulSet,
Redis persistence, a PodDisruptionBudget, NetworkPolicies, and an MCP-only
Role/RoleBinding. The reusable base deliberately omits its Secret and uses
placeholder image tags and public origins.

The Kubernetes remediation adapter was verified with mocked Kubernetes API
responses:

- restart patches only a Deployment pod-template annotation;
- rollback first resolves the requested Deployment revision from a ReplicaSet,
  then patches the prior container images;
- a non-allow-listed namespace is rejected before network access;
- the backend is called only after operator scope and a signed approval bound
  to the exact tool, service, and revision;
- the idempotency key is persisted on the Deployment template and a
  `resourceVersion` precondition prevents two MCP replicas from applying the
  same operation concurrently.

On 2026-08-01 the local overlay was also deployed to an ephemeral Kind 0.32.0
cluster running Kubernetes 1.36.1. The first rollout exposed that the hardened
PostgreSQL container could not initialize a PVC mount root; the base was fixed
to run as the Alpine postgres uid/gid and place `PGDATA` in an owned
subdirectory. The final run brought PostgreSQL, Redis, telemetry gateway, MCP,
and orchestrator to Ready, verified remediation patch access was `yes` only in
`incident-copilot` and `no` in `kube-system`, created incident `INC-D1A075`,
and rejected its destructive proposal without an execution trace step. The
cluster was deleted by the verification trap. See
[`deploy/kubernetes/results/2026-08-01-kind-verification.json`](../deploy/kubernetes/results/2026-08-01-kind-verification.json).

The combined Python suite passes 40 tests and the Go suite passes. A
cluster-side `kubectl apply --dry-run=client` was not claimed: kubectl still
requires API discovery for these resources, and this workstation has no
configured Kubernetes context. Actual rollout remains an environment delivery
step after images and secrets are supplied.

## Incident knowledge retrieval

The MCP knowledge index contains 18 synthetic fault-lab documents across
runbooks, past incidents, and technical documents. A test query for every one
of the eight fault families retrieves its expected runbook in the top five.
Repeated searches return the same ordering and metadata, including stable URI,
update timestamp, matched terms, and SHA-256 content hash.

The MCP-to-LangGraph integration test confirms that the retained evidence now
includes metric, log, deployment, runbook, past-incident, and technical-document
kinds.

The executable retrieval benchmark ran the real index over all 150 generated
cases using service, incident question, and telemetry evidence labels—but not
the root-cause label. Its 2026-08-01 rerun measured 100% Recall@5, 97.9%
NDCG@5, 0.240 ms mean latency, and 0.317 ms p95 latency on this machine. The
checked-in result includes
per-fault slices plus dataset and corpus hashes:
[`evals/results/retrieval_benchmark.json`](../evals/results/retrieval_benchmark.json).

## Structured model reasoning

The OpenAI Responses API adapter is covered with a mocked HTTP transport. Tests
verify the strict JSON Schema request, structured response parsing,
response/token metadata capture, rejection of hallucinated evidence IDs,
LangGraph integration, and model-generation trace event. A separate
credentialed `gpt-5.6-terra` run on 2026-08-08 completed all 450 calls across
the 150-case single-prompt, RAG, and validated-Agent variants. The checked-in
result measured 19.3%, 64.0%, and 81.3% root-cause Top-1 respectively, with
0%, 99.3%, and 100% grounded-response rates.

The credentialed comparison runner is tested with a mocked Responses endpoint.
It refuses execution without an explicit cost-confirmation flag, requires both
credential and model ID, disables response storage, records token/latency
metadata, and checkpoints every completed case for retry-safe cost control.
# Current offline workflow run — 2026-10-03

The current [run summary](../evals/results/workflow_benchmark_20261003T003502Z_f3b9481a.json)
and [150 per-case records](../evals/results/workflow_benchmark_20261003T003502Z_f3b9481a_cases.jsonl)
exercise actual LangGraph and BM25 with credential-free snapshot tools after the
signal/action applicability changes. No model, MCP transport, PostgreSQL restart,
real fault injection or production mutation runs in this benchmark.

| Measured lab contract | Actual result |
| --- | --- |
| Rule fault classification | 120/120 canonical fault snapshots |
| Primary service selection | 150/150 |
| Scorer-only lab action policy | 150/150 |
| Healthy/no-data abstention | 30/30 |
| Signal applicability on destructive proposals | 75/75 allowed |
| Approval-paused proposals rejected without execution | 75/75 |
| In-memory checkpoint roundtrip | 150/150 |
| Execution attempts / model calls | 0 / 0 |
| Mean snapshot workflow latency | 6.88ms |
| Model root-cause accuracy / semantic faithfulness / causal efficacy | Not measured |

The 120 positives are eight canonical patterns with service/seed variations,
not 120 independent real incidents. Action labels are lab policy, not proven
mitigation efficacy. No malicious-citation rejection rate is inferred from this
clean fixture run. Latency excludes live provider/model/network costs. All
current source and dataset/fixture/corpus hashes plus the JSONL content hash are
saved in the summary; JSONL line count and hash were independently checked.

`evals/run_workflow_benchmark.py` now writes exclusive run-specific summary/JSONL
pairs and refuses collisions. It no longer replaces historical fixed-name
reports. Full isolated-PG regression: 672 passed, no skips, one existing
Starlette/httpx deprecation warning. Ruff and diff whitespace checks pass.
