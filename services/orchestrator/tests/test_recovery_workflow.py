"""Compiled recovery graph, with recording fake tools and no external action."""
import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from incident_copilot import langgraph_workflow as workflow_module
from incident_copilot.api import app
from incident_copilot.approval import ApprovalReceiptSigner
from incident_copilot.guardrails import GuardrailViolation
from incident_copilot.langgraph_workflow import LangGraphIncidentWorkflow
from incident_copilot.mcp_client import McpToolError
from incident_copilot.models import ApprovalRequest, Evidence, IncidentQuestion
from incident_copilot.tools import DemoTelemetryTools


def setup_runtime(monkeypatch):
    clock = [datetime.now(UTC)]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz) if tz else clock[0].replace(tzinfo=None)

    monkeypatch.setattr(workflow_module, "datetime", Clock)
    context = {"tool": "restart_deployment", "service": "risk-engine", "namespace": "prod-sg",
        "cluster_context": "a" * 64, "deployment_uid": "recording-test-deployment",
        "resource_version": "11", "deployment_template_sha256": "b" * 64}
    tools = DemoTelemetryTools()

    async def action(**kwargs):
        if kwargs["dry_run"]:
            return {"accepted": True, "executed": False, "dry_run": True, "backend": "kubernetes",
                "preflight_mode": "kubernetes-server-dry-run", "target_context": context}
        return {"accepted": True, "executed": True, "execution_backend": "kubernetes",
                "idempotent_replay": False, "applied_generation": 2, "applied_template_sha256": "c" * 64}

    tools.restart_deployment = AsyncMock(side_effect=action)

    async def health(**kwargs):
        return {"source": "kubernetes_deployment_health", "read_only": True, "marker_status": "marker_matches",
            "cluster_context": context["cluster_context"], "deployment_uid": context["deployment_uid"],
            "service": context["service"], "namespace": context["namespace"],
            "observed_at": clock[0].isoformat(), "generation": 2, "observed_generation": 2,
            "template_sha256": "c" * 64, "desired_replicas": 1, "updated_replicas": 1,
            "ready_replicas": 1, "available_replicas": 1, "unavailable_replicas": 0, "total_replicas": 1}

    async def metrics(service, *, start, end):
        acknowledgement = initial[0]
        times = [acknowledgement + timedelta(seconds=seconds) for seconds in (5, 20, 35)]
        return [Evidence(kind="metric", source="prometheus-via-telemetry-gateway", service=service,
            title=metric, observation="Bounded recording fixture, not a runtime acceptance result",
            query=metric, observed_at=times[-1], score=0.9, attributes={"unit": unit,
                "data_origin": "prometheus_api", "discarded_samples": 0,
                "series": [{"timestamp": time.isoformat(), "value": value} for time in times if start <= time <= end]})
            for metric, unit, value in (("request_latency_p95", "milliseconds", 150), ("request_error_rate", "ratio", 0.01))]

    initial = [clock[0]]
    tools.query_deployment_health = AsyncMock(side_effect=health)
    tools.query_recovery_metrics = AsyncMock(side_effect=metrics)
    saver = InMemorySaver()

    def runtime():
        return LangGraphIncidentWorkflow(tools=tools, checkpointer=saver,
            approval_signer=ApprovalReceiptSigner("recording-recovery-test-secret"))

    return runtime, tools, clock, initial


def approval(state):
    return ApprovalRequest(decision="approve", actor="recording-test-operator", reason="Fake tool test only",
                           idempotency_key=f"{state.incident_id}-fake-recovery")


