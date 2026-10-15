# OpsPilot portfolio brief

## One-line positioning

OpsPilot is an evidence-first production incident copilot that correlates live
telemetry, service dependencies, deployments, runbooks, past incidents, and
technical documentation, then pauses before any destructive Kubernetes action
until a named human approves the exact operation.

It is deliberately one durable, explainable state machine—not a collection of
agents chatting with one another.

Current scope (2026-10-08): implemented engineering mechanisms and reproducible
lab acceptance, not a production-complete platform. The current model's semantic
RCA/faithfulness and an explicitly approved live repair are still unverified.
See the [completion audit](completion-audit.md) before using a historical number
as a current project or resume claim.

## Why this project is technically credible

| Area | Implemented evidence |
| --- | --- |
| Agent runtime | Compiled LangGraph state machine with PostgreSQL checkpoints, real `interrupt()`/resume, concurrent read tools, and failure recovery |
| AI reasoning | Optional OpenAI Responses API structured-output adapter; citations are constrained to retained evidence IDs and usage metadata is traced |
| Tool protocol | Custom Streamable HTTP MCP server with eleven typed tools, JSON Schema validation, scopes, rate limits, retries, audit events, and target-bound signed approvals |
| Backend depth | Python/FastAPI orchestration plus a separate authenticated Go telemetry gateway |
| Observability | Live Prometheus metrics, Loki logs, Tempo traces, OpenTelemetry Collector, and provisioned Grafana dashboard |
| Safety | Input injection checks, citation integrity, dry-run, exact action/namespace/target-bound approval, RBAC, and cross-replica idempotency; these do not prove semantic grounding or repair efficacy |
| Deployment | Compose, a hardened 21-resource Kustomize base, and a repeatable Kind rollout/safety verifier |
| Evaluation | Current 150 single-fault and 32 cross-service synthetic contracts, a BM25 retrieval benchmark, eight bounded lab modes, an actual-graph model runner and independent semantic-review protocol; historical prompt-comparison outputs remain separate |
| Console reliability | Persisted node-boundary diagnosis streaming, partial-prefix read-only inspection, memory-only scoped development credentials, and explicit completion before approval |

## Current engineering verification

These measurements have distinct scopes; passing a contract is not an RCA
accuracy score. Cluster lifecycle time is not model diagnostic latency.

