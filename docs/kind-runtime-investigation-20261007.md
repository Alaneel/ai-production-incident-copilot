# Local Kind runtime investigation — 2026-10-07

Result: **partial deployment evidence, failed end-to-end acceptance**.

Two newly named clusters were created with private kubeconfigs, not the caller's
current context: `copilot-runtime-20261007-a91c` and
`copilot-runtime-20261007-b72d`. Kind selected `kindest/node:v1.36.1`. Both
control planes became Ready and current orchestrator/MCP/Go images were built,
loaded and deployed with per-cluster tags. PostgreSQL, Redis, Go, MCP and
orchestrator rollout commands completed successfully. The script's in-namespace
patch permission / kube-system denial checks completed before API checks.
NetworkPolicy objects were applied; enforcement was **not** tested.

The first script exited with curl status 22 and cleaned its cluster. The second
used `KEEP_KIND_CLUSTER=true` temporarily so the exact failed stage could be
inspected. API access logs show health 200, unauthenticated incident read 401,
reader approval 403, incident creation 201 and subsequent approval 409. The
earlier working assumption that incident creation itself failed was disproved
by these logs. No successful verifier result file was emitted by either run.

A scoped read of the second run's `INC-D3D2BD` returned HTTP 200 and
`status=insufficient_evidence`, `tool=none`, `requires_approval=false`,
`dry_run_passed=false`, no approval actor and `workflow_completed.executed=false`.
Its terminal recommendation requests current failure signals. The fixture
gateway emits 16 one-minute samples beginning at the requested start; for a
30-minute query these end about 15 minutes before retrieval and cannot establish
current metric failure under the five-minute gate. This supports the need to
replace the legacy fixture-based waiting-approval expectation with scoped
runtime checks and a separately verified live-telemetry fault experiment.
It does not prove every reason for the workflow outcome or an approval/resume
path. The full create-response body was not retained and is not reconstructed.

No destructive action or paid model call was authorized/performed. The second
cluster was deleted explicitly after read-only inspection. Both clusters were
disposable; their test databases are gone. Built images and the downloaded node
image remain cached. This note retains observed evidence, not a fabricated
successful benchmark or complete incident trace. Full Kubernetes telemetry,
current-fault diagnosis, human interrupt/resume, recovery and causal remediation
acceptance remain open.