def test_acknowledged_action_waits_for_fresh_sustained_recovery_and_restores_without_redispatch(monkeypatch):
    async def scenario():
        factory, tools, clock, initial = setup_runtime(monkeypatch)
        runtime = factory()
        paused = await runtime.start(IncidentQuestion(question="Investigate risk-engine CPU saturation"))
        pending = await runtime.resume_with_approval(paused.incident_id, approval(paused))
        assert pending.status == "remediation_pending"
        assert pending.recovery["status"] == "pending"
        snapshot = await runtime.graph.aget_state(runtime._config(paused.incident_id))
        assert snapshot.next == ("wait_for_recovery",)
        assert snapshot.interrupts[0].value["kind"] == "recovery_check"
        assert not any(e.event == "workflow_completed" for e in pending.trace)
        dispatch_count = len(tools.restart_deployment.await_args_list)
        assert dispatch_count == 2  # one dry run, one fake acknowledged dispatch
        with pytest.raises(GuardrailViolation, match="human approval"):
            await runtime.resume_with_approval(paused.incident_id, approval(paused))
        clock[0] = initial[0] + timedelta(seconds=45)
        restored = factory()
        assert (await restored.get(paused.incident_id)) == pending
        resolved = await restored.recheck_recovery(paused.incident_id)
        assert resolved.status == "resolved"
        assert resolved.recovery["status"] == "verified"
        assert len(resolved.recovery["metric_evidence_ids"]) == 2
        assert set(resolved.recovery["metric_evidence_ids"]) <= {e.id for e in resolved.evidence}
        assert resolved.trace[-2].event == "workflow_completed"
        assert resolved.trace[-2].payload["recovery_verified"] is True
        assert len(tools.restart_deployment.await_args_list) == dispatch_count
        assert not (await restored.graph.aget_state(restored._config(paused.incident_id))).next
        with pytest.raises(GuardrailViolation, match="waiting"):
            await restored.recheck_recovery(paused.incident_id)
        assert len(tools.restart_deployment.await_args_list) == dispatch_count
        kwargs = tools.query_recovery_metrics.call_args.kwargs
        assert kwargs["start"] == initial[0] and kwargs["end"] == clock[0]
        assert "approval_receipt" not in resolved.model_dump_json()
    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["unavailable", "replaced_uid", "superseded_template", "unhealthy", "future"])
def test_recovery_failure_keeps_same_read_only_boundary_without_retrying_action(monkeypatch, failure):
    async def scenario():
        factory, tools, clock, initial = setup_runtime(monkeypatch)
        runtime = factory()
        paused = await runtime.start(IncidentQuestion(question="Investigate risk-engine CPU saturation"))
        await runtime.resume_with_approval(paused.incident_id, approval(paused))
        clock[0] = initial[0] + timedelta(seconds=45)
        original = tools.query_deployment_health.side_effect

        async def changed(**kwargs):
            if failure == "unavailable":
                raise McpToolError("private-provider-secret")
            result = await original(**kwargs)
            if failure == "replaced_uid":
                result["deployment_uid"] = "replacement-deployment"
            if failure == "superseded_template":
                result["template_sha256"] = "d" * 64
            if failure == "unhealthy":
                result["ready_replicas"] = 0
            if failure == "future":
                result["observed_at"] = (clock[0] + timedelta(seconds=1)).isoformat()
            return result

        tools.query_deployment_health.side_effect = changed
        pending = await factory().recheck_recovery(paused.incident_id)
        assert pending.status == "remediation_pending" and pending.recovery["status"] == "pending"
        assert (await runtime.graph.aget_state(runtime._config(paused.incident_id))).next == ("wait_for_recovery",)
        assert len(tools.restart_deployment.await_args_list) == 2
        assert "private-provider-secret" not in pending.model_dump_json()
    asyncio.run(scenario())


@pytest.mark.parametrize("result", [McpToolError("lost private acknowledgement"),
    None,
    {"accepted": True, "executed": True, "execution_backend": "kubernetes", "idempotent_replay": True},
    {"accepted": True, "executed": True, "execution_backend": "simulation"},
    {"accepted": True, "executed": True, "execution_backend": "kubernetes", "idempotent_replay": False,
        "applied_generation": True, "applied_template_sha256": "c" * 64}])
