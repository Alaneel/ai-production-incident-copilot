# Kubernetes deployment

## Current release qualification — 2026-10-14 UTC

Two bounded owned current-image attempts failed during rollout:
[first attempt](results/kind_live_db-pool-exhaustion_20261014T143904Z_afd8893f.json)
and [diagnostic attempt](results/kind_live_db-pool-exhaustion_20261014T145135Z_46fe2136.json).
PostgreSQL/Redis reported `No space left on device` after image import. Zero
Kubernetes fault checks ran. Both attempts cleaned their private cluster/config
and preserved existing resources; no third blind retry or global prune occurred.
Current Kubernetes acceptance is **blocked**, and the full eight-scenario matrix
is **NOT EXECUTED**. The current 39-check Compose success is separate evidence.

## Historical current-source boundary — 2026-10-08

Cross-service diagnosis and signal/action checks have changed since these runtime
reports. Source binding now includes 76 inputs, including the pure RCA module.
No current-source image rebuild or live cross-service/all-eight acceptance is
claimed. Historical success must not be promoted through a stale fingerprint.

The latest prior-source
[pool case passes 42/42](results/kind_live_db-pool-exhaustion_20261007T154949Z_10b0addc.json):
real four-session PostgreSQL pool exhaustion, controlled failed-request log/span
identity, live Go/MCP evidence, server dry-run scope checks and exact checkpoint
restoration in a replacement API Pod. It retains 25 evidence objects/44 events,
uses deterministic reasoning and reports zero model/production actions plus
completed cleanup. The 75-input
[offline aggregate](results/kind_fault_matrix_recovery_pool_20261007T160500Z_716e1bfd.json)
accepted that single revision/case with `full_fault_matrix=false`. The fault was
cleared by the lab, not an approved Agent mitigation.

## Prior-source runtime qualification — 2026-10-07

The new target-bound v3 [CPU case passes 35/35](results/kind_live_cpu-spike_20261007T151549Z_b4c73a48.json)
with newly built MCP/orchestrator images: actual same-key server dry runs,
independent resource/configuration identity, rejected forbidden/missing targets,
no observed template/generation/marker persistence, and exact PostgreSQL
approval-checkpoint/interrupt recovery in a replacement API Pod. Seventy-four
source fingerprints match; cluster/private config are independently confirmed
removed. No model, API approval or Agent remediation runs. The
[v2 offline aggregate](results/kind_fault_matrix_preflight_cpu_20261007T151619Z_d3df17c2.json)
accepts only this CPU case (`full_fault_matrix=false`), not all eight. This does
not prove packet-level absence of ordinary PATCH calls, action efficacy or model
quality. Kubernetes tests pass 280 cases; the offline validator rejects v1
artifacts rather than upgrading them.

Before the fresh single-case run, target-bound v3 had only local mock/protocol
acceptance and no rebuilt/accepted images. The older Kubernetes reports below
predate v2/v3 security changes and remain historical evidence. The read-only
[matrix verifier](verify_fault_matrix.py) refuses stale source sets and requires
eight unique successful same-image cases for full-matrix status. Its earlier
78-test revision produced the three-artifact
[stale-target-revision rejection](results/kind_fault_matrix_stale_targets_20261007T150321Z_408f6db1.json)
with zero accepted cases at that time. Current v2 verifier regression passes 174
cases; the new aggregate above accepts one of eight. Offline consistency cannot independently
attest the honesty of a report or re-observe a removed cluster.

The generalized runner's
[CPU case passes 28/28 checks](results/kind_live_cpu-spike_20261007T142832Z_a2e80b10.json)
in 162.187 seconds including cluster lifecycle. Live telemetry yields a
risk-engine CPU hypothesis and a restart proposal paused at human approval.
Separate PostgreSQL-enforced read-only inspection proves the persisted graph's
pending node and exact action/evidence-bound interrupt; the same checkpoint and
incident survive a new Ready API Pod. No approval, execution or model is called.
All 73 recorded source fingerprints match, and independent inventory confirms
cleanup. The runner accepts eight canonical scenarios with fresh stores per
case, but this report remains one case, not complete matrix coverage. CPU is a
bounded lab busy loop/gauge, not host saturation. Eighty Kubernetes tests and
54 selected workflow tests pass. Approved resume, action efficacy and current
model/semantic quality remain open. See [scope and command](LIVE_FAULT_LAB.md).

The same revision's
[memory case passes 28/28](results/kind_live_memory-leak_20261007T143215Z_bc3f39b5.json)
in a separate fresh cluster. Retained allocation samples grow 48 to 87 MiB;
payment-service is classified as memory growth. Synthetic release provenance
keeps remediation advisory-only and the completed graph has no pending interrupt.
The exact incident/checkpoint survive API replacement; hashes/cleanup pass,
with no model/approval/action. CPU plus memory are two current cases, not all
eight. Memory is a bounded retained-allocation gauge, not process RSS.