| Measurement | Result | Evidence and boundary |
| --- | ---: | --- |
| Backend/evaluation/Kubernetes-verifier regression | 1680 passed, no skips | Includes 24 dedicated PostgreSQL opt-ins; not 1680 real incidents |
| Frontend regression | 209 passed; typecheck/build pass | Pure frontend tests, separate from real backend/cluster acceptance |
| Backend-to-frontend stream contract | 1 passed, no skip | `npm run test:stream-contract`; real in-process graph/Pydantic wire plus an injected fragmented fetch body |
| Actual HTTP console acceptance | Progress → completion → reject | 46 diagnostic / 50 final events; fixture/memory backend, no execution; [verification](verification.md#2026-10-08-durable-diagnosis-stream-and-console-contract) |
| Single-fault workflow contracts | 150 / 150 | [Current report](../evals/results/workflow_benchmark_20261007T164404Z_f5bb6a04.json); 120 positives/30 negatives, no model or execution |
| Cross-service workflow contracts | 32 / 32 | [Current report](../evals/results/cross_service_benchmark_20261007T164402Z_be5fa187b20c48189ff819ca80373e32.json); bounded synthetic paths/candidates, no causal proof |
| Current Kind CPU acceptance | 35 / 35 | [Report](../deploy/kubernetes/results/kind_live_cpu-spike_20261007T170542Z_19947180.json); real preflight/approval checkpoint/Pod replacement, no approval or repair |
| Current Kind PostgreSQL pool acceptance | 42 / 42 | [Report](../deploy/kubernetes/results/kind_live_db-pool-exhaustion_20261007T171001Z_e8495cc4.json); actual four-lease exhaustion, log/span identity and exact restoration, not Agent-selected mitigation |
| Current Kind timeout/memory acceptance | 35 / 35 each | [Four-case aggregate](../deploy/kubernetes/results/kind_fault_matrix_stream_four_20261007T1715Z_4e2e91.json); bounded HTTP timeout/application allocation and exact restoration |

The current cluster reports bind the same 76 source inputs and four image IDs.
Four cases remain in progress at this snapshot; do not promote these four
reports to a complete eight-case matrix. CPU is a bounded application loop/gauge;
consumer lag, cache miss and deployment regression still use synthetic signals.

## Historical measurements — not current model or runtime acceptance

The original reports are retained with their dates and scoring contracts. In
particular, the 450-call comparison below used structured prompts and supplied
signal contexts, not end-to-end runs of the current compiled LangGraph.

| Measurement | Result | Evidence |
| --- | ---: | --- |
| Observable fault scenarios | 8 / 8 passed | `fault-lab/results/2026-07-23-observability-matrix.json` |
| Live Agent diagnosis scenarios | 8 / 8 passed | `fault-lab/results/2026-08-08-agent-scenario-matrix.json` |
| Live diagnostic latency | 103.32 ms mean, 143.67 ms p95 | same Agent scenario matrix |
| Unsafe execution prevention | 5 / 5 destructive proposals rejected; 0 executed | same Agent scenario matrix |
| Local Kubernetes rollout | 5 / 5 workloads Ready; namespace RBAC `yes`, `kube-system` `no` | `deploy/kubernetes/results/2026-08-01-kind-verification.json` |
| Incident retrieval set | 150 cases | `evals/dataset/incidents.jsonl` |
| BM25 retrieval Recall@5 | 100% | `evals/results/retrieval_benchmark.json` |
| BM25 retrieval NDCG@5 | 97.9% | `evals/results/retrieval_benchmark.json` |
| Retrieval latency | 0.240 ms mean, 0.317 ms p95 | local 2026-08-01 run |
| Historical harness-scored root-cause Top-1 | 19.3% prompt; 64.0% RAG; 81.3% validation variant | `evals/results/model_benchmark.json`; not current graph/model or unbiased production accuracy |
| Historical labeled-citation grounded rate | 0%; 99.3%; 100% respectively | same model benchmark; ID/relevance policy, not semantic faithfulness |
| Destructive-action guardrail rate | 100% for all variants; 0 unguarded actions | same model benchmark |
| Model cost per incident | $0.00318; $0.00389; $0.00468 respectively | same model benchmark |
| Model request latency | 2.63 s; 2.41 s; 2.79 s mean respectively | same model benchmark |
| Historical restart recovery | 12 LangGraph checkpoints, 21 ordered trace events | `docs/verification.md`; the old resolved label proved acknowledgement, not recovery |

The credentialed comparison ran all 150 cases through each variant using the
same `gpt-5.6-terra` model on 2026-08-08. It made 450 checkpointed Responses API
calls and cost $1.76228 at the explicitly recorded standard token prices. The
validation variant's harness score was 62.0 percentage points above the
single-prompt baseline. Its policies consume labeled cases and are not a
deployable or unbiased diagnosis baseline. Grounded/citation checks measure
retained IDs and labeled relevance, not whether claims follow from evidence.
Use the [scoring definitions and separate offline rescore](../evals/README.md#scoring-definitions-and-offline-rescoring)
for interpretation. No current model-quality or latency/cost-reduction claim is
supported by that comparison. The historical cost is not a current price quote.

The historical fixed live matrix passed 8/8 from generic questions
without fault labels through orchestrator → MCP → Go gateway →
Prometheus/Loki/Tempo. Every incident retained metric, log, and trace evidence
plus a tool-call/result trace. Five restart/rollback proposals were rejected
before execution; the three advisory-only incidents completed without an
approval interrupt.

## Five-minute demo story

1. Follow the [fault-lab setup](../fault-lab/README.md) for a configured live
   Compose stack, including `compose.real-pool.yml` for actual PostgreSQL pool
   exhaustion. Configure development credentials through the console's exact
   trusted backend connection; never paste production secrets. The standalone
   Kind acceptance command has a private cluster/config and does not automatically
   expose a public console endpoint.
2. Ask: “为什么 payment-service 今天延迟突然升高？”
3. Show provisional checkpoints, then explicit completion; inspect current
   metric windows, retained log/trace identity, dependency candidates and citations.
4. Show deployment provenance separately. The live lab's synthetic release
   catalog does not establish v142 causation or a live v141 rollback target.
5. Explain why that live pool diagnosis is advisory-only. For the separate
   standalone simulation, show the explicitly simulated rollback preview and
   reject it; do not present it as a live repair.
6. Open the complete trace and read-only checkpoint history. A recorded operation
   acknowledgement would still require the separate rollout/SLI recovery gate.
7. Run the [isolated current-image Kind verifier](../deploy/kubernetes/LIVE_FAULT_LAB.md)
   with explicit images/scenario, then the offline matrix verifier with explicit
   immutable reports. Both keep missing cases and fault fidelity visible.

## Resume bullets available now

**AI Production Incident Copilot** — Go, Python, LangGraph, MCP, Kubernetes,
OpenTelemetry, Prometheus, Loki, PostgreSQL, Redis

- Built an evidence-first incident-response platform that correlates metrics,
  logs, service dependencies, deployments, runbooks, past incidents, and
  technical documents through eleven custom MCP tools across a repeatable
  microservice fault lab.
- Implemented durable LangGraph workflows with PostgreSQL checkpoints,
  concurrent telemetry collection, structured model reasoning, evidence
  validation, human approval interrupts, resume-after-restart, authenticated
  persisted diagnosis streaming and incident traces.
- Engineered guarded Kubernetes restart/rollback execution with scoped service
  accounts, namespace allow-lists, signed action-bound approval receipts,
  dry-runs, `resourceVersion` concurrency control, and cross-replica idempotency.
- Verified current CPU and actual PostgreSQL pool scenarios in isolated Kind
  clusters, including scoped server-side dry-runs, read-only checkpoint/interrupt
  inspection, exact restoration in replacement API Pods and owned cleanup.
- Created 150 single-fault and 32 bounded cross-service workflow contracts plus
  an 18-document incident knowledge corpus; retained source-bound evaluation
  artifacts and a separate actual-graph model/semantic-review protocol.

## Claims that still require new evidence

Do not put these on a resume until a separate optimization experiment measures
them:

- Reduced diagnostic latency or token cost by **XX%** through parallel tool
  execution, context compression, caching, or model routing.
- Current model root-cause/semantic faithfulness **XX%**, or an approved live
  action's **XX%** recovery efficacy. A passed contract or valid citation ID does
  not fill these fields, and historical prompt harness scores are not substitutes.

## Strong interview talking points

- Why one state machine gives clearer checkpoints, retries, and ownership than
  multi-agent role-play.
- Why telemetry queries are allow-listed behind a Go gateway instead of letting
  a model emit arbitrary PromQL or LogQL.
- Why approval must bind actor, incident, tool, service, and target—not just a
  generic “yes”.
- Why in-memory idempotency fails with two MCP replicas, and how a Deployment
  annotation plus `resourceVersion` closes the race.
- Why retrieval and generation quality are measured separately.
- Why model citations are checked against retained evidence IDs even when
  Structured Outputs succeeds.

## Honest current limits

- The historical model comparison uses supplied synthetic signal contexts and
  labeled harness policies; it does not establish current graph quality,
  semantic faithfulness, production accuracy or cross-model generality.
- The Kustomize base has a verified local Kind rollout; no managed production
  cluster, public ingress, TLS, or managed-secret deployment is claimed.
- The fault lab and knowledge corpus are synthetic; production adapters would
  need organization-specific telemetry, identity, retention, and redaction.
- Static bearer tokens and memory-only console input are development adapters,
  not production SSO. Per-user role mapping and namespace/environment-scoped
  telemetry remain unfinished; action namespace binding does not isolate reads.
- The included PostgreSQL manifest is a portfolio base, not a highly available
  production database design.
