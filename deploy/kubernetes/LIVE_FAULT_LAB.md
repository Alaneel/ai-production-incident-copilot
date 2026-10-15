# Live Kubernetes fault-lab renderer

Current-source boundary (2026-10-08): the cross-service/metric-scope/coverage
changes expand binding to 76 inputs. Reports below retain their measured source
and images; historical reports do not certify the modified worktree. Fresh images
`stream-live-20261008-4e2e91` now pass CPU/pool/timeout/memory with 35/42/35/35 checks.
The [four-case aggregate](results/kind_fault_matrix_stream_four_20261007T1715Z_4e2e91.json)
verifies the current 76 sources and shared image IDs but keeps
`full_fault_matrix=false`; remaining cases are in progress. No model, approval
or suggested action runs. These are single-namespace bounded lab cases, not
live cross-service RCA, namespace isolation or Agent-selected repair efficacy.
Rebuild/reaccept when source changes before claiming complete current coverage.

The latest prior-source
[pool run passes 42/42](results/kind_live_db-pool-exhaustion_20261007T154949Z_10b0addc.json)
in 168.321 seconds including lifecycle, with actual four PG leases, controlled
log/span correlation, live Go/MCP diagnosis and exact checkpoint restoration
after replacing the API Pod. Its 75 source inputs matched at that run; reasoning
was deterministic, model/production-action counts were zero, and cleanup passed.
The [offline pool aggregate](results/kind_fault_matrix_recovery_pool_20261007T160500Z_716e1bfd.json)
accepted one case at that revision, not today's source or the full matrix. The
lab cleared the injection; Agent-selected recovery efficacy was not tested.

Prior-source v2-contract CPU acceptance:
[35/35 checks pass](results/kind_live_cpu-spike_20261007T151549Z_b4c73a48.json)
in 155.981 seconds including cluster lifecycle. Fresh MCP/orchestrator images
exercise true same-key server dry runs and bounded negative targets; independent
snapshots/configuration fingerprint match the retained identity without observed
template/generation/marker persistence. Diagnosis stays at human approval, and
the exact context-bound graph checkpoint/interrupt survives a new Ready API Pod.
The [offline aggregate](results/kind_fault_matrix_preflight_cpu_20261007T151619Z_d3df17c2.json)
accepted that single case at its revision but not the full eight-case matrix. No model,
approval or suggested remediation runs. All 74 source fingerprints match;
cluster/private config cleanup is independently confirmed. This is not causal
mitigation, host CPU stress, model-quality or packet-level PATCH audit evidence.

The runtime artifacts below predate the namespace-bound v2 and target-bound v3
approval changes. V3 adds real backend `dryRun=All` preflight and Deployment/RS
identity/configuration binding; before the CPU/pool runs it had only mock acceptance. These older artifacts
are historical, not current image/worktree acceptance.
Rebuild both orchestrator and MCP images and rerun before claiming current
coverage; the offline matrix verifier rejects their now-stale source hashes.

Generalized CPU acceptance:
[28/28 checks pass](results/kind_live_cpu-spike_20261007T142832Z_a2e80b10.json)
in 162.187 seconds, retaining 23 evidence objects and 42 workflow events. A
generic risk-engine question yields CPU saturation and a restart proposal that
stops at approval. Separate PostgreSQL read-only graph inspection before and
after API Pod replacement retains the exact checkpoint ID, pending node and
action/evidence-bound interrupt. All 73 source hashes match and cleanup is
independently confirmed. No model, approval or suggested action runs. This is
one bounded busy-loop/gauge case, not host CPU stress, full matrix coverage,
controlled log/span identity for this case, approved resume or causal efficacy.

The same revision's independent
[memory case passes 28/28](results/kind_live_memory-leak_20261007T143215Z_bc3f39b5.json)
in 152.969 seconds. Five current retained-allocation gauge samples increase
48 → 58 → 68 → 77 → 87 MiB. A generic payment-service question yields memory
growth; synthetic releases leave advice without a rollback or interrupt. Its
25 evidence objects, 44 events and exact completed checkpoint survive a new API
Pod. Hashes and independent cleanup pass; no model/approval/action runs. The
gauge measures bounded retained allocations, not RSS. These two cases do not
constitute the complete eight-case matrix.

