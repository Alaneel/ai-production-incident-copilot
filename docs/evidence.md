# Evidence and what it establishes

The public entry point uses small, explicit claims with links to the exact retained artifacts. Synthetic evaluation, live dependency tests, Kubernetes experiments and model quality measure different things.

## Fresh release-runtime synthetic workflow runs

| Artifact | Recorded result | Scope |
|---|---|---|
| [Single-fault workflow summary](../evals/results/workflow_benchmark_20261014T150511Z_55a6e188.json) | 150 cases: 120 fault cases and 30 controls; no recorded contract failures; 75 destructive proposals explicitly rejected; zero execution attempts/model calls | Actual compiled LangGraph + BM25 over synthetic snapshots; in-memory checkpoints. Eight canonical fault families varied by service/seed, not 150 independent failure modes |
| [Cross-service summary](../evals/results/cross_service_benchmark_20261014T150505Z_390ff20a414f45cca5456e475ed6767f.json) | 32 fixture structures; all structural contracts pass; zero mutation attempts/model calls | Directed path provenance, local and complaint evidence, timing, ambiguity and abstention. Synthetic candidate service/family localization, not causal proof |
| [Small current demo](assets/demo-result.json) | Ten cases pass, zero execution attempts/model calls | Fresh 2026-10-13 UTC replay of eight families plus two controls on the exact hashed release runtime |

These two release runs were measured on **2026-10-14 UTC** after dependency remediation. Socket connections were denied and ambient SDK tracing was enabled adversarially; both runners disable exporters and establish an explicit tracing-off context. The generator verifies 34 workflow and 36 cross-service source fingerprints, paired JSONL records, input/corpus hashes, runtime/build lock hashes and the provenance helper. Current image-lock and all seven release image recipes are also bound; that binding does not execute the images. Both summaries retain CPython 3.13.16 and exact observed service-runtime distribution versions. No scorer or workflow business rule changed for this replay.

The original [2026-10-07 UTC workflow](../evals/results/workflow_benchmark_20261007T164404Z_f5bb6a04.json) and [cross-service run](../evals/results/cross_service_benchmark_20261007T164402Z_be5fa187b20c48189ff819ca80373e32.json) remain immutable historical artifacts. Their earlier runner/dependency bytes are not the current release environment and are not presented as current acceptance.

```bash
python scripts/render_portfolio_evidence.py
```

The generated [SVG](assets/evidence.svg) and [bounded data extract](assets/evidence.json) contain only presentation fields. The full summaries and case records retain the original provenance and limitations. No latency claim is used in the front page: a synthetic in-process workflow duration would be misleading as production diagnosis latency.

## Dependency and Kubernetes evidence

The [full project reference](project-reference.md#verification-status) preserves prior source-specific tests and acceptance. In particular, the retained [four-case Kubernetes aggregate](../deploy/kubernetes/results/kind_fault_matrix_stream_four_20261007T1715Z_4e2e91.json) accepts CPU, actual PostgreSQL pool exhaustion, downstream timeout and bounded application memory growth, while explicitly retaining `full_fault_matrix=false`.

Those experiments did not execute a human-approved agent-selected repair or call a model. Removing the injected fault is not proof that a proposed action mitigates a real incident. CPU and memory injections are bounded application scenarios, not host-scale load or uncontrolled production leaks. The historical portfolio phase used a separate owned PostgreSQL 14.19 instance; it did not match the current deployed container configuration. Current real Compose acceptance passed 39 checks against the patched images. Two bounded current Kind attempts failed during rollout with actual PostgreSQL/Redis `No space left on device` diagnostics; zero Kubernetes fault checks ran. Both attempts removed only their owned resources. The full eight-scenario matrix remains unexecuted; historical results are not promoted to current acceptance.

The [release validation record](release-verification-2026-10-13.md) records the fresh regression suite, dedicated PostgreSQL checks, packaged platform acceptance and explicit limitations.

## Claims kept out of the front page

- Deterministic classifier agreement is not LLM root-cause accuracy or semantic faithfulness.
- An approval receipt and Kubernetes acknowledgement are not recovery evidence; the implemented recovery gate requires rollout/SLI observations.
- Source fingerprints do not prove multi-namespace isolation, current cluster image acceptance or the efficacy of a suggested repair.
- Heuristic prompt screening is not a complete prompt-injection defense; tool permissions and explicit approvals remain enforcement boundaries.
- Development Reader/Operator credentials are not production per-user identity or SSO.

See [model evaluation](model-evaluation-plan.md), [the evaluation guide](../evals/README.md), and [remaining completion gaps](completion-audit.md) before making broader claims.
