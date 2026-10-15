# Repeatable incident fault lab

## Current release acceptance — 2026-10-14 UTC

The [current exact-image report](results/compose_platform_20261014T142444Z_82ec8643.json)
passes **39/39** checks: original real-pool failure/diagnosis/recovery and durable
HTTP/SSE restart behavior, both Prometheus interfaces, actual telemetry versions,
Grafana flags, oversized Tempo-limit rejection, browser-origin controls and
nonroot/cap-drop/no-new-privileges/read-only settings. All task-owned resources
were removed and pre-existing resource IDs preserved. Models and remediation
were not executed. This is one Compose fault family, not Kubernetes or all eight.

## Historical isolated merged-Compose acceptance

The [2026-10-07 actual run](results/compose_platform_20261007T131716Z_c3b86ddc.json)
passes all 21 checks, including live metric/log/trace evidence, actual pool
exhaustion/recovery, an advisory-only diagnosis and exact state after API restart.
Cleanup is confirmed. The first run remains a failed verifier-field report;
it was not rewritten after correcting `action` to the actual `tool` contract.

`verify_compose_platform.py` combines the base, fault-lab and real-pool Compose
files with `compose.acceptance.yml`. The final overlay removes every host port
and explicitly disables model credentials. Run only with locally built, current
images and all store images already cached:

```bash
.venv/bin/python fault-lab/verify_compose_platform.py --run-local-integration \
  --orchestrator-image incident-copilot/orchestrator:<your-fresh-tag> \
  --mcp-image incident-copilot/mcp-server:<your-fresh-tag> \
  --gateway-image incident-copilot/telemetry-gateway:<your-fresh-tag> \
  --lab-image incident-copilot/fault-lab:<your-fresh-tag>
```

The runner resolves local immutable image IDs and uses a fresh project UUID,
`--env-file /dev/null`, generated distinct test credentials, required API auth,
`up --no-build --pull never`, and container-internal HTTP probes. It checks that
no merged service publishes host ports before allocation. It starts the four
lab services, traffic generator, telemetry stores, Grafana, Go, MCP, orchestrator,
PostgreSQL/Redis and a separate Unix-socket pool database. It exhausts the real
pool, collects live evidence, creates a deterministic diagnosis through the
authenticated API, checks the advisory boundary, clears the injected fault,
then restarts the API and compares restored incident state exactly.

Only this generated project's resources are removed with `down --volumes`;
container/network/volume listings must confirm project-label absence. Its
ephemeral databases are disposable, while the diagnosis/trace and source/config
hashes remain in an exclusive dated report. Existing projects and built images
are retained. Startup errors are redacted, not written with credentials.
The runner does not read `.env.local`, invoke a model, approve/execute remediation
or claim real deployment provenance. Service startup alone does not verify
Grafana rendering or full monitoring behavior. It is one fault-family acceptance,
not the eight-case matrix, in-flight crash recovery, Kubernetes, model accuracy
or causal remediation efficacy.

## Opt-in real local PostgreSQL pool experiment

Current remote remediation qualification: deployment metadata remains synthetic
even when telemetry is live. It cannot select a live rollback revision. Current
workflows retain pool diagnosis but return a read-only deployment-history
investigation, not rollback dry-run/approval, until observed deployment metadata
for the correct namespace is available. The native verifier checks that explicit
gate. A fresh current-code native real-pool run now verifies it; historical
approval-paused reports below remain dated evidence, not proof of the current
recommendation.

The [2026-10-03 native real-pool report](results/native_scenario_db-pool-exhaustion_20261003T021520Z.json)
passes all 24 checks in 90.319 seconds. Four actual PostgreSQL sessions retain
the same server PIDs across 0/4 → 4/4 → 0/4 leased connections; direct work
requests change from exhausted HTTP 503 to recovered HTTP 200 after the explicit
test-only clear. Prometheus/Loki/Tempo, the current Go gateway and actual MCP HTTP
feed the compiled graph. Its diagnosis-stage five-minute sample projection is
retained with simulation compatibility disabled, seven eligible pool samples
and 59 log events; it recognizes pool exhaustion but proposes only investigation
because the deployment catalog is synthetic. No rollback dry run, approval or
execution occurs. A fresh tool-free process recovers exactly matching PostgreSQL
state/repository records and nine checkpoints. The legacy check name
`approval_interrupt_retained` means the expected terminal graph position is
retained here, **not** that this advisory incident had an approval interruption.

All recorded workload/orchestrator/MCP source hashes match the current tree.
The gateway was rebuilt from current local source with `-buildvcs=false` because
the system Git cannot pass the Xcode license gate; Go tests pass. Four fresh
cached-image/tmpfs observation containers and their unique network were used
and verified absent afterward; zero application-pool sessions remained. No
existing Docker data, images or volumes were removed. This closes current native
real-pool/telemetry/checkpoint acceptance, not packaged Docker/Kubernetes runtime,
model accuracy, semantic faithfulness, expert calibration or mitigation efficacy.
The 0.132-second workflow time is deterministic/no-model, not LLM latency.

### Packaged service runtime acceptance — 2026-10-07

A fresh `fault-lab/app/Dockerfile` build now succeeds; the earlier disk-space
failure did not recur. The isolated packaged-service verifier passes
[22 runtime checks](results/packaged_pool_runtime_20261007T131143Z_e93e8c82.json),
including image-contained source hashes matching the worktree, uid 10001,
read-only root filesystem/socket mount, no published ports, PostgreSQL tmpfs data
and no TCP listener. Four actual PG sessions move through 0/4 → 4/4 → 0/4 leased
connections, with HTTP 200 → 503 → 200 and the same backend PIDs. Unauthorized
fault injection is rejected. Graceful payment shutdown closes all four sessions.