Latest strict runtime acceptance:
[28/28 checks pass](results/kind_live_pool_20261007T140934Z_c55eefd8.json),
including exact controlled failed-request correlation and nonempty diagnostic
traces (20). The 169.462-second run retains 25 evidence objects and 44 workflow
events, restores exact incident state in a new API Pod and confirms cleanup.
All recorded source fingerprints match. No model or suggested remediation runs.
Use lab/gateway images containing the FastAPI provider-ownership and newest-first
Tempo search fixes; the old images cannot satisfy these checks. Both are built
from the worktree with unique `trace-identity-20261007` tags, retained alongside
failed reports. This remains sampled correlation for one real pool fault, not
causal/model quality, all-eight coverage or arbitrary approval/crash recovery.

Reinspection qualification: the 23-check report below has an empty trace array
and zero observed calls. It retains a trace evidence envelope, **not proof of
delivered spans**. The original artifact is unchanged. The v2 runner now rejects
empty telemetry envelopes and separately verifies a controlled failed request
through the installed MCP client and Go gateway. It checks the exact trace ID,
request ID, client/server call parentage and matching server-completion span ID;
the pool-error log must share trace/request IDs and show 4/4 saturation. The
error log's internal span ID is not claimed to equal the server span. Background
traffic is briefly paused only inside the owned cluster to avoid bounded search
sample eviction, then resumed before diagnosis. This is sampled identity proof,
not causal root-cause or model-quality validation.

Current single-family runtime evidence (2026-10-07):
[23 checks pass](results/kind_live_pool_20261007T134228Z_79004dbd.json) in a newly
owned Kind cluster, including independent PostgreSQL session IDs, current Python
image/source hashes, real pool exhaustion, live metric/log evidence,
advisory-only diagnosis, recovery and exact retained incident state in a new
Ready API Pod with a different UID. The run takes 131.334 seconds including
cluster lifecycle, retains 25 evidence objects and 44 trace events, invokes no
model or remediation, and confirms cleanup. This time is not model diagnostic
latency. Two earlier restart-probe failures remain unchanged.

This does not prove log/span identity correlation, all eight faults, checkpoint
history/in-flight crash recovery, human approval/execution, deployment chronology,
network-policy enforcement, Grafana/UI rendering or model/causal quality.

## Isolated runtime command

`verify_live_fault_lab.py` runs the complete resource/telemetry diagnosis check
against a newly named Kind cluster, using a private kubeconfig, generated test
secrets and only the explicitly supplied local application images:

```bash
.venv/bin/python deploy/kubernetes/verify_live_fault_lab.py --run-local-integration \
  --scenario db-pool-exhaustion \
  --orchestrator-image incident-copilot/orchestrator:<current-local-tag> \
  --mcp-server-image incident-copilot/mcp-server:<current-local-tag> \
  --telemetry-gateway-image incident-copilot/telemetry-gateway:<current-local-tag> \
  --fault-lab-image incident-copilot/fault-lab:<current-local-tag>
```

The `--scenario` argument accepts the eight canonical names from
`fault-lab/scenarios.json`; its default remains `db-pool-exhaustion`. Each
invocation creates its own cluster and fresh observation stores. Non-pool cases
connect ledger to risk-engine so checkout traffic reaches all four services.
The API receives only a generic service question, not the fault mode/expected
classification. The expected family and safety outcome are verifier inputs,
never diagnosis inputs. Extending the runner does not itself prove that all
eight runtime cases pass; every single-case report keeps `full_fault_matrix=false`.

