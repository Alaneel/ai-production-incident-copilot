# Local validation — 2026-10-13

> Historical first-phase presentation validation. Dependency versions, licensing and current checks have since changed; use [release validation](release-verification-2026-10-13.md) for the release candidate.

Presentation changes were verified in an isolated local copy. No application, workflow, database formula, authorization or recovery behavior was changed. Existing dirty/untracked source work was preserved; actual environment files, kubeconfig, databases and operator metadata were excluded from the copy.

## Results

| Check | Current result | Scope and limits |
|---|---|---|
| Existing Python regression suite | **1,817 passed, 27 skipped**, one upstream Starlette deprecation warning | Orchestrator, MCP, evaluations, fault-lab app and Kubernetes verifier tests. Every skip required an isolated/dedicated PostgreSQL URL. Kubernetes rendering tests use installed local CLIs; this is not live cluster acceptance |
| Dedicated PostgreSQL follow-up | **27 passed**, no skips | Exact 27 previously skipped cases, unchanged tests, using a new owned PostgreSQL **14.19** UTF8 instance, private Unix socket, dedicated `_tests` database, no TCP listener. Actual checkpoint/repository recovery, concurrency/locking, SIGKILL/fresh-process context and bounded pool behavior; tool HTTP/Kubernetes remains mocked/synthetic as specified by each test |
| Frontend `npm test` | **Typecheck and production build pass; 209 tests pass** | Node 22.22.0, isolated copied dependencies; current console rendering and pure contracts, no live backend/cluster |
| Explicit backend stream contract | **1 passed** | Actual in-process Python API / compiled LangGraph / Pydantic output through the frontend parser and fragmented body decoder; complete cursor 46, truncated EOF remains unconfirmed; no model or Kubernetes calls |
| Go telemetry gateway | **Pass** | Existing `go test ./...`, dependencies resolved offline; command entry point has no tests |
| New offline demo | **10/10 pass; two runs emit identical JSON** | Eight families and two controls; BM25 + actual compiled graph; five source/data fingerprints retained; zero model calls/execution attempts; in-memory checkpoint round trips |
| Demo dependency snapshot | **49 exact runtime package versions recorded and matched** | Reachable dependency union from both service manifests, including binary/pool and standard-server extras; no editable paths or unrelated lint/test-only packages. Version snapshot for CPython 3.13.0 / macOS arm64, without downloaded artifact hashes |
| Offline transport regression | **1 passed** | Ambient LangSmith/LangChain/OTEL tracing explicitly enabled with a synthetic key and socket connections denied; the real ten-case demo passes with zero attempted connections |
| Retained evidence provenance | **Pass** | 34 single-fault and 36 cross-service source fingerprints, recorded runner/dataset/corpus hashes and paired JSONL hashes match. Record-derived counts/rejections match summaries. Eight independent tamper probes refuse generation before writing output |
| Presentation assets | **Readable architecture and evidence SVGs; relative file links checked** | Vector assets and exact JSON inputs are committed with the documentation; diagrams separate full architecture from snapshot-demo scope |

The 1,817 regression passes and the 27 dedicated PostgreSQL passes cover **1,844 distinct collected Python tests across two runs**. This is not a claim that one PostgreSQL-enabled run of the entire suite occurred. PostgreSQL 14.19 is a locally available validation dependency, not a claim of equivalence to the deployed image version or topology.

## Reproduce the main checks

From the repository root after the documented setup:

```bash
python scripts/portfolio_demo.py
python scripts/render_portfolio_evidence.py
# Regression tooling is separate from the demo-only runtime snapshot.
python -m pip install 'pytest>=8.3,<9'
python -m pytest tests/test_portfolio_demo_offline.py services/orchestrator/tests services/mcp-server/tests \
  evals/tests fault-lab/app/tests deploy/kubernetes/tests -q -rs -p no:cacheprovider
(cd services/telemetry-gateway && go test ./...)
npm test
npm run test:stream-contract
```

For the optional PostgreSQL tests, provision a new UTF8 database with a name ending `_tests` and a single private socket under `/private/tmp`; point `INCIDENT_TEST_DATABASE_URL` at that owned test database. The tests enforce the local target and dedicated database where required. Do not use an existing application database. The verification instance used here was stopped in `finally`, and its temporary socket directory was removed after shutdown.

Validation used a credential-free environment. Docker Compose only rendered synthetic configuration using an isolated plugin directory; a real daemon endpoint was deliberately unavailable. Redis's owned Unix-socket test needed local bind permission outside the filesystem sandbox and left no persistent Redis data. Earlier attempts that lacked tool discovery or bind permission were superseded by the clean final regression run; tests were not weakened to pass.

## What remains unverified

- Fresh live Kubernetes fault acceptance against release images, all-eight current fault coverage, model semantic faithfulness and human-approved causal repair efficacy.
- Production SSO/per-user identity and multi-namespace production isolation.
- Installation in a new environment from downloaded artifacts: `requirements-demo.lock` records exact current runtime versions, but no fresh wheel installation or cross-platform acceptance was performed. Core manifests retain their ranges; artifact hashes are not locked.
- Current dependency audit reports version-matched critical/high findings in the existing Node/Python/Go runtime; exact feature/symbol reachability is not uniformly established. The faithful lock does not constitute security clearance. These findings require explicit remediation and retesting before public release.
- Authorship/license decisions and final publish-tree/history review; see the [release checklist](release-checklist.md).
