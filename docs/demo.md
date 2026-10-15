# Ten incidents through the real workflow

This demo is a short route into the backend. It reuses the existing evaluation runner's independently authored synthetic observations and the actual compiled LangGraph, rather than printing a scripted transcript.

## Run

From the repository root, install [the tested runtime snapshot](../requirements-demo.lock) and then both local services with `--no-deps --no-build-isolation`, as described in the README. The snapshot covers CPython 3.13.16 / macOS arm64 and pins versions plus SHA256 artifacts, with separately locked build tooling; other platforms are unverified. Then run:

```bash
python scripts/portfolio_demo.py
# Optional append-only local record:
python scripts/portfolio_demo.py --output work/my-demo.json
```

The runner deliberately selects one case from each of eight fault families, one healthy control, and one no-data control. Expected summary:

```json
{"cases": 10, "passed": 10, "model_calls": 0, "execution_attempts": 0}
```

The [retained small result](assets/demo-result.json) was produced during the 2026-10-13 local validation. Runtime IDs, observation timestamps and latency are omitted so the public output focuses on the observable contract.

## What to observe

| Input family | Proposed tool | Expected observable outcome |
|---|---|---|
| Database pool exhaustion | `rollback_release` | `waiting_approval` → explicit rejection → `rejected` |
| CPU saturation | `restart_deployment` | `waiting_approval` → explicit rejection → `rejected` |
| Memory growth / deployment regression | `rollback_release` | retained proposal, rejection, no execution |
| Kafka consumer lag | `restart_deployment` | retained proposal, rejection, no execution |
| Downstream timeout / retry storm / cache penetration | `none` | advisory diagnosis without mutation |
| Healthy / no-data controls | `none` | `insufficient_evidence`, no destructive proposal |

The test condition also checks service identity, expected family/action, exact state after an in-memory checkpoint round trip and zero execution attempts. The command exits nonzero on a failed contract. The snapshot tool implementation refuses a non-dry-run execution attempt; any such attempt is counted as a failure.

The fixture observations enter the workflow; expected labels stay in the scorer. The existing BM25 knowledge index supplies runbook evidence. The standalone entry point explicitly disables SDK tracing before imports and inside its execution context, even when a shell enables LangSmith/LangChain tracing. A [socket-deny regression](../tests/test_portfolio_demo_offline.py) checks this with synthetic tracing credentials and every connection blocked. There is no model, MCP network transport, PostgreSQL restart or Kubernetes operation. These boundaries keep the demo cheap and repeatable and define what its output proves.

## Read the code behind the result

1. [Demo selection and checks](../scripts/portfolio_demo.py).
2. [Existing snapshot runner and tools](../evals/run_workflow_benchmark.py).
3. [Compiled state machine](../services/orchestrator/incident_copilot/langgraph_workflow.py).
4. [Approval receipt signing](../services/orchestrator/incident_copilot/approval.py).
5. [Knowledge index](../services/mcp-server/incident_mcp/knowledge.py).

To explore real HTTP/SSE and the Go/MCP path, use the [full setup](project-reference.md#run-the-services). To inspect retained larger runs and their measured limits, use the [evidence guide](evidence.md).
