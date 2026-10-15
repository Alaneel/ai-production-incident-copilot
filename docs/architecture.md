# Architecture decisions

## One orchestrated state machine

The system uses one explicit incident workflow rather than a group of agents
chatting with one another. Every node owns a typed input/output boundary and
emits trace events. Parallelism is limited to independent read operations such
as metrics, logs, distributed traces, incident knowledge, and deployment history.

The workflow is compiled with LangGraph. Every node update is checkpointed by
`AsyncPostgresSaver` under the incident ID as `thread_id`. `human_approval`
calls LangGraph `interrupt()`; the API resumes the same thread with
`Command(resume=...)`. A tested process restart between those two calls
preserves the evidence, trace, remediation proposal, and next node.

PostgreSQL also stores a canonical JSONB incident record, named approval
decisions, idempotency claims, tool audit records, and evaluation results.
Redis holds short-lived task projections and an ordered trace event set used by
the SSE endpoint.

The console's diagnosis stream uses a single authenticated
`POST /v1/incidents/stream`. It advances `astream` with synchronous durability,
but reads each state back from the saver instead of equating a speculative
stream update with a committed checkpoint. Each retained progress state is
saved to the canonical repository before its SSE snapshot is emitted. The
explicit complete event follows graph termination/approval pause and successful
canonical persistence; cache availability is not a completion requirement.
Disconnect cancels and closes the owned iteration without a background restart.
The saved prefix remains inspectable by read-only GET/history. This is
node-boundary progress, not token streaming or per-tool live log streaming.

## Evidence-first diagnosis

Every hypothesis stores stable evidence IDs. The output guardrail rejects a
leading hypothesis unless at least two retained evidence items from distinct
kinds support it. Evidence contains source, query, timestamp, service, relevance
score, and structured attributes so the console and postmortem can reproduce
the reasoning.
The citation guardrail also checks that each leading citation has a usable
observation: positive relevance, nonempty summary, and valid retained samples
or events when structured data is present. Empty series, non-finite values,
empty logs, and empty traces cannot count as corroboration. At least one cited
metric or log observation is required; knowledge and deployment metadata alone
cannot substantiate a root cause. Signal availability and deterministic citation
selection use the same validity predicate.
Anomaly windows are derived from timestamped finite samples rather than the
query lookback. Lab thresholds require at least two distinct abnormal samples;
healthy points and gaps longer than two minutes split intervals. Memory uses
the shared sustained-growth rule. Events retain per-metric intervals, evidence
IDs, thresholds, peaks, and sample counts. The incident-level window is the
envelope of those intervals, not a claim that every intervening sample is
abnormal. A left-censored interval means the first retained sample is already
abnormal, so true onset is unknown. No detected interval means a null window.
These bounded heuristics use fault-lab thresholds, not production SLOs.

Signal-based memory classification checks the most recent five finite samples.
It requires at least three samples, two increases, no decrease, and growth of
at least 1 MiB and 20% of the starting working set. A high but stable working
set, a single allocation jump, or a transient spike does not establish a leak
hypothesis. Invalid individual metric samples are skipped without discarding
valid samples from the same series. These thresholds are a bounded heuristic;
retained-object profiling is still needed to confirm a real memory leak.
The MCP client samples the last five minutes of memory at five-second steps;
other diagnostic metrics retain the 30-minute window and one-minute steps.
The live memory-fault verifier waits at least 25 seconds for distinct scrapes.
The MCP client normalizes numeric metric samples before evidence creation,
discards missing, malformed, boolean, and non-finite values, and records the
discarded sample count. Empty series are labeled unavailable with relevance
score zero rather than reported as a zero-valued metric peak. A non-list series
is a protocol error and is rejected explicitly.
Metric timestamps must be timezone-aware and inside the actual per-metric query
window. Future, stale, missing, and malformed timestamps are discarded. Samples
are normalized to UTC and sorted; identical duplicates count once, while
conflicting values at the same timestamp are all discarded. Evidence records
the final valid sample time as `observed_at` and retains query bounds plus the
discard count, rather than implying that an old sample was observed now.