The pre-receipt-v2
[consumer-lag case passes 28/28](results/kind_live_kafka-consumer-lag_20261007T143715Z_9e605f43.json)
in 137.032 seconds, with 23 evidence objects and 42 events. Synthetic lag is
classified from primary-service telemetry; restart stays approval-paused, and
the exact interrupt survives a new API Pod. No model/approval/action runs and
cleanup is independently confirmed. This is not an actual Kafka failure or
current-source acceptance.

Latest strict v2 acceptance passes
[28/28 checks](results/kind_live_pool_20261007T140934Z_c55eefd8.json), including
exact Go/MCP failed-request log/server-span correlation and 20 actual diagnostic
traces. The service-provider initialization and newest-first Tempo query fixes
are in uniquely tagged new lab/gateway images. Exact API Pod-replacement state
recovery and scoped cleanup still pass. Model calls/remediation stay zero;
all-eight faults, semantic/causal quality and human-approved action efficacy
remain unproven. Fifty Kubernetes tests pass. See
[live fault-lab scope](LIVE_FAULT_LAB.md) for details.

The old 23-check report retains an empty trace evidence envelope, not delivered
Tempo spans. Reinspection found zero trace samples/observed calls. Its artifact
is unchanged; the stricter v2 runner requires nonempty telemetry and exact
controlled-request Go/MCP log/server-span correlation. See
[live fault-lab scope](LIVE_FAULT_LAB.md) for the new checks and limitations.

The new live fault-lab runner now passes
[23 real-cluster checks](results/kind_live_pool_20261007T134228Z_79004dbd.json).
An isolated real PG pool, four-service traffic and Prometheus/Loki feed
Go/MCP/API diagnosis; Tempo span delivery was not proven by that report.
Independent PG identity and Python source-hash checks pass;
API replacement uses a new Ready Pod UID and restores the exact retained state.
The owned cluster and private config were removed and independently confirmed
absent. Diagnosis stays advisory because release metadata is still synthetic;
no model or remediation runs. This is single-family Kubernetes diagnosis/state
recovery acceptance, not the full original scope. See [live fault-lab scope](LIVE_FAULT_LAB.md).

Two isolated Kind runs now prove control-plane initialization and rollout of
PostgreSQL, Redis, Go, MCP and the API, plus scoped RBAC and API-auth observations.
**The legacy fixture verifier still fails**: it expects an
approval-paused incident, while inspected retained state is insufficient evidence
and a subsequent approval request returns 409. The fixture's old metric samples
do not satisfy current-signal checks. No successful report is claimed. Both new
clusters were cleaned; images remain cached. See the
[runtime investigation](../../docs/kind-runtime-investigation-20261007.md).
That historical failure does not certify a live fault or approval/resume path.

The new [live fault-lab renderer](LIVE_FAULT_LAB.md) combines the actual backend
base with four services, traffic and real Prometheus/Loki/Tempo ingestion, plus
an isolated PostgreSQL application pool. It supplies no secrets and applies
nothing. Its render/contract tests and separate single-family runtime acceptance
now pass; broader cluster/production requirements remain pending.

The base deploys the durable backend into the `incident-copilot` namespace:
two orchestrators, two MCP servers, two Go gateways, PostgreSQL, Redis,
namespace ingress isolation, disruption protection, and least-privilege
remediation RBAC.

The MCP service account can only `get`/`patch` Deployments and `get`/`list`
ReplicaSets inside this namespace. The application independently checks its
namespace allow-list and still requires operator scope, an exact signed
approval receipt, and an idempotency key before using those permissions.
The key is also persisted on the Deployment pod template. A
`resourceVersion` precondition makes retry deduplication effective across both
MCP replicas, not only inside one process.

The pod-template marker now includes an operation fingerprint bound to action
parameters and hashed approval identity. A matching currently retained key is
replayed only when that fingerprint also matches. Legacy key-only or conflicting
markers require manual outcome review and are not automatically reapplied. Mock
API tests cover fresh MCP instances; this revision has not been live-tested in
Kind. A later action overwrites the latest marker, so it is not a durable
historical ledger or an exactly-once guarantee.

The manifests are a reusable production base, not a claim that this repository
has been deployed to a cluster. They intentionally contain no credential
values, public Ingress, TLS certificate, or environment-specific storage class.

