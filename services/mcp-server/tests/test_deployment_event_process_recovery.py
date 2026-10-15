"""Real PostgreSQL/process recovery; Kubernetes itself remains HTTP-mocked."""
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys

import pytest


WRITER = r"""
import asyncio, json, os
from unittest.mock import patch
import httpx
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from incident_copilot.approval import ApprovalReceiptSigner
from incident_copilot.langgraph_workflow import LangGraphIncidentWorkflow
from incident_copilot.mcp_client import McpTelemetryTools
from incident_copilot.models import IncidentQuestion
from incident_copilot.store import PostgresIncidentRepository
from incident_mcp.deployments import KubernetesDeploymentReader
from incident_mcp.server import app, registry
from test_deployment_events import api_handler
from current_metric_fixture import current_metric_fixture

async def main():
    requests = []
    url = os.environ["INCIDENT_TEST_DATABASE_URL"]
    async with httpx.AsyncClient(base_url="https://kubernetes.test",
            transport=httpx.MockTransport(api_handler(requests, mode=os.environ["EVENT_RECOVERY_MODE"]))) as api:
        reader = KubernetesDeploymentReader(api_url="https://kubernetes.test",
            allowed_namespaces={"prod-sg"}, include_events=True, client=api)
        with patch.object(registry, "deployment_reader", reader), current_metric_fixture(registry):
            async with httpx.AsyncClient(base_url="http://mcp.test",
                    transport=httpx.ASGITransport(app=app)) as client:
                tools = McpTelemetryTools(url="http://mcp.test/mcp", read_token="local-read-token",
                    operator_token="local-operator-token", client=client)
                async with AsyncPostgresSaver.from_conn_string(url) as saver:
                    await saver.setup()
                    workflow = LangGraphIncidentWorkflow(tools=tools, checkpointer=saver,
                        approval_signer=ApprovalReceiptSigner("local-approval-secret-change-me"))
                    state = await workflow.start(IncidentQuestion(
                        question="Why is payment-service slow?", environment="prod-sg"))
                    repository = await PostgresIncidentRepository.connect(url)
                    try:
                        await repository.save(state)
                    finally:
                        await repository.close()
    print(json.dumps({"state": state.model_dump(mode="json"), "requests": len(requests),
        "read_only_requests": all(request.method == "GET" for request in requests)}), flush=True)
    await asyncio.Event().wait()
asyncio.run(main())
"""


@pytest.mark.skipif(not os.getenv("INCIDENT_TEST_DATABASE_URL"), reason="isolated PostgreSQL test URL required")
@pytest.mark.skipif(os.name != "posix", reason="SIGKILL recovery requires POSIX")
@pytest.mark.parametrize("mode,status,event_count", [("available", "available", 1), ("forbidden", "unavailable", 0)])
def test_event_context_survives_sigkill_and_fresh_tool_free_reader(mode, status, event_count):
    root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(root / "fault-lab"))
    from verify_observed_topology import checkpoint_url
    checkpoint_url(True, True)
    # Do not inherit model credentials, external telemetry endpoints or cluster
    # feature switches. All tool HTTP traffic uses in-process mock transports.
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("OPENAI_", "KUBERNETES_", "MCP_", "TELEMETRY_"))
                   and key not in {"DATABASE_URL", "REDIS_URL"}}
    environment.update(
        PYTHONPATH=os.pathsep.join(str(root / path) for path in (
            "services/orchestrator", "services/mcp-server", "services/mcp-server/tests")),
        LANGGRAPH_STRICT_MSGPACK="true", EVENT_RECOVERY_MODE=mode,
    )
    writer = subprocess.Popen([sys.executable, "-c", WRITER], cwd=root, env=environment,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        ready, _, _ = select.select([writer.stdout], [], [], 20)
        assert ready, "Writer did not report its committed checkpoint"
        line = writer.stdout.readline()
        assert line, "Writer exited before reporting its checkpoint"
        report = json.loads(line)
        expected = report["state"]
        assert report["requests"] == 4 and report["read_only_requests"]
        assert expected["status"] == "waiting_approval"
        assert expected["approval_actor"] is expected["approval_decision"] is None
        assert expected["remediation"]["arguments"]["revision"] == "2"
        events = [item for item in expected["evidence"] if item["kind"] == "deployment_event"]
        assert len(events) == event_count
        comparison, = [item["payload"] for item in expected["trace"]
                       if item["event"] == "deployment_event_window_comparison"]
        assert comparison["timeline_statuses"] == [status]
        assert comparison["causation_verified"] is False
        assert len(comparison["event_observations"]) == event_count
        if events:
            assert events[0]["attributes"]["first_observed_at"] is not None
            assert events[0]["attributes"]["last_observed_at"] is not None
            observation, = comparison["event_observations"]
            assert observation["evidence_id"] == events[0]["id"]
            assert observation["revision_at_event_verified"] is False
            assert observation["individual_occurrence_in_window_verified"] is False
        assert not any(item["step"] == "execute_or_simulate" for item in expected["trace"])
        assert "private-api-token" not in line and "private-permission-error" not in line
        assert writer.poll() is None
        writer.kill()
        assert writer.wait(timeout=5) == -signal.SIGKILL
    finally:
        if writer.poll() is None:
            writer.kill()
            writer.wait(timeout=5)
        writer.stdout.close()
        writer.stderr.close()

    # Existing inspection helper has no generator and raises on any tool access.
    reader = subprocess.Popen([sys.executable, str(root / "fault-lab/restore_native_checkpoint.py"),
                               "--inspect-existing-test-checkpoint"], cwd=root, env=environment,
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        output, _ = reader.communicate(input=json.dumps(expected), timeout=30)
        assert reader.pid != writer.pid
        assert reader.returncode == 0, "Fresh tool-free recovery process failed"
        restored = json.loads(output)
        assert restored["ok"] and all(restored["checks"].values())
        assert restored["checks"]["approval_interrupt_retained"]
        assert restored["history_checkpoint_count"] >= 2
        assert restored["incident"] == expected
    finally:
        if reader.poll() is None:
            reader.kill()
            reader.wait(timeout=5)
        reader.stdin.close()
        reader.stdout.close()
        reader.stderr.close()