If no finite metric samples or nonempty service log events are retained, the
workflow completes as `insufficient_evidence`. Retrieved runbooks, deployment
metadata, and trace summaries alone do not establish a root cause. This path
preserves evidence and an explicit abstention trace, skips model generation and
destructive tools (including dry-runs), and advises checking ingestion and the
service/time window before retrying. The console shows no root-cause hypothesis
or confidence claim; SSE treats this status as terminal.
For generic live questions, unmatched diagnostic signals do not default to
database pool exhaustion. Offline rules abstain with `insufficient_evidence`.
When a model is configured and real samples exist, it can investigate an
unclassified failure with grounded citations; retrieval uses the question
without appending the default database profile. An unclassified model result
receives investigative advice rather than a profile-derived destructive action.

The checked-in synthetic knowledge corpus contains 18 runbooks, past incidents,
and technical documents spanning all eight fault families. A deterministic
BM25 index returns stable source URIs, matched terms, update timestamps, and a
SHA-256 content fingerprint. This keeps local retrieval reproducible and lets a
future external document adapter preserve the same evidence contract.

## Bounded cross-service investigation

`services[0]` and `complaint_service` retain the user's first explicitly named
service. `incident_type` remains that service's local classification; a downstream
fault never silently replaces the complaint or an approved action target.
`diagnosis_service` is an investigation candidate, not a confirmed root cause.
It is null for multiple service/family candidates or unresolved diagnostics.

The workflow separately retains directed catalog edges and sampled CLIENT/SERVER
parentage. Fresh, consistent observed edges take priority over catalog fallback;
mixed paths are labeled explicitly. Unknown name-only dependencies, contradictory
directions, feedback cycles, stale/future samples and conflicting physical span
identities cannot establish an observed path. The investigation is bounded to
two hops, eight related services and four full downstream diagnostic queries,
with four concurrent reads and ten-second per-read bounds. Omitted names and
query failures remain visible; they are not evidence of health.

Each candidate requires current service-local fault signals. Remote candidates
also require a current complaint latency/error symptom window that overlaps the
local fault window. Metric units and ratio ranges are checked consistently in
primary classification, candidate assessment and action applicability; an explicit
wrong unit cannot become a destructive-action signal. Missing units remain a
bounded legacy-fixture compatibility case. Memory growth, thresholds and time
windows are lab heuristics, not calibrated production SLOs or probabilities.

The state retains local/symptom evidence IDs, candidate paths, path provenance,
per-service investigations and untrusted out-of-scope evidence as audit history.
Excluded observations cannot support a hypothesis or supply a rollback revision.
Metric MCP/gateway responses must echo the exact requested service and metric;
they cannot be relabeled into a requested evidence slot. The console independently
rechecks local signal support, current windows, exact path basis and citations
before rendering candidate metadata.

All remote-candidate outcomes remain advisory-only: no preflight, approval or
mutation is requested. Missing bounded downstream investigations, failed reads,
unavailable/oversized evidence, or unexplained current downstream anomalies also
block a primary-service destructive proposal. Coverage is recomputed at execution
boundaries rather than trusting a historical green trace flag. An investigated
intermediate hop may retain only symptoms when a more specific candidate exists
further along its path; that preserves a hypothesis, not causal authority.
`causal_direction_verified` is always false. Call direction plus temporal overlap
does not prove failure propagation, semantic entailment or mitigation efficacy.
Namespace-bound action identity does not make service-only telemetry queries
safe for multiple environments or tenants; namespace telemetry isolation remains
unfinished.

## Model reasoning boundary

When both `OPENAI_API_KEY` and an explicit `OPENAI_MODEL` are configured, the
hypothesis node calls the OpenAI Responses API with a strict JSON Schema. The
model sees a compressed evidence projection—not raw metric series or log
payloads—and retrieved text is labeled as untrusted data. Returned citations
must be a subset of retained evidence IDs, after which the normal multi-kind
grounding guardrail still runs. Provider, model, response ID, and token usage
are added to the workflow trace.

Without both settings, the node uses the deterministic fault-lab diagnosis.
This preserves an offline demo and makes it impossible to mistake an unexecuted
model configuration for measured model quality.

## Tool boundaries

The MCP server owns tool discovery and policy:

| Tool | Capability | Default |
| --- | --- | --- |
| `query_metrics` | `telemetry:read` | read-only |
| `search_logs` | `telemetry:read` | read-only |
| `query_traces` | `telemetry:read` | read-only |
| `get_service_dependencies` | `telemetry:read` | read-only |
| `search_runbooks` | `runbook:read` | read-only |
| `search_incident_knowledge` | `runbook:read` | read-only |
| `get_recent_deployments` | `telemetry:read` | read-only |
| `query_operation_status` | `production:execute` | read-only marker inspection |
| `query_deployment_health` | `production:execute` | read-only bound rollout snapshot |
| `restart_deployment` | `production:execute` | dry-run |
| `rollback_release` | `production:execute` | dry-run |

An execution request is rejected unless an HMAC-signed approval receipt is
unexpired and uses the v3 contract. Its signed payload carries incident and actor;
MCP checks tool, service, namespace, target, and the canonical target-context
SHA-256, then binds the signed approval
identity into execution/idempotency state. Restart retains `target=namespace`;
rollback retains `target=revision` but now also binds namespace separately.
Versionless, v2, namespace-less or context-digest-less receipts are refused before cached replay or
backend access, even with a fresh idempotency key. Deploy the signer and verifier
together; there is no default namespace or legacy-acceptance fallback. Existing
external receipts require renewed, scoped approval rather than field injection.
The API claims the approval idempotency key in PostgreSQL before resume; the MCP
server independently deduplicates the exact tool execution. Kubernetes-backed
actions also persist the key on the Deployment template and patch against its
`resourceVersion`, preventing duplicate execution across MCP replicas. A failed
approval/recovery call retains the recorded decision and operation lock;
uncertain transport/persistence outcomes require status inspection, not blind
resubmission with another key.

With a Kubernetes backend, each dry run reads a fresh Deployment and (for
rollback) a unique owned ReplicaSet, then submits the shared conditional patch
with `dryRun=All`. Admission errors, forbidden namespaces, missing identities,
ambiguous history and malformed responses fail closed without simulation fallback.
The normalized target context records service/namespace/tool, Deployment UID,
observed resourceVersion and full pod-template digest. Rollback additionally
records revision, ReplicaSet UID and target-template digest. `cluster_context`
fingerprints configured API origin and CA-file bytes, not an authoritative cluster
UID; credentials are excluded. Simulation instead explicitly reports
`preflight_mode=simulation` and a null context/digest.

The orchestrator validates preflight mode/backend and explicit boolean outcomes,
retains context in remediation arguments, the human interrupt and durable intent,
and compares it with the successful preflight trace before dispatch or inspection.
MCP verifies its signed digest before cache/backend access. Live execution and
retries re-read Deployment identity before marker replay; non-replays also reject
changed templates or rollback targets. Controller-only resourceVersion changes
use the latest conditional version; a 409 rechecks identity and never blindly
re-patches. Read-only status reconstructs the same context-bound fingerprint.
Legacy proposals need renewed diagnosis/preflight/approval, not field injection.

