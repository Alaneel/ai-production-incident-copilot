# Isolated real PostgreSQL application-pool packaging experiment

This is **not** the full Kubernetes incident-response/telemetry stack. It runs
one payment-service container and a dedicated PostgreSQL sidecar in the separate
`incident-pool-lab` namespace. There is no PostgreSQL Service, TCP listener,
persistent volume, model configuration or remediation executor. The database is
ephemeral; replacing the Pod loses only this experiment's database. Do not put
useful data in it. Existing incident-copilot resources are not patched/reused.

Build the current lab image and make it available in the target test cluster:

```bash
docker build -t incident-copilot/fault-lab:real-pool fault-lab/app
# For your own kind test cluster, substitute its explicit name:
kind load docker-image incident-copilot/fault-lab:real-pool --name YOUR_TEST_CLUSTER
```

Inspect the active Kubernetes context before applying anything. Create this
experiment's namespace first. Supply two separate private files containing
fresh random values (no trailing newline): a non-default fault token and a
database initialization password. Keep them outside the repository and do not
reuse production credentials. The Secret is deliberately not generated or
committed by Kustomize.

```bash
kubectl apply -f deploy/kubernetes/experiments/real-pool/namespace.yaml
kubectl -n incident-pool-lab create secret generic incident-pool-lab-secrets \
  --from-file=fault-token=/absolute/private/fault-token \
  --from-file=postgres-password=/absolute/private/postgres-password
kubectl apply -k deploy/kubernetes/experiments/real-pool
kubectl -n incident-pool-lab rollout status deployment/payment-service --timeout=120s
kubectl -n incident-pool-lab port-forward service/payment-service 8091:8090
```

With that forwarding running, `/health` must report `db_pool_backend=postgres`.
Authenticated `GET /admin/pool` reports four actual backend PIDs. Existing
`POST /admin/fault` (`db_pool_exhaustion`, duration 10–900 seconds) reserves all
four leases and `/v1/work` must return 503. Authenticated `DELETE /admin/fault`
must restore HTTP 200. Tokens belong in the `x-fault-token` header, not URLs.
No restart/rollback is needed for this experiment. Do not delete a namespace you
did not create or that contains unrelated data; this guide does not auto-clean
an existing cluster/namespace.

The app runs as uid 10001 with group 70; the database runs as uid/group 70.
Only the socket emptyDir is shared, mounted read-only on the app. PostgreSQL
socket permissions are 0770 and local socket access is the experiment's trust
boundary; the initialization password is not used by local trust connections.
The app does fixed `SELECT 1` work. Server-wide `max_connections` is not exhausted.

The bootstrap validates local target/token **before connecting**, probes with
fixed `SELECT 1`, closes each readiness connection, retries operational failures
for a 60-second loop budget, and then replaces itself with uvicorn without a
shell. A final bounded connect/query probe can overrun the loop budget; this is
not a strict end-to-end startup deadline. Permanent errors fail closed with only
the exception class printed. Startup probes allow for the readiness phase.

Current Kubernetes evidence is manifest rendering, contract/unit tests and a
local-PG bootstrap test. A fresh lab image build and isolated/merged Docker
runtime acceptance now pass (see `fault-lab/README.md`); the earlier Docker disk
failure is historical, not a current build blocker. No Kubernetes runtime
acceptance is claimed for this experiment. Loki,
Tempo, Prometheus, upstream/downstream integration and agent deployment must
still be tested separately. The separate native real-pool report proves only
the native stack at its recorded revision.

Isolation follows [PostgreSQL connection settings](https://www.postgresql.org/docs/17/runtime-config-connection.html):
empty `listen_addresses` disables IP listeners; socket ownership/permissions
are separate from SQL authentication.
