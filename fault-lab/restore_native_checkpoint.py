"""Fresh-process verification of a dedicated local test PostgreSQL incident."""
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/orchestrator"))

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver  # noqa: E402
from incident_copilot.approval import ApprovalReceiptSigner  # noqa: E402
from incident_copilot.langgraph_workflow import LangGraphIncidentWorkflow  # noqa: E402
from incident_copilot.models import IncidentState  # noqa: E402
from incident_copilot.store import PostgresIncidentRepository  # noqa: E402


class NoTools:
    def __getattr__(self, name):
        raise AssertionError("Checkpoint inspection must never invoke telemetry or remediation tools")


async def inspect(expected):
    url = os.environ["INCIDENT_TEST_DATABASE_URL"]
    async with AsyncPostgresSaver.from_conn_string(url) as saver:
        workflow = LangGraphIncidentWorkflow(tools=NoTools(), checkpointer=saver,
            approval_signer=ApprovalReceiptSigner("inspection-only-no-execution-secret"))
        restored = await workflow.get(expected.incident_id)
        snapshot = await workflow.graph.aget_state(workflow._config(expected.incident_id))
        history = await workflow.history(expected.incident_id)
    repository = await PostgresIncidentRepository.connect(url)
    try:
        stored = await repository.get(expected.incident_id)
    finally:
        await repository.close()
    checks = {
        "fresh_process_checkpoint_matches": restored is not None and restored.model_dump() == expected.model_dump(),
        "fresh_process_repository_matches": stored is not None and stored.model_dump() == expected.model_dump(),
        "checkpoint_history_retained": len(history) >= 2,
        "approval_interrupt_retained": ("human_approval" in snapshot.next if expected.status.value == "waiting_approval" else not snapshot.next),
    }
    return {"ok": all(checks.values()), "checks": checks, "history_checkpoint_count": len(history),
            "incident": restored.model_dump(mode="json") if restored else None}


if __name__ == "__main__":
    try:
        if sys.argv[1:] != ["--inspect-existing-test-checkpoint"]:
            raise ValueError("Explicit internal checkpoint inspection gate required")
        from verify_observed_topology import checkpoint_url
        checkpoint_url(True, True)
        payload = sys.stdin.read(10 * 1024 * 1024 + 1)
        if len(payload) > 10 * 1024 * 1024:
            raise ValueError("Inspection payload exceeds budget")
        result = asyncio.run(inspect(IncidentState.model_validate(json.loads(payload))))
        print(json.dumps(result))
        raise SystemExit(0 if result["ok"] else 1)
    except Exception as error:
        print(json.dumps({"ok": False, "error_type": type(error).__name__}))
        raise SystemExit(1) from None