[Kubernetes server-side dry-run](https://kubernetes.io/docs/reference/using-api/api-concepts/#dry-run)
performs authorization/admission/validation without persisting the change.
It requires patch authorization and does not establish causal correctness or
mitigation efficacy. Current tests use MockTransport/in-process MCP, not fresh
live Kubernetes acceptance of this revision. Actual approved execution,
post-action recovery and complete release rollback remain unverified. The
adapter's rollback changes container images only, not the full pod template;
the approval preview states that scope rather than showing `kubectl rollout undo`.

Execution acknowledgement and recovery are separate. Boolean `accepted=true`
and `executed=true` alone now leave `remediation_pending`, including simulation.
Only a non-replayed real Kubernetes acknowledgement with its actual applied
generation/template digest can enter the read-only recovery branch. Those values
and the acknowledgement time must match the retained execution-result event;
the original approval, execution intent and preflight target binding remain
mandatory. Missing, malformed or lost acknowledgements and latest-marker replays
cannot invent the originally applied template or resume mutation.

`execute_or_simulate → verify_recovery → wait_for_recovery → verify_recovery`
is checkpointed independently of dispatch. A pending real observation pauses at
an explicit recovery interrupt. Operator-only `POST .../recovery-check` accepts
no body/query/target override and resumes only that waiting node; it never
re-enters execution or releases the original approval claim. PostgreSQL
transaction-scoped advisory locks cover fresh checkpoint inspection through
canonical save, excluding concurrent observers across API replicas. A four-slot
per-process bulkhead leaves headroom in the ten-connection repository pool.
Busy/capacity/unavailable states fail without a dispatch retry. The requester,
bounded reason codes, health snapshot and metric evidence are retained in trace.

Recovery requires a fresh GET of the same cluster configuration, namespace and
Deployment UID, a matching operation marker, the exact acknowledged generation
and template, an observed controller generation, and desired/total/updated/
ready/available replica counts all equal and positive. Zero unavailable replicas
may be omitted in the Kubernetes API's integer wire representation; other
missing counts are not fabricated as ready. These checks also reject a later
unrelated rollout. See the official [Deployment status fields](https://github.com/kubernetes/api/blob/master/apps/v1/types.go)
and [kubectl rollout completion checks](https://github.com/kubernetes/kubectl/blob/master/pkg/polymorphichelpers/rollout_status.go).

Latency and error SLIs must come from the live Prometheus adapter, with explicit
`data_origin=prometheus_api`, correct units, and post-acknowledgement timestamps.
The fixed trailing 60-second window needs at least three independent samples
per SLI with at least 30 seconds of shared healthy coverage, gaps/freshness no
greater than 30 seconds, p95 latency at most 1000 ms and error ratio at most .05.
These are lab thresholds, not production SLOs. Missing, discarded, conflicting,
future or unhealthy samples keep recovery pending; the gate does not select a
convenient green subsegment. Fixture/unknown origins cannot verify live recovery.
Only verified rollout and SLIs produce `resolved`; even that is observed recovery,
not proof that the action causally fixed the incident. Local mock/real-PG tests
cover this branch, not human-approved live Kubernetes action efficacy.

Explicit rejection of execution becomes `remediation_failed`; accepted but
unconfirmed requests remain `remediation_pending`. A non-object response also
retains an unknown pending outcome. Neither can be reapproved automatically.
Dry-run acceptance uses a strict boolean check. Advisory-only proposals have
`dry_run_passed=false` and no preflight mode because no preflight happened.
A failed, absent, or
malformed acceptance flag completes as `remediation_failed` with a
`remediation_blocked` trace. It never reaches the approval interrupt or an
execution call, and the console hides approval controls for that proposal.
Rollback targets come only from the latest positive-score deployment evidence
for the primary service. Missing or invalid previous revisions, a target equal
to the current revision, or conflicting records at the same deployment time
block the proposal before any dry-run. The workflow does not fall back to an
older record or a hard-coded revision, and knowledge documents cannot supply
the execution target.
If an execution call raises an MCP error or timeout, its outcome is retained as
`remediation_pending` rather than discarded through an API exception. The
approval actor, original idempotency key, call trace, and error type remain
checkpointed; raw error text and signed receipts are not retained in the trace.
The workflow finishes without automatically replaying the mutation. Operators
must verify the operation outcome before another execution attempt.
Approval claims are atomic at both key and `(incident_id, operation)` scope.
PostgreSQL stores the operation claim in a separate table with a composite
primary key and a cascading foreign key to the original idempotency record.
Both inserts share one transaction; a contender that loses the operation claim
removes its newly inserted key. Releasing an owned key also releases its claim,
while releasing another contender's key cannot unlock the winning operation.
This prevents concurrent operators or API replicas from resuming one incident
with different execution keys. Competing approvals receive HTTP 409.
The API claims approval and records the decision in one repository transaction.
An audit insert failure rolls back both claims, so execution never starts with
an unrecorded decision. The original key remains bound to incident, remediation,
decision, actor, and reason even after a claim is released; changing that
content returns a conflict. Database errors before graph resume return HTTP 503
without exposing raw database diagnostics.

## Telemetry gateway

The Go gateway isolates vendor APIs from the Agent layer. The production path is
orchestrator → MCP client → MCP policy server → Go telemetry gateway. The
gateway accepts bounded, typed queries and centralizes authentication, rate
limiting, timeouts, and request traces. This prevents model-generated raw
PromQL, LogQL, or TraceQL from bypassing policy and keeps telemetry credentials out of
the Python service.

The gateway additionally exposes read-only
`GET /v1/services/{service}/observed-dependencies` for an observability backend.
It searches the last 30 minutes, retrieves at most eight traces within one
five-second deadline, and retains at most 64 CLIENT→SERVER calls from exact
same-trace parent/span matches. Each call includes the trace ID, client/server
span IDs and server-span start time; it has no invented latency, error rate,
or causal-root field. Trace documents are byte-bounded and limited to 2,048
spans. Duplicate IDs, cycles, invalid IDs/timestamps, unrelated services and
unsupported kinds are excluded. Results disclose sampled/partial coverage,
failed trace reads and omitted calls. Empty sampled output is not evidence of
no dependencies. The fixture backend returns 501 rather than substituting
catalog relations. Provider failures are redacted and query overrides refused.
The existing `/dependencies` endpoint still serves the static fixture catalog.
The MCP `get_service_dependencies` tool concurrently reads that endpoint and
the observed endpoint with separate six-second budgets, preserving successful
data if either read fails. It labels catalog availability separately from
observed availability, strips arbitrary provider fields, and does not fabricate
observed data for fixtures/old adapters. The orchestrator validates observation
service/source, window freshness, coverage/count consistency, call budgets,
unique nonzero trace/span identifiers and timestamps. Ambiguous duplicate call
identities are all discarded, never resolved by first winner. Observed service
names are prioritized ahead of catalog-only names for bounded impact queries.
Exact normalized call references and observation metadata are retained in the
incident, trace and PostgreSQL checkpoints; `causal_direction_verified` remains
false. The console independently projects catalog and observed-call fields,
shows the retained UTC observation window, sampled/partial coverage and
read/failure/omission/discard counters. Per-service call details expose safe
trace and client/server span identifiers, without external links or new tool
requests. Empty samples and unsupported/unavailable reads do not imply no
dependencies. Refreshing an incident displays its retained observation window,
not a newly collected topology or current health assessment.

Wire-format references: [Tempo trace lookup API](https://grafana.com/docs/tempo/latest/api_docs/#query)
and [OpenTelemetry span schema](https://github.com/open-telemetry/opentelemetry-proto/blob/main/opentelemetry/proto/trace/v1/trace.proto).
Both Tempo legacy `batches`/`instrumentationLibrarySpans` and OTLP
`resourceSpans`/`scopeSpans` are covered by test fixtures, including hex and
protobuf-JSON base64 identifiers. This is tested compatibility, not a current
live Tempo-version verification.

## Fault lab

`compose.fault-lab.yml` adds four instrumented services, continuous traffic,
Prometheus, Loki, Tempo, an OpenTelemetry Collector, and provisioned Grafana.
Each service exposes the same bounded fault-control API and emits metrics,
structured logs, and W3C-propagated spans. Fault logs are batched directly to
Loki's push API, avoiding privileged Docker socket mounts.
Inbound request spans use SERVER kind; downstream requests use CLIENT kind
and inject context inside that client span. SDK exporter/HTTP transport tests
verify the remote server's parent is the outgoing client rather than the
upstream server. No live fault-lab rerun is implied by these tests.

When `PROMETHEUS_URL`, `LOKI_URL`, and `TEMPO_URL` are configured, the Go
gateway uses a strict metric allow-list plus bounded log and trace windows
against those live backends. The Agent never supplies raw PromQL, LogQL, or
TraceQL. Without all three values it falls back to deterministic fixtures,
keeping unit tests and offline demos reproducible.

## Trust boundaries

```text
untrusted user text
  → input guardrail
  → orchestrator
  → schema-validated MCP arguments
  → least-privilege tool scope
  → telemetry gateway

destructive proposal
  → dry-run
  → human review
  → signed approval receipt
  → exact-argument verification
  → idempotent execution
```

Retrieved knowledge and log content is data, never instructions. The retrieval
layer retains source boundaries and content fingerprints; retrieved content
cannot change tool permissions or system policy.
