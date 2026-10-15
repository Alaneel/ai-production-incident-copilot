# Release validation — updated 2026-10-14 UTC

The original filename is retained for existing links. This record supersedes the presentation-only environment and preserves historical results separately. **Current backend, frontend, offline and packaged Compose checks pass; current Kubernetes acceptance is blocked by Docker disk exhaustion.** Source preparation does not authorize a push, public repository, deployment or binary/image redistribution.

## Exact release inputs

- CPython 3.13.16 / uv 0.13.0; Node 22.23.3 / npm 10.9.9; first-party Go gateway 1.26.9. Official archives/checksum-database authentication are retained in the preparation record.
- First-party Python distributions: orchestrator 0.2.0, MCP 0.2.0, fault-lab 0.1.0, each with MIT metadata/license. Platform-marked artifact-SHA locks cover runtime/demo 51 pins, test 70, build 8 and fault-lab 38; dependency resolution/build isolation are disabled for first-party installation.
- The [image lock](../container-images.lock.json) binds exact upstream parents, frozen readonly Go locks and actual locally verified Linux arm64 IDs. Go observability derivatives use 1.26.9, except Prometheus's upstream-required 1.27.2. Native amd64 derivative execution is not claimed.
- The four upstream Go derivatives retain selected/replacement module ZIP/hash/h1 records, actual linked-license text and covered-source retrieval instructions. Both Prometheus UI asset sets and their JavaScript notices remain. The scoped Collector implements the five configured components, not the full Contrib catalog. PostgreSQL/Redis/Grafana derivatives preserve upstream server behavior while applying signed minimal security patches. Third-party terms remain separate from MIT.

## Fresh observed checks

| Gate | Result and scope |
|---|---|
| Clean installations | Python third-party SHA locks plus exact first-party wheels; dependency compatibility; npm clean integrity-lock install and actual installed graph |
| Final complete Linux backend/eval/fault/render suite | **1848 passed + 108 subtests**, zero skipped/failed, 49.30 seconds on 2026-10-14 UTC. All 27 real PostgreSQL 17.11 tests ran through an owned private Unix socket, with no network/daemon socket or host ports. One existing TestClient deprecation warning |
| Frontend | All 209 existing tests retained; four hosting/bundle-boundary regressions; **213 passed**, typecheck and neutral build passed |
| Documented startup / Workers SSR | Real local workerd served compiled console HTML and a PNG asset on an owned loopback port; render regression observed zero outbound calls |
| Cross-language SSE | One actual Python API/LangGraph/Pydantic → TypeScript decoder contract, including fragmented UTF8 and incomplete-stream behavior |
| Offline demo | Ten cases pass; zero model/execution calls; two byte-identical outputs equal the retained artifact. SDK-preimported, outer-tracing-enabled and socket-denied regression observes zero transport attempts |
| Fresh synthetic workflow | 150 cases: 120 fault cases/30 controls; 75 destructive proposals explicitly rejected; zero model/execution/transport attempts |
| Fresh synthetic cross-service | 32 structures; all recorded structural contracts pass; zero model/mutation/transport attempts |
| Evidence generation | Exact source/input/corpus/case-record, runtime/build/helper and declared image-recipe binding; unchanged baseline plus **19 tamper refusals** pass |
| Go gateway | 1.26.9 vet, race, module verification and trimmed build; actual native/compiler metadata verified |
| Lint | Zero errors; three unused-directive warnings in unchanged generated declarations |
| Public metadata | Neutral bundle contains zero private hosting target occurrences; original behavior preserved via ignored local override |
| Final packaged Compose | **39/39** checks: actual PG/Redis pool exhaustion, metric/log/trace diagnosis, advisory boundary, fault clearing, persisted HTTP/SSE restart state, both Prometheus UIs, actual version/config flags, CORS and container restrictions |
| Exact images | All 11 runtime IDs scanned unsuppressed with the fixed 14 October advisory database; actual functions/configuration and known compressed installer artifacts separately assessed |
| Current Kubernetes | **FAIL / blocked:** two bounded owned Kind attempts failed at rollout. PostgreSQL/Redis reported `No space left on device`; zero fault checks ran. Cleanup and exact pre-existing resource preservation passed both times |
| Hosted GitHub CI | **NOT EXECUTED:** read-only SHA-pinned workflow prepared; local equivalents are observed separately |

The final full suite retains all original 1844 cases and adds four meaningful regressions: ambient-tracing offline transport denial, absent cached-image refusal before allocation and two partial-cluster creation cleanup outcomes. Four socket fixtures construct a real event loop before mocking socket boundaries; all original assertions remain. The fault source-inventory expectation expanded from 94 to 142 inputs to include all seven release image contexts and frozen Go locks. These are verifier/fixture corrections, not business-rule rewrites.

The current Compose report is [retained here](../fault-lab/results/compose_platform_20261014T142444Z_82ec8643.json). All services used exact recorded local IDs, no published host ports and no model credentials. The verifier checked actual nonroot/cap-drop-ALL/no-new-privileges/read-only settings, loopback browser origins and remote-origin refusal, both Prometheus UI HTML/JS assets, Tempo oversized-limit rejection and Grafana external-update/analytics/plugin flags. Fault removal by the lab is not an approved agent-selected repair or proof of causal efficacy.

## Kubernetes failure and preserved evidence

The [first current attempt](../deploy/kubernetes/results/kind_live_db-pool-exhaustion_20261014T143904Z_afd8893f.json) and [bounded diagnostic attempt](../deploy/kubernetes/results/kind_live_db-pool-exhaustion_20261014T145135Z_46fe2136.json) both failed before fault checks. Actual database logs showed storage exhaustion after image import, rather than a demonstrated permission/configuration defect. The task used a private kubeconfig, explicit owned UUID/context and exact client/node pins. Both clusters and owned resources were removed; original containers/networks/volumes were preserved. A third blind retry or global prune was not performed.

This is one attempted scenario with zero completed fault checks; **the full current eight-scenario matrix is NOT EXECUTED**. Earlier four-case Kubernetes successes remain historical and do not establish current acceptance. Current Compose success cannot substitute for Kubernetes acceptance. The Kubernetes example must retain this limitation until storage and the exact current run are qualified.

## Security, provenance and release boundary

The [security record](dependency-security.md) keeps the unfixed braces build advisory, raw OS/package findings and exact function/configuration dispositions visible. Runtime installed Python matches are zero, but dormant `ensurepip` archives retain known affected vendors. This is not a zero-vulnerability certification. Binary/image redistribution requires the applicable upstream terms and covered-source notices; first-party MIT does not relicense Redis/Grafana/Loki/Tempo or dependency packages.

Fresh 150/32 summaries were measured on 2026-10-14 UTC. Their image-lock/recipe fingerprints bind declared inputs but do not execute those images; live Compose is separate evidence. Historical 2026-10-07 and 2026-10-13 artifacts remain unchanged. Synthetic agreement does not measure model quality, semantic faithfulness, live causal RCA or repair efficacy.

Production SSO, model faithfulness, current all-eight Kubernetes acceptance and actual human-approved agent-selected remediation remain outside the verified scope. Read-only SHA-pinned CI has not run on GitHub. Publication requires a separate exact-ref/destination authorization after review; old private-identifier history is preserved outside the proposed parentless public candidate.

All failed native PostgreSQL preparations, Linux fixture/build attempts, first sidecar builds, startup preview and Kind diagnostics remain retained in the preparation record. Final local verification snapshots are append-only. No paid model, production action or remote publication was performed.
