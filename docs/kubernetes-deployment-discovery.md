# Read-only Kubernetes revision discovery

Enable `KUBERNETES_DEPLOYMENT_DISCOVERY_ENABLED=true` on MCP to read Deployment
and ReplicaSet resources directly from Kubernetes. It is independent of
`KUBERNETES_REMEDIATION_ENABLED`: discovery does not enable real mutation. It
uses existing service-account token/CA setup and `MCP_ALLOWED_NAMESPACES`.
`get_recent_deployments` has a validated namespace argument; the orchestrator
passes the incident environment as namespace. Other callers default to `prod-sg`,
which must be allowed. Do not enable against an account/cluster outside scope.

When disabled, the catalog remains synthetic. When enabled, any read, permission,
timeout or validation failure is an error, **not** fixture fallback. Output is
sanitized; no raw Pod template/environment/full API response is returned.

Each query has three GETs: Deployment, one namespace ReplicaSet list (`limit=200`),
and Deployment again. Optional event discovery adds one GET before the final
Deployment identity check. The query deadline is six seconds and each response body
is capped at 512 KiB. Over-budget or paginated lists fail closed. Owner references
must match exact Deployment UID/name/kind/controller in the requested namespace;
labels or a matching revision number alone do not prove ownership. Invalid or
duplicate owned revisions, missing current revision/identity, and a changed
Deployment UID/resourceVersion are rejected.

Output is **current revision state with retained history**, not a chronological
deployment event log. It contains current revision, nearest lower retained owned
revision, UID/resourceVersion, namespace and bounded retained-revision list.
Garbage collection may remove the immediately previous revision; no prior
retained revision means no rollback target. One current-state row is returned;
`limit` bounds its revision list. Compatibility `deployed_at` is explicitly marked
`timestamp_semantics=query_observation_not_deployment_time`. `revision_created_at`
is ReplicaSet creation, not rollout time: rollback may reuse an older ReplicaSet.
Do not infer deployment/incident timing or causation from this snapshot.

## Optional event observations

`KUBERNETES_DEPLOYMENT_EVENTS_ENABLED=true` adds a read-only core/v1 Events
query for the exact Deployment UID. Discovery must also be enabled. Events have
a 1.5-second sub-deadline inside the six-second total, at most 100 records and
the same response-size cap. Partial lists, denied reads, timeouts or malformed
responses yield `event_timeline.status=unavailable`; they do not fabricate
events or invalidate an otherwise verified revision snapshot. Disabled event
discovery is `not_requested`; a valid query with no retained accepted events is
`empty`. These statuses are not interchangeable.

Only fixed deployment-related reasons and matching namespace/name/kind/API
version/UID are retained. Raw messages and arbitrary event fields are excluded.
First and latest observation times are separate; missing times remain unknown.
Identical Event UID records are deduplicated and conflicting UID records reject
the event timeline. The adapter revalidates these fields before creating cited
`deployment_event` evidence.