The verifier only creates uniquely named experiment containers, an internal
network and socket volume, and verifies their absence afterward. The image is
retained. It does not use `.env.local`, invoke a model or touch existing Docker
resources. To repeat after building a fresh uniquely tagged image:

```bash
.venv/bin/python fault-lab/verify_packaged_pool.py \
  --run-local-integration --image incident-copilot/fault-lab:<your-fresh-tag>
```

Images must already exist locally; the verifier resolves immutable image IDs
and never pulls or builds. Results use exclusive, run-specific filenames with
source/image identities. Failed startup still attempts cleanup of only the
newly allocated names; daemon/permission errors cannot count as confirmed
absence. Five offline verifier tests cover preflight, failure cleanup and error
redaction. This closes **packaged payment/PG resource-fault acceptance only**;
it does not exercise the merged Compose stack, four-service telemetry/Go/MCP
workflow, model reasoning or a Kubernetes cluster.

### Compose configuration (full-platform runtime verification pending)

`compose.real-pool.yml` is an explicit overlay after the two existing Compose
files. It adds a **dedicated** ephemeral `lab-pool-postgres`, sharing only a Unix
socket volume with payment-service; it does not reuse the orchestrator database
or permit remote database targets. The server has no TCP listener/published port.
Only payment-service changes to real mode; the other services and telemetry
settings are retained by the merge. Supply fresh non-default `FAULT_INJECTION_TOKEN`
and `INCIDENT_LAB_POSTGRES_PASSWORD` values, then use:

```bash
docker compose --env-file .env.local \
  -f docker-compose.yml -f compose.fault-lab.yml -f compose.real-pool.yml \
  up --build -d
```

Use a dedicated test Compose project and inspect existing resources before
starting; this command is a user-operated launch, not an automatic cleanup.
The overlay requires both values and the application refuses the default fault
token. Payment-service runs as uid 10001/group 70 and mounts the socket read-only;
PostgreSQL's socket permission is 0770. Local socket trust is the experiment's
access boundary, not password auth. `incident_lab.start` validates scope/token,
performs bounded read-only readiness retries, then execs the server. PostgreSQL
data is tmpfs; restarting its container loses this disposable experiment only.