For a repeatable local rollout, the `overlays/local-kind` overlay reduces the
stateless workloads to one replica, uses deterministic telemetry fixtures, and
keeps the production base unchanged. The verification script builds and loads
local images, creates an ephemeral development Secret, waits for every rollout,
checks remediation RBAC boundaries, creates an incident, rejects its destructive
proposal, and deletes the cluster unless `KEEP_KIND_CLUSTER=true` is set:

Image tags are now `kind-<cluster-name>` and are substituted into the rendered
local overlay before applying. Use a fresh cluster name to preserve existing
image tags too. A preexisting result path is refused before allocation, and
exclusive output creation prevents overwriting historical reports. The legacy
fixture approval expectation described above is currently not passing.

The script refuses to replace an existing same-name cluster. Choose a fresh
`KIND_CLUSTER_NAME` instead. If cluster inventory fails or creation does not
complete successfully, it will not run cleanup deletion; inspect any partially
created resources manually. Automatic cleanup is limited to a cluster whose
creation succeeded in this invocation, and `KEEP_KIND_CLUSTER=true` preserves
that cluster even after later verification failure. Do not use this verifier
against a production cluster.

The verifier now creates a private kubeconfig under a fresh
`/private/tmp/copilot-kind-kubeconfig.*` directory. Kind creation writes only to
that explicit file, never the caller's kubeconfig. Every kubectl request includes
both `--kubeconfig` and `--context kind-<explicit-cluster-name>`; changes to a
caller's default context cannot redirect the verifier. Cleanup passes the same
private configuration to Kind deletion and removes it only after successful
deletion plus a successful cluster inventory confirming absence. A kept cluster,
partial creation, deletion error or failed absence check retains the private
file for manual inspection; its path is reported. Retained files contain cluster
credentials and should not be shared or committed. Tests use stubbed external
commands and an unrelated kubeconfig sentinel, not real cluster mutations.

The orchestrator base now uses `ORCHESTRATOR_AUTH_MODE=required`. Its Secret
must include distinct `orchestrator-read-token` / `orchestrator-operator-token`
values of at least 32 characters and an `orchestrator-operator-actor`. The Kind
script uses explicitly public test credentials, checks unauthenticated requests
and reader approval are denied, and submits rejection with the bound actor.
The authentication changes have only been rendered/syntax-tested, not live
cluster-tested. Browser integration still requires a trusted server-side proxy;
do not embed these credentials in the frontend.

```bash
KIND_RESULT_PATH=deploy/kubernetes/results/kind-verification.json \
  ./deploy/kubernetes/verify_kind.sh
```

## Prepare

Build and push the three application images, then update the `images` entries
in `base/kustomization.yaml`.

Create the secret from a secure source. `secret.example.yaml` is deliberately
excluded from Kustomize:

```bash
kubectl apply -f deploy/kubernetes/base/namespace.yaml
kubectl -n incident-copilot create secret generic incident-copilot-secrets \
  --from-literal=postgres-user=incident \
  --from-literal=postgres-password='<generated>' \
  --from-literal=database-url='<postgres connection string>' \
  --from-literal=telemetry-gateway-token='<generated>' \
  --from-literal=mcp-read-token='<generated>' \
  --from-literal=mcp-operator-token='<generated>' \
  --from-literal=orchestrator-read-token='<distinct generated >=32 chars>' \
  --from-literal=orchestrator-operator-token='<distinct generated >=32 chars>' \
  --from-literal=orchestrator-operator-actor='<credential-bound principal>' \
  --from-literal=mcp-approval-secret='<generated>'
```

The gateway expects Prometheus and Loki services in the `monitoring` namespace.
Override their URLs for a different observability installation.

Create an overlay for at least:

- immutable image digests or environment-owned version tags;
- the public origin/Ingress and TLS configuration;
- Prometheus and Loki service URLs;
- optional `OPENAI_API_KEY` Secret data plus an explicit `OPENAI_MODEL`;
- storage classes, resource sizing, and replica counts;
- any namespace added to the remediation allow-list and its matching RBAC.

## Render and apply

```bash
kubectl kustomize deploy/kubernetes/base
kubectl apply -k deploy/kubernetes/base
kubectl -n incident-copilot rollout status deploy/orchestrator
```

The public ingress hostname and CORS origin are intentionally placeholders;
patch them in an environment overlay rather than editing the reusable base.

Before enabling execution, verify the effective permissions:

```bash
kubectl auth can-i patch deployment/payment-service \
  --as=system:serviceaccount:incident-copilot:mcp-remediator \
  -n incident-copilot
kubectl auth can-i patch deployments \
  --as=system:serviceaccount:incident-copilot:mcp-remediator \
  -n kube-system
```

The first should be `yes` only for an existing in-scope deployment; the second
must be `no`.