The graph records how event observation intervals relate to the sampled metric
anomaly window. Interval overlap does **not** prove an individual occurrence
inside the window, an associated release revision, rollout time or causation.
It neither grants rollback authority nor replaces approval. Events are not a
complete release archive: Kubernetes defaults to a configurable one-hour
retention, and first/latest times describe event observations rather than
deployment completion. See the [Event API](https://kubernetes.io/docs/reference/kubernetes-api/core/event-v1/)
and [API server event retention option](https://kubernetes.io/docs/reference/command-line-tools-reference/kube-apiserver/).

The workflow retains `data_origin=kubernetes_api` and namespace for its target
guard. An observed eligible target does **not** prove rollback is the correct
mitigation; semantic cause/action validation remains incomplete. Human approval,
durable intent and execution evidence/scope checks still apply. The rollback
executor also now requires a unique owned ReplicaSet and rejects incomplete
lists, preventing unrelated same-number revision selection at dispatch time.

## Read-only configuration

The incident overview now displays retained event observations as a UTC table
with first/latest times, sampled-window relationships and links to exact evidence
IDs. This is a display-time comparison of retained timestamps, not a new tool
query or causal inference. Empty, unavailable, not-requested and legacy unknown
states remain distinct. Invalid/ambiguous identities, wrong origin/scope and
oversized lists do not become temporal claims; missing times display Unknown.
The table has no execution controls and explicitly disclaims rollout timing,
individual-occurrence overlap, causal proof and remediation authority.
Projection tests and rendering of the actual React component pass. A local
browser acceptance on 2026-10-03 also covered a default desktop viewport and a
390×844 narrow viewport using actual MCP/compiled-graph fixtures with mocked
Kubernetes responses. A narrow-table clipping issue was found and corrected to
stacked observation fields; panel client/scroll widths now match at both sizes.
Evidence anchor navigation and expansion and unavailable-query messaging were
checked without approving any action. Screenshots: [desktop](screenshots/desktop.jpg),
[narrow](screenshots/mobile.jpg), [unavailable query](screenshots/unavailable.jpg).
This is not a live-cluster console or full-browser/accessibility matrix acceptance.
The development server emitted React multiple-renderer warnings during hot
reload; checked pages rendered after reload, but the hot-reload warning itself
has not been diagnosed or resolved. Temporary preview/fixture servers were stopped.

`deploy/kubernetes/overlays/read-only-history` uses a separate
`mcp-deployment-reader` ServiceAccount and namespace Role with only
`get deployments`, `list replicasets` and `list` core `events`. Revision and
event discovery are enabled and real
remediation disabled. Base remediator RBAC remains, but is not bound to this
Pod's ServiceAccount. Inspect without applying:

```bash
kubectl kustomize deploy/kubernetes/overlays/read-only-history
```

Build/tag current MCP source and update the image reference before deploying;
the base image tag does not prove this new code is installed. Supply existing
application secrets using the deployment guide and inspect context before any
apply. No existing cluster is altered by tests. Native verifiers explicitly
disable discovery so inherited environment cannot access a real cluster.
The base remediator Role is unchanged and does not grant event reads; enabling
events with another ServiceAccount requires an explicitly scoped `list events`
grant, otherwise optional event context is unavailable.

## Verified scope

Tests cover owned/foreign/controller/namespace filtering, revision ambiguity,
response/list bounds, Deployment changes, scope validation before requests,
redacted HTTP errors, no fallback, RBAC/static contracts and actual Kustomize
rendering. A **mock** Kubernetes HTTP API runs through actual MCP ASGI transport
and the compiled LangGraph; evidence survives checkpoint readback and execution
stays paused. Operation-response-loss tests remain intact with owned fixtures.
Event tests additionally cover legacy/series time fields, missing or invalid
times, identity filters, count validation, deduplication/conflicts, denied and
partial queries, message exclusion, and all sampled-window comparison outcomes.
Mock event evidence and the non-causal comparison survive in-memory checkpoint
readback. Two isolated-PostgreSQL acceptance cases also commit the actual
MCP/graph state, SIGKILL the writer and read from an independent tool-free
process: available events and denied/unavailable event queries. Checkpoint and
repository state match exactly, including evidence IDs, first/latest times,
non-causal comparison, retained history and the human-approval interrupt.
No telemetry is re-queried or remediation dispatched during inspection. These
are crash-after-commit tests, not proof of mid-write loss behavior or real
cluster observations.
No real Kubernetes discovery/mutation was exercised. New image/test-cluster
runtime acceptance remains unverified and Docker capacity remains constrained.
No model requests occurred.

Implementation follows Kubernetes documentation on
[owner references](https://kubernetes.io/docs/concepts/overview/working-with-objects/owners-dependents/),
[revision annotations](https://kubernetes.io/docs/reference/labels-annotations-taints/),
and [paginated API lists](https://kubernetes.io/docs/reference/using-api/api-concepts/).