def test_unknown_replay_simulation_and_incomplete_ack_cannot_verify_recovery(monkeypatch, result):
    async def scenario():
        factory, tools, _, _ = setup_runtime(monkeypatch)
        runtime = factory()
        paused = await runtime.start(IncidentQuestion(question="Investigate risk-engine CPU saturation"))
        tools.restart_deployment = AsyncMock(**({"side_effect": result} if isinstance(result, Exception) else {"return_value": result}))
        state = await runtime.resume_with_approval(paused.incident_id, approval(paused))
        assert state.status == "remediation_pending" and state.recovery["status"] == "pending"
        tools.query_deployment_health.assert_not_awaited()
        tools.query_recovery_metrics.assert_not_awaited()
        assert not (await runtime.graph.aget_state(runtime._config(paused.incident_id))).next
        with pytest.raises(GuardrailViolation):
            await factory().recheck_recovery(paused.incident_id)
        tools.restart_deployment.assert_awaited_once()
    asyncio.run(scenario())


@pytest.mark.parametrize("field,value", [("applied_generation", 3), ("applied_template_sha256", "d" * 64),
    ("execution_acknowledged_at", "2026-01-01T00:00:00+00:00")])
def test_recovery_acknowledgement_cannot_be_rewritten_in_checkpoint(monkeypatch, field, value):
    async def scenario():
        factory, tools, _, _ = setup_runtime(monkeypatch)
        runtime = factory()
        paused = await runtime.start(IncidentQuestion(question="Investigate risk-engine CPU saturation"))
        pending = await runtime.resume_with_approval(paused.incident_id, approval(paused))
        pending.recovery[field] = value
        tools.query_deployment_health.reset_mock()
        tools.query_recovery_metrics.reset_mock()
        with pytest.raises(GuardrailViolation, match="acknowledgement"):
            await runtime._verify_recovery({"incident": pending.model_dump(mode="json")})
        tools.query_deployment_health.assert_not_awaited()
        tools.query_recovery_metrics.assert_not_awaited()
        assert len(tools.restart_deployment.await_args_list) == 2
    asyncio.run(scenario())


def test_api_recovery_check_is_operator_only_and_accepts_no_target_overrides(monkeypatch):
    factory, tools, clock, initial = setup_runtime(monkeypatch)
    read, operator = "recording-reader-" + "r" * 32, "recording-operator-" + "o" * 32
    monkeypatch.setenv("ORCHESTRATOR_AUTH_MODE", "required")
    monkeypatch.setenv("ORCHESTRATOR_READ_TOKEN", read)
    monkeypatch.setenv("ORCHESTRATOR_OPERATOR_TOKEN", operator)
    monkeypatch.setenv("ORCHESTRATOR_OPERATOR_ACTOR", "recording-test-operator")
    with TestClient(app) as client:
        app.state.workflow = factory()
        headers = {"Authorization": f"Bearer {operator}"}
        paused = client.post("/v1/incidents", headers=headers, json={"question": "Investigate risk-engine CPU saturation"}).json()
        approved = client.post(f"/v1/incidents/{paused['incident_id']}/approval", headers=headers,
            json={"decision": "approve", "actor": "recording-test-operator", "reason": "Fake tool test only",
                "idempotency_key": "api-fake-recovery-observation"})
        assert approved.json()["status"] == "remediation_pending"
        path = f"/v1/incidents/{paused['incident_id']}/recovery-check"
        assert client.post(path).status_code == 401
        assert client.post(path, headers={"Authorization": f"Bearer {read}"}).status_code == 403
        assert client.post(path, headers=headers, json={"service": "other-service"}).status_code == 400
        assert client.post(path + "?namespace=other-ns", headers=headers).status_code == 400
        clock[0] = initial[0] + timedelta(seconds=45)
        checked = client.post(path, headers=headers)
        assert checked.status_code == 200 and checked.json()["status"] == "resolved"
        assert checked.headers["cache-control"] == "no-store"
        assert client.post(path, headers=headers).status_code == 409
        assert len(tools.restart_deployment.await_args_list) == 2
        assert read not in checked.text and operator not in checked.text