The separate [Kubernetes packaging experiment](../deploy/kubernetes/experiments/real-pool/README.md)
uses an isolated namespace and socket-sharing sidecar. It is not yet the full
four-service telemetry/Agent Kubernetes deployment. Compose merging and Kubernetes
rendering pass tests. The earlier image build failed on Docker disk exhaustion;
the new isolated service build/runtime success above does not establish merged
Compose or cluster acceptance. User-owned Docker data was not removed. See
[Compose merge semantics](https://docs.docker.com/reference/compose-file/merge/).

### Native resource and telemetry verification

The default remains bounded simulation. A new native-only experimental mode
creates four actual PostgreSQL sessions and uses measured lease counts for pool
gauges. Healthy `/v1/work` requests execute fixed read-only `SELECT 1`; the
`db_pool_exhaustion` injection reserves all leases, so subsequent requests wait
up to 0.5 seconds and return a real acquisition timeout. Explicit clearing or
the bounded expiry timer releases leases; shutdown closes all sessions. This is
application-pool exhaustion, not exhaustion of the server's `max_connections`.
It never changes database schema, rows or server configuration.

Use only a **dedicated local test database**, with a non-default fault token:

```bash
INCIDENT_LAB_DB_POOL_BACKEND=postgres \
INCIDENT_LAB_POSTGRES_URL='<explicit dedicated local PostgreSQL URL>' \
FAULT_INJECTION_TOKEN='<random local experiment token>' \
PYTHONPATH=fault-lab/app \
  .venv/bin/python -m uvicorn incident_lab.app:app --host 127.0.0.1 --port 8095
```

Only explicit loopback hosts or `/private/tmp/` / `/tmp/` Unix socket targets
are accepted. Remote/implicit/multi-host targets and libpq service/host-address
overrides (including environment overrides) are rejected before connecting.
The pool is limited to four sessions and read-only statements; tokens are
required before fault injection/clear. Existing `/admin/fault` and
`DELETE /admin/fault` use `x-fault-token`; a pool fault's duration remains bounded
by the existing 10–900 second request contract. `/health.db_pool_backend`
distinguishes `postgres` from `simulation`, without exposing the database URL.

Local tests verify four distinct server PIDs in `pg_stat_activity`, acquisition
timeout, clear/automatic expiry, partial-reservation cancellation, metrics and
HTTP recovery, and closed sessions on shutdown. This lab-only small pool has no
production reconnect/replacement policy. The isolated Docker service is now
verified above; full-platform Docker/Kubernetes runtime remains unverified.
Historical matrices must not be reclassified as real PostgreSQL
resource-fault acceptance. The native verifier defaults to simulation and strips
the real database URL unless explicitly opted in for this single scenario.

With isolated live telemetry stores already running, opt in as follows (use
the disposable-store wrapper described below when no stores are running):

```bash
INCIDENT_TEST_DATABASE_URL='<explicit dedicated local PostgreSQL URL>' \
  .venv/bin/python fault-lab/verify_observed_topology.py \
    --run-local-integration --gateway-binary /absolute/path/to/gateway \
    --live-telemetry --verify-log-correlation --scenario db-pool-exhaustion \
    --checkpoint-postgres --real-postgres-pool
```

For a self-contained single run with **new owned observation stores**, use the
matrix runner's explicit selector instead of manually starting containers:

```bash
INCIDENT_TEST_DATABASE_URL='<explicit dedicated local PostgreSQL URL>' \
  .venv/bin/python fault-lab/verify_native_matrix.py \
    --run-local-integration --gateway-binary /absolute/path/to/gateway \
    --scenario db-pool-exhaustion --checkpoint-postgres --real-postgres-pool
```

Build the gateway from the current source first. The runner requires all four
cached observation images and unused experiment ports; it never pulls images
or reuses user containers. Real pool mode rejects omission of the single pool
selector/checkpoint flag before Docker work, then validates the explicit local
database. Other single simulated cases can use `--scenario <name>` without the
real-pool flag; omitting `--scenario` retains the eight-case simulated matrix.
Real pool mode is not allowed for an entire mixed-scenario matrix.

Single-run aggregate reports use fresh exclusive
`native_scenario_single_<UTC>_<nonce>.json` names, retain the selected cases,
image/config/source/gateway fingerprints, hashed per-case artifacts and cleanup
status. `ok=true` means the selected run passed; `selection_mode=single_case`
and `full_matrix_completed=false` explicitly prevent an eight-case claim.
The aggregate requires the PostgreSQL backend and all five real-resource
checks, as well as ordinary/recovery checks, zero model calls and no production
actions; a simulated or incomplete child report cannot count as real-pool success.
Reports are checkpointed while cases run, preserving earlier run files.

The [self-contained single-run aggregate](results/native_scenario_single_20261003T022707Z_02ec1fa5.json)
now passes with the new command: one selected case, real PostgreSQL backend,
all 24 child checks, hashed artifact and `cleanup_complete=true`, in 103.329
seconds including stack lifecycle. The [child report](results/native_scenario_db-pool-exhaustion_20261003T022839Z.json)
takes 89.917 seconds, recognizes pool exhaustion, and remains advisory because
the rollback catalog is synthetic. Source/config hashes and the child artifact
hash were checked against the saved files; owned containers/network were
verified absent afterward and zero application-pool sessions remained. This is
single-case native acceptance, not a fresh eight-case matrix, packaged runtime,
live Kubernetes or model/semantic correctness result. Full Python regression
passes 786 tests without skips, with one existing warning.

This enables real leases only on the owned payment service, using the same
dedicated local target as checkpoints. It inspects token-protected, read-only
`GET /admin/pool` snapshots (backend, counts and PIDs; no connection URL), checks
those exact PIDs in `pg_stat_activity`, requires 4/4 leases and HTTP 503 **after**
injection, and verifies explicit clear produces HTTP 200 with the original
sessions. The incident must retain a real utilization=1 sample and timeout log
with active=max=4. Diagnosis remains deterministic and advisory-only when the
rollback target is unverified; no model
request or restart/rollback executes. This verifier is not a semantic RCA
accuracy benchmark.

The historical [real-pool report](results/native_scenario_db-pool-exhaustion_20261002T232101Z.json)
passes all 23 checks in 90.130 seconds, including independent-process PostgreSQL
state/history/approval-interrupt recovery. The first
[failed sampling report](results/native_scenario_db-pool-exhaustion_20261002T231835Z.json)
is preserved: its exhausted snapshot incorrectly ran before injection. The
verifier now samples after injection; the successful report is a fresh run,
not a rewritten artifact. No model call or remediation execution occurred.

The implementation uses asynchronous connections/cursors following the
[Psycopg concurrency documentation](https://www.psycopg.org/psycopg3/docs/advanced/async.html).

The latest native PostgreSQL batch passes all eight canonical scenarios and
144/144 checks in one fresh run (801.413 seconds):
[`native_scenario_matrix_20261002T205334Z.json`](results/native_scenario_matrix_20261002T205334Z.json).
It verifies exact log/span references, real Prometheus/Loki/Tempo→Go→MCP
telemetry, deterministic diagnosis and separate-process checkpoint/repository
recovery. Five proposals stay approval-paused; three return read-only guidance.
No model requests or production actions ran. This is bounded fault simulation,
not model accuracy or live API crash/Redis/approval-resume proof; see
[verification scope](../docs/verification.md). Earlier failed runs remain intact.

The lab runs four instrumented services:

```text
checkout-api → payment-service → ledger-service
                         └──────→ risk-engine (catalog dependency)
```

The services expose Prometheus metrics, batch structured JSON logs directly to
Loki's push API, and export OTLP traces through the OpenTelemetry Collector into
Tempo. Grafana is provisioned with all three data sources and an incident
dashboard.

## Start

```bash
docker compose -f docker-compose.yml -f compose.fault-lab.yml up --build
```

Endpoints:

- OpsPilot API: `http://localhost:8080`
- telemetry gateway: `http://localhost:8081`
- checkout traffic entrypoint: `http://localhost:8090`
- Grafana: `http://localhost:3001`
- Prometheus: `http://localhost:9090`
- Loki: `http://localhost:3100`
- Tempo: `http://localhost:3200`

## Live trace-parentage probe without the full lab

`verify_observed_topology.py` starts three native fault-lab services on localhost
8191–8193, a compiled Go gateway on localhost 8081, and MCP on localhost 8003.
All five ports must be unused. It needs the actual Collector (OTLP 4318) and
Tempo (HTTP 3200) already running, but no PostgreSQL, Redis, Prometheus or Loki
process. For an existing stopped Compose lab, starting only its Tempo and
Collector containers is sufficient. Do not start or stop user-owned running
services just to satisfy the probe; the runner refuses occupied local ports.

```bash
GOCACHE=/private/tmp/copilot-go-build go -C services/telemetry-gateway build \
  -buildvcs=false -o /private/tmp/copilot-live-parentage-gateway ./cmd/gateway
.venv/bin/python fault-lab/verify_observed_topology.py \
  --run-local-integration \
  --gateway-binary /private/tmp/copilot-live-parentage-gateway
```

The explicit gate permits one normal checkout request and bounded polling for
its unique trace. It does not inject faults, read `.env.local`, call a model,
submit approval, or execute remediation. Temporary credentials are generated
for the gateway/MCP and are not printed or saved. Child processes are always
stopped in `finally`; externally supplied Tempo/Collector are not managed by the
runner. Stop them afterward only if you started them from a stopped state for
this test. `TELEMETRY_GATEWAY_ADDRESS` permits the native gateway's localhost
binding; its ordinary default remains `:8081`.

The result retains actual CLIENT→SERVER parentage through native workload HTTP,
OTLP, live Tempo, actual Go and MCP HTTP, and a LangGraph memory checkpoint.
Metrics, logs and deterministic reasoning remain synthetic for this narrow
probe. A successful result is not an eight-fault diagnosis benchmark, model
evaluation, PostgreSQL recovery test, or causal-root proof. The 2026-10-03 local
run found both checkout→payment and payment→ledger calls in 17.197 seconds:
[`observed_topology_live_20261002T165821Z.json`](results/observed_topology_live_20261002T165821Z.json).

The optional `--live-telemetry` mode also requires isolated Prometheus on 9090
and Loki on 3100. It uses the actual MCP adapter for all telemetry, not Demo
metrics/logs. Configure Prometheus to scrape `host.docker.internal:8191`,
`:8192` and `:8193` once per second when running it in Docker Desktop alongside
the native services. Use fresh test-only storage per run: the gateway's
30-minute window and eight-trace search budget can retain prior samples and
exclude a newly generated trace. Never reset unrelated user observability data.
`--fault-cpu-spike` additionally injects a 45-second, intensity-0.95 fault only
into the newly launched localhost payment process, after collecting 12 seconds
of baseline traffic. It requires both explicit local integration and live
telemetry flags. Native process shutdown clears the in-memory fault state.

```bash
.venv/bin/python fault-lab/verify_observed_topology.py \
  --run-local-integration --live-telemetry --fault-cpu-spike \
  --gateway-binary /private/tmp/copilot-live-parentage-gateway
```

Live-mode results use `native_live_telemetry_<UTC timestamp>.json` and persist
per-check pass/fail results, including failed diagnoses. Success requires real
metric samples, retained log events and trace summaries, observed call
parentage, no causal upgrade and no execution step; the CPU mode also requires
CPU classification and a waiting-for-approval state. No approval is submitted.
The CPU gauge is the experiment's configured fault-intensity proxy, not measured
host CPU utilization. Deployment/catalog metadata and knowledge documents remain
fixture-backed; reasoning is deterministic, model calls remain zero, and
checkpoint storage is still in memory. This single-case probe is not the full
eight-fault matrix or a model-accuracy benchmark.

The MCP adapter evaluates ordinary metrics every 15 seconds over 30 minutes
(at most 121 evaluation points), while memory growth retains its five-minute,
five-second query. Classification still requires multiple valid anomaly points.
The Go gateway omits Prometheus `NaN`/infinite values instead of replacing them
with zero; startup histogram gaps must not invalidate other finite samples or
produce a fabricated healthy observation.

### Canonical native fault cases

`--scenario` accepts all eight names from `scenarios.json`, requires
`--live-telemetry`, and cannot be combined with the older `--fault-cpu-spike`
flag. It launches four owned localhost services on 8191–8194 in the chain
checkout→payment→ledger→risk. This experimental chain is observed through spans;
it is not substituted for the independent static catalog. Add port 8194 to the
isolated Prometheus scrape targets. All six native gateway/MCP/workload ports
must be unused before launch.

```bash
.venv/bin/python fault-lab/verify_observed_topology.py \
  --run-local-integration --live-telemetry \
  --scenario db-pool-exhaustion \
  --gateway-binary /private/tmp/copilot-live-parentage-gateway
```

The runner first verifies a unique healthy baseline trace, emits 12 seconds of
baseline requests, injects a 120-second intensity-1 fault into the canonical
service, then emits traffic for at least 30 seconds before diagnosis. The graph
is prepared before fault injection, and an additional bounded traffic task
continues through diagnosis so current memory trends cannot silently become
stale after stopping requests. That task is cancelled and awaited on success or
failure; immediate workload errors prevent diagnosis. HTTP
500/502/503/504 responses are counted instead of aborting expected fault traffic;
unexpected statuses and transport failures still fail the run. The question
contains only the service name, never the expected fault label. Per-case checks
require the expected incident type, a hypothesis, expected remediation and the
correct waiting-approval/advisory boundary. No approval is submitted. Healthy
baseline call edges are not required in the post-fault sample: an early failure
can prevent an outgoing call. Instead, the verifier retains the baseline proof
and compares post-fault topology with the memory checkpoint read.

Results are `native_scenario_<name>_<UTC timestamp>.json`, including failed
checks, injection time, HTTP status counts, total elapsed time and separate
workflow diagnostic latency. Each run needs fresh, isolated observability test
storage; do not sequentially reuse the same 30-minute/log window as a clean
matrix. The runner does not reset externally supplied stores. Eight supported
selectors are not proof that all eight cases passed. These faults remain bounded
simulations (including pool and Kafka gauges), not real database/Kafka resource
exhaustion, measured production CPU, model accuracy or durable recovery evidence.

Canonical cases deliberately use a valid leading-zero baseline trace ID with
120 random bits. Tempo can omit leading zeroes in search responses; the gateway
normalizes only this hexadecimal search boundary to 32 characters, retaining
strict OTLP byte widths and rejecting zero/malformed/oversized IDs. See Tempo's
[`left_pad_trace_ids` configuration](https://github.com/grafana/tempo/blob/main/docs/sources/tempo/configuration/_index.md).
Baseline parentage timeout now saves a `_baseline_failed.json` artifact with
normalized topology, polling count and no fault/approval/model execution; it
does not proceed to injection or diagnosis. Other startup/transport errors
remain safely redacted and exit nonzero.

### Isolated eight-case native matrix

```bash
.venv/bin/python fault-lab/verify_native_matrix.py \
  --run-local-integration \
  --gateway-binary /private/tmp/copilot-live-parentage-gateway
```

This opt-in runner preflights every workload/gateway/MCP/observability port and
requires all four image versions to be cached locally. It never pulls images,
starts existing containers, or deletes existing networks, images or volumes.
For each canonical case it creates a uniquely named private Docker network and
four new localhost-only `--rm` observability containers, with read-only checked-in
configs and tmpfs data storage. Each case gets new Prometheus/Loki/Tempo samples;
only these newly owned containers/network are stopped/removed afterward. The
existing Compose project may remain stopped. The included
`observability/prometheus-native.yml` scrapes all four native services once per
second through Docker Desktop's `host.docker.internal`.

Results are checkpointed after each case to
`results/native_scenario_matrix_<UTC timestamp>.json`, referencing hashed
per-case artifacts and recording image IDs, source/config/gateway hashes and
separate diagnostic/runtime timings. Success requires all twelve named case
checks, zero model calls, no production actions and successful owned-stack
cleanup. A failed case remains failed while other isolated cases can continue;
failed cleanup stops the batch. Probe timeouts terminate only the newly created
probe process group and its native children. Allow roughly 12–16 minutes for
eight cases, including startup, baseline/fault traffic and cleanup. This is a
deterministic eight-simulation integration matrix, not a 150-case model benchmark
or durable PostgreSQL recovery verification.

Port preflight uses `SO_REUSEADDR`, matching the native servers' restart
semantics, so a previous case's closed TCP `TIME_WAIT` sockets are not mistaken
for active services. `SO_REUSEPORT` is never enabled; listening sockets still
block startup. Interrupted cases are checkpointed as failures after owned
cleanup, then the matrix stops instead of presenting a partial batch as success.

The 2026-10-03 local run passed all eight cases and 96 checks in 818.743 seconds;
five proposals stayed awaiting approval and three returned read-only guidance.
No model requests or execution occurred. Mean deterministic workflow time was
0.107 seconds (excluding setup/warmup/cleanup). See
[`native_scenario_matrix_20261002T180456Z.json`](results/native_scenario_matrix_20261002T180456Z.json).
Earlier failed/incomplete matrices are retained separately. These measured
figures are not AI accuracy, semantic faithfulness or real production RCA claims.

### Exact log-to-span references

Native logs use the active OpenTelemetry context for `trace_id` (32 hex digits)
and `span_id` (16 hex digits); normal log extras cannot override them. Without a
valid span these IDs are omitted, not invented. `x-request-id` provides bounded
client correlation separately. The older `x-trace-id` header is accepted only
as a request correlation value, never as OpenTelemetry identity. Downstream
calls propagate W3C trace context and request ID independently.

Add `--verify-log-correlation` to a `--live-telemetry` probe to verify actual
Loki/MCP request-completed events against the unique baseline trace's retained
Tempo SERVER IDs, including target service and shared request ID. It is opt-in
and does not work with Demo logs. The no-fault 2026-10-03 probe passed all seven
checks in 58.305 seconds and correctly returned `insufficient_evidence` for the
healthy service:
[`native_live_telemetry_20261002T183144Z.json`](results/native_live_telemetry_20261002T183144Z.json).
The older eight-case matrix was not rerun after this log-identity revision.

The verifier now waits for log visibility with at most ten query rounds, one
second between rounds and a 15-second monotonic deadline checked between rounds.
Existing tool-call timeouts still apply; this is not a hard 15-second total
request timeout. Every expected `(service, SERVER span ID)` pair must match the
same unique trace and request ID, including multiple spans for one service.
Matches can accumulate across rounds but cannot come from a different baseline.
Missing evidence at the bound still fails the check.

`baseline_log_lookup` retains each round's event/match counts, redacted query
error types, missing exact span pairs and the native workload's delivery/retry/
drop counters. Cancellation propagates without retry. This distinguishes
immediate absence from observed later visibility without asserting that all
missing references are ingestion delays or relaxing identity checks.

A fresh memory probe with this verifier passed all 18 checks in 90.712 seconds:
[`native_scenario_memory-leak_20261002T205054Z.json`](results/native_scenario_memory-leak_20261002T205054Z.json).
Both exact log references were present on the first round, so this run does not
prove the previous absence was delayed ingestion. It retains the actual lookup
history and shipper counters rather than inferring a cause. The full eight-case
batch has not been rerun at this verifier revision.

### PostgreSQL checkpoints and fresh-process recovery

Add `--checkpoint-postgres` to a live native probe and supply
`INCIDENT_TEST_DATABASE_URL` for a **dedicated local test database**. The verifier
accepts an explicit loopback host or a Unix socket under `/private/tmp/` or
`/tmp/`; it rejects remote/implicit hosts and libpq service/address overrides.
It does not load the project's general database URL or print credentials.
This opt-in run creates checkpoint/incident tables and retains a new incident
record, so do not point it at production or a shared database.

With the isolated observability stack already running:

```bash
INCIDENT_TEST_DATABASE_URL='postgresql:///postgres?host=/private/tmp/copilot-approval-postgres.srVsg3&port=55439' \
  .venv/bin/python fault-lab/verify_observed_topology.py \
  --run-local-integration \
  --gateway-binary /private/tmp/copilot-live-parentage-gateway \
  --live-telemetry --verify-log-correlation \
  --scenario db-pool-exhaustion --checkpoint-postgres
```

Replace the example socket, port and compiled gateway path with your own local
test resources. After saving the diagnosis, the verifier closes the original
checkpoint connection and starts a separate recovery reader. That process checks
complete checkpoint/repository state, checkpoint history and the approval
interrupt. Its tool client rejects all tool access; it does not approve, resume
or execute. Repository initialization includes idempotent schema setup, not a
strictly read-only SQL session.

The 2026-10-03 single pool scenario passed all 18 checks in 105.298 seconds,
retaining 42 trace events, 25 evidence items and eight checkpoints while paused
for approval:
[`native_scenario_db-pool-exhaustion_20261002T185901Z.json`](results/native_scenario_db-pool-exhaustion_20261002T185901Z.json).
This uses a bounded fault simulation and deterministic reasoning, with no model
requests or production actions. It does not establish all eight durable cases,
Redis recovery, live API-server crash recovery, or approval/resume behavior.

The batch runner also accepts `--checkpoint-postgres`:

```bash
INCIDENT_TEST_DATABASE_URL='postgresql:///postgres?host=/private/tmp/copilot-approval-postgres.srVsg3&port=55439' \
  .venv/bin/python fault-lab/verify_native_matrix.py \
  --run-local-integration \
  --gateway-binary /private/tmp/copilot-live-parentage-gateway \
  --checkpoint-postgres
```

It validates the dedicated local database address before Docker work, creates
fresh owned observability stores for each of the eight scenarios, and requires
all 18 named checks, PostgreSQL mode and successful fresh-process recovery in
each case. It enables exact log/span correlation automatically. The aggregate
records the checkpoint backend, required checks, per-case recovery summaries,
reader/source hashes and individual artifact hashes, but never the database
URL. Failed cases remain failures; cleanup failure stops the batch. The same
no-model, no-execution and recovery scope limitations apply.

The first complete PostgreSQL batch finished all eight cases in 863.218 seconds:
all recovery checks passed, but only seven cases passed end-to-end (143/144
checks). Downstream timeout failed exact baseline log/span correlation. The
aggregate remains failed and must not be reported as eight successful cases:
[`native_scenario_matrix_20261002T191955Z.json`](results/native_scenario_matrix_20261002T191955Z.json).
At that revision the lab log shipper dropped failed HTTP batches, a delivery risk;
that alone does not establish the cause of this missing reference. No model or
production actions ran, all owned observability resources cleaned successfully,
and the isolated database was stopped with checkpoint data retained.

### API process crash inspection

Add `--verify-api-crash` to a native probe that already specifies
`--checkpoint-postgres --live-telemetry --scenario <canonical-name>`. Port 8004
must be free. The verifier starts its own authenticated orchestrator API with
fresh read/operator tokens, deterministic reasoning, actual MCP tools and the
dedicated local test database. Tokens are never printed or included in reports.

Diagnosis goes through `POST /v1/incidents`; the verifier does not manually save
the API's incident record. After receiving the saved diagnosis, it sends SIGKILL
only to that owned API child, waits for its exit, starts a new API process, and
reads the complete state and checkpoint history over authenticated HTTP. A
separate tool-disabled recovery reader also checks the repository/checkpoint
state and approval interrupt. The API adapter exposes no approval endpoint;
there is no approval/resume or remediation execution.

This verifies a crash **after** committed diagnosis, not an in-flight diagnosis
or execution crash. Without `--verify-redis-stream`, task state is in memory;
no Redis or event-stream recovery guarantee follows. Only this probe's API/workload processes and owned
observation stores are stopped. Database records remain in the dedicated test
database for inspection.

A single pool-fault API crash probe passed all 23 checks in 90.028 seconds,
recovering 42 trace events, 25 evidence items and eight checkpoints while still
waiting for approval:
[`native_scenario_db-pool-exhaustion_20261002T213201Z.json`](results/native_scenario_db-pool-exhaustion_20261002T213201Z.json).
No model or production action calls occurred. This does not extend the earlier
eight-case matrix to eight API-crash scenarios or prove any approval/resume.

Add `--verify-redis-stream` alongside `--verify-api-crash` to create an owned
temporary Redis instance using the locally installed `redis-server`. It opens
only a Unix socket with mode 700 inside a fresh mode-700 temporary directory;
TCP, snapshots and append-only persistence are disabled. No externally supplied
Redis URL is used. Both API processes connect to this same test instance.

After the API crash, the verifier compares the Redis task hash with its pre-crash
value and the expected incident metadata, reads the complete authenticated SSE
trace stream, then requests `after=<middle-sequence>` and checks exact remaining
events and terminal state. The SSE inspection rejects duplicate/out-of-order
sequences, envelope/payload ID mismatches, duplicate fields and payloads above
1 MiB. Reports include event counts, cursor and TTL, not socket paths or tokens.

This tests API-crash survival while Redis stays running, not Redis-process crash,
disk durability, expiry-after-24-hours or automatic approval/resume. The owned
Redis and its temporary socket directory are removed after verification; the
incident/trace remain in PostgreSQL and the result artifact. No user Redis data
is flushed or deleted.

The single pool-fault Redis/API crash probe passed all 27 checks in 90.179
seconds. SSE returned all 42 exact events; cursor 22 returned the remaining
20 events and the approval-paused terminal state. Redis event TTL was 86,399
seconds at inspection:
[`native_scenario_db-pool-exhaustion_20261002T213636Z.json`](results/native_scenario_db-pool-exhaustion_20261002T213636Z.json).
This is survival across the API crash, not a Redis-process crash/disk-durability
test. Temporary Redis data was removed after verification; PostgreSQL and the
artifact retain the incident trace.

Add `--verify-redis-loss` alongside the Redis/API crash flags to SIGKILL only
that owned Redis after normal stream recovery checks, then verify authenticated
full/cursor SSE replay from checkpoint storage. The API emits a `replay_source`
frame with backend, reason and `read_only: true`; it preserves original event
IDs and never repairs the cache or resumes execution. Missing/partial/divergent
or invalid cache events also use the authoritative checkpoint trace.

The single pool-fault Redis-loss probe passed all 31 checks in 90.994 seconds:
[`native_scenario_db-pool-exhaustion_20261002T214223Z.json`](results/native_scenario_db-pool-exhaustion_20261002T214223Z.json).
It returned 42 full events and 20 events after cursor 22, with PostgreSQL
checkpoint replay provenance, still waiting for approval. This is stream
fallback after Redis loss, not Redis data restoration, disk durability or a
hard deadline for a hung Redis network read. No model, approval or actions ran.

The current SSE cache read requests cancellation after two seconds via
`asyncio.wait_for`, then replays the remaining checkpoint events with reason
`task_store_timeout`. This bounds cooperative cache I/O, not total HTTP time,
PostgreSQL query time or a custom task store that refuses cancellation. Original
event sequences and approval state remain unchanged; no cache repair is performed.

Add `--verify-redis-stall` alongside `--verify-redis-stream --verify-api-crash`
to SIGSTOP only this run's owned Redis, inspect full/cursor checkpoint replay,
then SIGCONT it in a `finally` block. The probe requires exact events/states,
explicit timeout provenance and measured reads between 1.5 and 6 seconds each.
It then verifies normal Redis delivery resumes without fallback metadata. If
combined with `--verify-redis-loss`, the independent SIGKILL loss check runs
after Redis has resumed. Neither flag can target an external Redis instance.

Committed diagnosis/approval results no longer depend on successful cache
publication. After canonical persistence, the API attempts one event-store
publish with a two-second cooperative timeout. On Redis error/timeout it returns
the saved result with `X-Incident-Event-Delivery: checkpoint-fallback` and reason
`cache-unavailable` or `cache-timeout`; normal delivery reports `event-store`.
These two sanitized headers are exposed to configured CORS origins and documented
in OpenAPI for create/approval responses. The reason is absent on confirmed
delivery; both headers may be absent when publication was not attempted (for
example a previously finalized approval). No credential/provider headers are
exposed, and CORS read permission does not replace bearer authentication.
No publish retry or diagnosis/execution replay occurs. Unknown cache-write outcome
does not imply the write was rolled back. Database persistence failures still
fail the request rather than being labeled cache degradation.

Add `--verify-cache-publish` with both owned Redis stall/loss flags to create
additional diagnoses during each cache failure. The probe checks HTTP 201,
degraded-delivery headers, full checkpoint/repository agreement, preserved
approval status and absence of execution nodes. It uses the already-running
authenticated API; this is not API startup with an unavailable Redis or a live
approval/execution experiment. Extra incidents are retained in the dedicated
test PostgreSQL and result artifact.

A combined single pool-fault probe passed all 46 checks in 97.604 seconds:
[`native_scenario_db-pool-exhaustion_20261002T215325Z.json`](results/native_scenario_db-pool-exhaustion_20261002T215325Z.json).
The additional paused-Redis diagnosis returned HTTP 201 in 2.147 seconds with
`cache-timeout`; the Redis-loss diagnosis returned HTTP 201 in 0.106 seconds
with `cache-unavailable`. Both exactly matched checkpoint and incident storage,
retained 42 trace events and remained waiting for approval. No model or approval/
production action calls ran. This does not verify unavailable-Redis startup,
live approval recovery or crashes during the commit/publish gap.

A combined pool-fault API crash/Redis stall/loss probe passed all 36 checks in
93.939 seconds:
[`native_scenario_db-pool-exhaustion_20261002T214702Z.json`](results/native_scenario_db-pool-exhaustion_20261002T214702Z.json).
While Redis was paused, full replay took 2.012 seconds and cursor replay 2.011
seconds, returning 42/20 exact events with timeout provenance. Normal delivery
resumed after SIGCONT, and subsequent SIGKILL fallback also passed. No approval,
model or production action calls ran. This does not establish a total HTTP/DB
deadline, uncooperative cancellation safety or Redis disk/data recovery.

### Bounded log delivery

The native workload now keeps each failed batch for up to five attempts on
network errors, HTTP 429 and server errors, with delays of 0.5, 1, 2 and 4 seconds.
It retries the identical timestamp and serialized record, including trace/span
identity, before consuming the next batch. Other HTTP rejections are not retried.
Logging never waits for queue capacity: the queue holds at most 1,000 records,
each serialized line is limited to 16 KiB, and batches contain at most 100 records.

Prometheus exposes `incident_log_retry_total`, `incident_log_delivered_total`
and `incident_log_dropped_total{reason=...}`. Drop reasons are `queue_full`,
`oversize`, `rejected`, `retry_exhausted` and `shutdown`, with bounded labels.
Counters do not recursively emit logs. Cancelling the shipper counts pending
and queued records and propagates cancellation promptly.

This is bounded best-effort lab delivery, not a durable spool or exactly-once
transport. A lost acknowledgement can cause duplicate pushes; shutdown does
not guarantee flushing, retries can exhaust, and the queue can overflow. A
missing baseline log reference must still fail the verification check. The
earlier failed matrix remains evidence for its original revision.

After this change, a fresh downstream-timeout probe passed all 18 checks in
91.962 seconds, with two exact baseline log/span references and successful
fresh-process PostgreSQL inspection (40 trace events, 23 evidence items, nine
checkpoints):
[`native_scenario_downstream-timeout_20261002T201326Z.json`](results/native_scenario_downstream-timeout_20261002T201326Z.json).
This single rerun does not prove the earlier missing-log cause or replace a fresh
eight-case batch. Transient outage behavior is currently tested with controlled
HTTP/network failures, not a live Loki outage injection.

## Inject and verify

Fault durations are enforced with `time.monotonic()` rather than UTC wall time.
The returned `expires_at` remains display metadata; a clock correction must not
prematurely clear a fault or extend its elapsed lifetime. Explicit clear resets
both deadlines and the fault gauges. This is process-local injection state, not
a persisted fault schedule across workload restarts. Clock-jump tests cover
forward/backward wall-clock corrections and expiry at the monotonic deadline.

The preceding eight-case log-retry batch passed all log/span and recovery checks,
but remains failed (seven passing cases, 140/144 checks) because a clock jump
expired its memory fault before diagnosis:
[`native_scenario_matrix_20261002T201508Z.json`](results/native_scenario_matrix_20261002T201508Z.json).
After the monotonic TTL fix, a fresh memory probe diagnosed sustained growth and
retained its approval pause, but failed exact baseline log/span correlation
(17/18 checks):
[`native_scenario_memory-leak_20261002T204432Z.json`](results/native_scenario_memory-leak_20261002T204432Z.json).
No model or production actions ran. The recurring missing-reference issue is
unresolved, and no fully green latest eight-case batch is claimed.

```bash
python fault-lab/faultctl.py list
python fault-lab/faultctl.py inject db-pool-exhaustion
python fault-lab/faultctl.py clear db-pool-exhaustion
python fault-lab/faultctl.py verify db-pool-exhaustion
python fault-lab/faultctl.py verify-all
python fault-lab/verify_agent_scenarios.py \
  --output fault-lab/results/agent-scenario-matrix.json
```

`verify` injects the fault, generates requests, waits for a scrape/ingestion
cycle, queries the authenticated Go gateway for Prometheus, Loki, and Tempo evidence,
reports a machine-readable result, and clears the fault in a `finally` block.

`verify_agent_scenarios.py` is the stronger end-to-end check. For each fault it:

1. injects and exercises the bounded scenario;
2. asks the real orchestrator a generic service-level question with no fault
   label;
3. follows orchestrator → MCP → Go gateway → Prometheus/Loki/Tempo;
4. checks service selection, incident classification, root-cause profile,
   knowledge retrieval, eight-metric correlation, and remediation routing;
5. rejects every destructive proposal and verifies that no execution step ran;
6. clears the fault and writes a machine-readable matrix.

This distinction is intentional: `faultctl verify-all` proves that all eight
faults emit queryable telemetry, while `verify_agent_scenarios.py` proves that
the Agent interprets those live signals correctly.

Fault injection requires `X-Fault-Token`, has a maximum 15-minute lifetime, and
uses bounded implementations: CPU work is time-limited and the memory-leak
simulation is capped at about 102 MiB by default and 128 MiB at maximum
intensity.

For an API with `ORCHESTRATOR_AUTH_MODE=required`, configure that mode, distinct
read/operator tokens and the bound operator actor in the verifier's local
environment (see `docs/api-authorization.md`). These are server/CLI credentials,
never browser variables. Diagnosis uses only the reader token; rejection uses
the operator token and configured actor. Missing auth configuration aborts before
fault injection. Reported runtime failures include the exception type, not raw
HTTP response bodies that could contain sensitive diagnostics or echoed headers.

The eight scenarios and expected evidence are defined in
[`scenarios.json`](scenarios.json). They cover database pool exhaustion,
downstream timeout, CPU spike, memory leak, deployment regression, Kafka
consumer lag, retry storm, and cache penetration.