Current reports use `kind-live-fault-acceptance-v2`, a generalized contract
distinct from historical pool-specific v2 and generalized v1 reports. They retain
35 common checks (42 for the real-pool correlation case). The verifier separately
calls installed read-token MCP restart preflight twice with the same key for each
primary service, plus bounded forbidden/missing-target negative probes. Real
`dryRun=All` responses must match independent target/configuration snapshots;
template, generation and operation markers must remain unchanged. ResourceVersion
alone may change due to controllers. CPU/consumer-lag diagnoses must pause with
real scoped preflight context bound to their trace and interrupt; rollback families
remain advisory because release metadata is synthetic. The separate probe does
not turn advisory diagnoses into actions. Neither API approval nor suggested
execution is called. Preflight is not a rollout-health or mitigation check, and
snapshots alone do not attest absence of ordinary PATCH HTTP requests.
For every case, a separate process uses a
PostgreSQL-enforced read-only connection and the actual compiled graph to inspect
the persisted checkpoint. The visible incident, pending node, interrupt ID,
action arguments and evidence binding must agree. The same checkpoint must
survive an API replacement with a new Ready Pod UID. Advisory cases must instead
have no pending node or interrupt. This tests retained pauses, not approved
resume, arbitrary in-flight crash recovery or execution efficacy.

Fault fidelity is explicit: pool exhaustion holds four actual PostgreSQL
sessions; downstream timeout and retries use bounded HTTP behavior; CPU uses a
bounded application busy loop and lab gauge; memory uses capped retained
allocations. Deployment regression, consumer lag and cache miss remain synthetic
signals, not actual rollout/Kafka/cache failures. Non-pool cases require nonempty
metric/log/trace payloads, but do not claim the pool-specific controlled request's
exact log/server-span correlation. Clearing the selected fault must return a
healthy response from that service as well as checkout.

All telemetry/PostgreSQL/Redis images must already exist locally. The Kind node
image may be downloaded by cluster creation. The script loads cached images
into the new node, creates secrets over stdin without retaining their values,
applies the generated manifest, waits for workloads, injects/clears the selected
fault, and saves the complete diagnosis/evidence/tool trace. It checks PG
session identities independently from the sidecar and compares Python source
hashes inside the running images against the worktree. Report fingerprints also
cover Python source/packages/Dockerfiles, the knowledge corpus, Go source and
deployment/telemetry configuration. No API approval or model
call is performed. It does not use `.env.local` or the caller's current context.

After rolling API restart, the recovery probe must target one Ready,
non-terminating Pod with a new UID, not `exec deployment/orchestrator`, which can
select an exiting old Pod. Exact retained incident readback is required. No ready
replacement or ambiguous replacements fails the check instead of counting an
old process as recovery. Failure diagnostics preserve stage/return-code details
with generated credentials scrubbed; pod states omit raw provider messages.

Only the newly created cluster is deleted, and successful Kind inventory must
confirm its absence before the private kubeconfig is removed. Partial creation
or deletion failure leaves the private file path in the report for manual
inspection; do not share its credential contents. Dated JSON reports are created
exclusively and preserve failures, source/manifest hashes and image identities.
Existing clusters, image tags and user configuration are not reused. New test
tags and cached images remain after cleanup; test databases are disposable.

## Offline matrix consistency

After running the current images, supply only explicit immutable child reports:

```bash
.venv/bin/python deploy/kubernetes/verify_fault_matrix.py \
  <current-case-report-1.json> <current-case-report-2.json> \
  --output <new-matrix-summary.json>
```

The output file is exclusive: an existing path is not replaced. Reports must use
the generalized contract, include the exact current source set, share all four
image IDs, pass all required checks and retain nonempty primary-service
telemetry with exact graph recovery. Pool cases additionally retain controlled
request correlation. The inputs and their individual manifests are hash-bound;
unique tags mean manifest hashes are not expected equal. Old pool-v2/generalized-v1
reports, stale source sets, duplicate/unknown cases and mismatched images fail.
The validator independently recomputes strict preflight response types,
configuration/target/snapshot/trace bindings, fresh retry and negative results.
No report auto-discovery, cluster/model call or input rewriting occurs.

Exit zero means the supplied subset is internally accepted, not that all eight
are present. To gate complete canonical coverage, require both fields:

```bash
jq -e '.accepted == true and .full_fault_matrix == true' <new-matrix-summary.json>
```

This offline check cannot independently attest report honesty or re-observe
removed clusters. It is not semantic/root-service discovery, model quality,
whole-chain recovery, real Kubernetes preflight or mitigation efficacy proof.
The runner asks about the known primary service; nonempty hypotheses do not
independently prove top-1 cause correctness. Checkout HTTP 200 alone can retain
a failed downstream status. These limitations remain separate acceptance work.

## Renderer and scope

`render_fault_lab.py` emits a Kubernetes `List` to stdout. It does **not** create
a cluster, apply resources, supply secrets, or change the caller's kubeconfig.
Use it only in a newly owned disposable test cluster with an explicit private
kubeconfig/context. The separate single-family runtime result above supplements
render tests; neither is a model RCA benchmark or complete production acceptance.

```bash
.venv/bin/python deploy/kubernetes/render_fault_lab.py --image-tag <fresh-tag>
```

The renderer starts from the actual production-base Kustomize output, keeps its
API authentication, durable PostgreSQL/Redis stores and RBAC, and reduces
Deployments to one replica. It replaces all three backend image tags with the
explicit local test tag and sets their pull policy to `Never`. A current lab
image with that same tag is required. Build/load images before applying; do not
reuse a tag from an unrelated project. Store images are also required locally in
the node: PostgreSQL 17 Alpine, Redis 7.4 Alpine, and the exact Prometheus/Loki/
Tempo/collector images in `STORES`. PostgreSQL/Redis retain the base pull policy;
the telemetry and pool-sidecar images never pull automatically.

New workloads include:

- checkout → payment → ledger, plus risk-engine;
- a separate PostgreSQL sidecar for payment's four-connection resource fault;
- bounded 2-RPS checkout traffic;
- Prometheus scraping all four services;
- Loki log ingestion and Tempo traces through the OpenTelemetry collector.

Configurations come from the checked-in Compose fault-lab telemetry files, not
a new untracked monitoring setup. Tempo exposes both query HTTP 3200 and OTLP
HTTP 4318 so the collector can deliver spans. The Go gateway uses these live
namespace-local services rather than fixtures. The MCP origin allowlist matches
the current client's explicit origin. API model credentials are deliberately
empty, even if a base secret contains a key. No paid reasoning or production
execution is part of this experiment.

All namespaced objects target `incident-copilot` within the private test cluster.
No generated Secret, NodePort or public Ingress is supplied. Provide both
`incident-copilot-secrets` (base backend/API credentials) and
`incident-pool-lab-secrets` (`fault-token`, `postgres-password`) separately, using
fresh test values. The app database uses only a shared Unix socket, with no TCP
listener or database Service. It does not reuse the canonical incident database.
Pool data and observation stores are ephemeral; never put useful data in them.

Pool-specific required runtime evidence (the historical pool results above do
not gain the new graph-checkpoint checks retroactively):

1. Ready workloads and verified current image/source identities.
2. Four actual PostgreSQL sessions and successful baseline checkout traffic.
3. Current Prometheus points, correlated Loki events and delivered Tempo spans.
4. Injected pool leases 0/4 → 4/4 and real request 200 → 503.
5. Authenticated API diagnosis with cited current evidence and no fixture fallback.
6. Advisory-only behavior for the still-synthetic deployment catalog. This does
   not prove a real rollback target or an approval/execute path.
7. Explicit clear returning 0/4, HTTP 200, and retained backend session IDs.
8. API process restart with exact incident/checkpoint recovery.
9. Exported diagnosis/tool trace before removing only the owned cluster.

Whole-response semantics, current deployment chronology, causal mitigation
efficacy, all eight faults, interruption/approval recovery, network-policy
enforcement, UI/Grafana rendering and expert/model benchmarks remain separate
requirements. This renderer must not turn its own fixture tests into those
claims. It intentionally leaves the original bounded fixture verifier unchanged
instead of weakening time checks to manufacture an approval pause.
