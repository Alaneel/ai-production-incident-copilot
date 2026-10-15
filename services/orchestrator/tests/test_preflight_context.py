"""Actual compiled graph/MCP boundary; Kubernetes is a non-network MockTransport."""
import asyncio
import copy
import json
from collections import defaultdict, deque
from unittest.mock import AsyncMock

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver

from incident_copilot.approval import ApprovalReceiptSigner
from incident_copilot.guardrails import GuardrailViolation
from incident_copilot.langgraph_workflow import LangGraphIncidentWorkflow
from incident_copilot.mcp_client import McpTelemetryTools, McpToolError
from incident_copilot.models import (
    ApprovalDecision, ApprovalRequest, IncidentQuestion, normalize_target_context, target_context_digest,
)
from incident_copilot.tools import DemoTelemetryTools
from incident_mcp import server as mcp_server
from incident_mcp.kubernetes import KubernetesRemediator
from incident_mcp.security import ApprovalReceiptVerifier, TokenAuthenticator
from incident_mcp.target_context import (
    normalize_target_context as mcp_normalize, target_context_digest as mcp_digest,
)
from incident_mcp.tools import ToolRegistry

SECRET = "preflight-context-test-only-secret"


def context(tool="restart_deployment"):
    value = {"tool": tool, "service": "risk-engine", "namespace": "prod-sg",
             "cluster_context": "a" * 64, "deployment_uid": "test-deployment-uid",
             "resource_version": "7", "deployment_template_sha256": "b" * 64}
    if tool == "rollback_release":
        value.update(revision="141", replicaset_uid="test-rs-uid", rollback_template_sha256="c" * 64)
    return value


@pytest.mark.parametrize("tool", ["restart_deployment", "rollback_release"])
def test_independent_wire_models_normalize_and_hash_identically(tool):
    value = context(tool)
    assert normalize_target_context(value) == mcp_normalize(value)
    assert target_context_digest(value) == mcp_digest(value)
    assert target_context_digest(dict(reversed(list(value.items())))) == mcp_digest(value)
    if tool == "restart_deployment":
        assert normalize_target_context(value)["revision"] is None


@pytest.mark.parametrize("change", [
    {"cluster_context": ""}, {"deployment_uid": " "}, {"resource_version": 7},
    {"unexpected": "ignored?"}, {"revision": "141"}, {"service": "other-service"},
    {"namespace": "other-ns"}, {"tool": "rollback_release"},
])
def test_malformed_or_wrong_scope_preflight_never_interrupts(change):
    async def run():
        tools = DemoTelemetryTools()
        tools.restart_deployment = AsyncMock(return_value={
            "accepted": True, "executed": False, "dry_run": True, "backend": "kubernetes",
            "preflight_mode": "kubernetes-server-dry-run", "target_context": {**context(), **change},
        })
        runtime = LangGraphIncidentWorkflow(tools=tools, checkpointer=InMemorySaver(),
                                           approval_signer=ApprovalReceiptSigner(SECRET))
        state = await runtime.start(IncidentQuestion(question="Investigate risk-engine CPU saturation"))
        assert state.status == "remediation_failed" and not state.remediation.dry_run_passed
        snapshot = await runtime.graph.aget_state(runtime._config(state.incident_id))
        assert not snapshot.interrupts
        assert all(call.kwargs["dry_run"] is True for call in tools.restart_deployment.await_args_list)
    asyncio.run(run())


@pytest.mark.parametrize("result", [
    {"accepted": True},
    {"accepted": True, "executed": True, "dry_run": True},
    {"accepted": True, "executed": False, "dry_run": True,
     "backend": "kubernetes", "preflight_mode": "kubernetes-server-dry-run"},
    {"accepted": True, "executed": False, "dry_run": True, "backend": "kubernetes",
     "preflight_mode": "simulation", "target_context": None},
    McpToolError("private provider diagnostic"),
    TimeoutError("private provider diagnostic"),
])
def test_missing_ambiguous_or_failed_preflight_does_not_fall_back_to_simulation(result):
    async def run():
        tools = DemoTelemetryTools()
        tools.restart_deployment = AsyncMock(**({"side_effect": result} if isinstance(result, Exception)
                                                else {"return_value": result}))
        runtime = LangGraphIncidentWorkflow(tools=tools, checkpointer=InMemorySaver(),
                                           approval_signer=ApprovalReceiptSigner(SECRET))
        state = await runtime.start(IncidentQuestion(question="Investigate risk-engine CPU saturation"))
        assert state.status == "remediation_failed" and state.remediation.preflight_mode is None
        assert "private provider" not in state.model_dump_json()
        assert not (await runtime.graph.aget_state(runtime._config(state.incident_id))).interrupts
    asyncio.run(run())


@pytest.mark.parametrize("change", [{"deployment_uid": "new-uid"}, {"cluster_context": "d" * 64}, None])
def test_restored_proposal_cannot_replace_or_omit_its_retained_preflight_context(change):
    async def run():
        tools = DemoTelemetryTools()
        tools.restart_deployment = AsyncMock(return_value={
            "accepted": True, "executed": False, "dry_run": True, "backend": "kubernetes",
            "preflight_mode": "kubernetes-server-dry-run", "target_context": context(),
        })
        runtime = LangGraphIncidentWorkflow(tools=tools, checkpointer=InMemorySaver(),
                                           approval_signer=ApprovalReceiptSigner(SECRET))
        state = await runtime.start(IncidentQuestion(question="Investigate risk-engine CPU saturation"))
        state.approval_decision, state.approval_actor = ApprovalDecision.APPROVE, "test-operator"
        prepared = await runtime._prepare_execution({"incident": {**state.model_dump(mode="json"),
            "_approval_idempotency_key": "context-tamper-operation-key"}})
        if change is None:
            state.remediation.arguments.pop("target_context")
        else:
            state.remediation.arguments["target_context"].update(change)
        with pytest.raises(GuardrailViolation, match="context|target"):
            runtime._approved_target_context(state)
        with pytest.raises(GuardrailViolation, match="context|target"):
            await runtime._prepare_execution({"incident": {**state.model_dump(mode="json"),
                "_approval_idempotency_key": "context-tamper-operation-key"}})
        prepared["incident"]["remediation"]["arguments"] = state.remediation.arguments
        for event in prepared["incident"]["trace"]:
            if event["event"] == "execution_intent_prepared":
                event["payload"]["arguments"] = copy.deepcopy(state.remediation.arguments)
        with pytest.raises(GuardrailViolation, match="context|target"):
            await runtime._execute(prepared)
        tools.restart_deployment.assert_awaited_once()
    asyncio.run(run())


@pytest.mark.parametrize("replace_uid", [False, True])
@pytest.mark.parametrize("tool", ["restart_deployment", "rollback_release"])
def test_graph_checkpoint_receipt_and_status_keep_actual_mcp_preflight_identity(monkeypatch, replace_uid, tool):
    async def run():
        service = "risk-engine" if tool == "restart_deployment" else "payment-service"
        deployment = {"metadata": {"name": service, "namespace": "prod-sg", "uid": "original-uid", "resourceVersion": "7"},
                      "spec": {"template": {"metadata": {"annotations": {}},
                                            "spec": {"containers": [{"name": "app", "image": "test/app:142"}]}}}}
        rs = {"metadata": {"name": "owned-rs", "namespace": "prod-sg", "uid": "original-rs-uid",
                           "annotations": {"deployment.kubernetes.io/revision": "141"},
                           "ownerReferences": [{"apiVersion": "apps/v1", "kind": "Deployment", "name": service,
                                                "uid": "original-uid", "controller": True}]},
              "spec": {"template": {"spec": {"containers": [{"name": "app", "image": "test/app:141"}]}}}}
        calls = []

        def kube(request):
            calls.append((request.method, request.url.path, request.url.params.get("dryRun")))
            if request.url.path.endswith("/replicasets"):
                return httpx.Response(200, json={"items": [rs]})
            if request.method == "PATCH":
                patch = json.loads(request.content)
                changed = copy.deepcopy(deployment)
                changed["spec"]["template"]["metadata"]["annotations"].update(patch["spec"]["template"]["metadata"]["annotations"])
                if "containers" in patch["spec"]["template"].get("spec", {}):
                    changed["spec"]["template"]["spec"]["containers"] = patch["spec"]["template"]["spec"]["containers"]
                if request.url.params.get("dryRun") != "All":
                    deployment.update(changed)
                return httpx.Response(200, json=changed)
            return httpx.Response(200, json=deployment)

        async with httpx.AsyncClient(base_url="https://kubernetes.test", transport=httpx.MockTransport(kube)) as kube_client:
            backend = KubernetesRemediator(api_url="https://kubernetes.test", allowed_namespaces={"prod-sg"}, client=kube_client)
            monkeypatch.setattr(mcp_server, "registry", ToolRegistry(ApprovalReceiptVerifier(SECRET), remediator=backend))
            monkeypatch.setattr(mcp_server, "authenticator", TokenAuthenticator("preflight-read", "preflight-operator"))
            monkeypatch.setattr(mcp_server, "_rate_windows", defaultdict(deque))
            monkeypatch.setattr(mcp_server, "_rate_lock", asyncio.Lock())
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=mcp_server.app)) as client:
                adapter = McpTelemetryTools(url="http://mcp.test/mcp", read_token="preflight-read",
                                           operator_token="preflight-operator", client=client)
                adapter._call_tool = AsyncMock(wraps=adapter._call_tool)
                tools = DemoTelemetryTools()
                tools.restart_deployment, tools.rollback_release = adapter.restart_deployment, adapter.rollback_release
                tools.query_operation_status = adapter.query_operation_status
                saver = InMemorySaver()
                runtime = LangGraphIncidentWorkflow(tools=tools, checkpointer=saver, approval_signer=ApprovalReceiptSigner(SECRET))
                question = "Investigate risk-engine CPU saturation" if tool == "restart_deployment" else "Why is payment-service slow?"
                state = await runtime.start(IncidentQuestion(question=question))
                assert state.status == "waiting_approval"
                observed = state.remediation.arguments["target_context"]
                assert state.remediation.preflight_mode == "kubernetes-server-dry-run"
                assert observed["deployment_uid"] == "original-uid" and observed["service"] == service
                assert sum(method == "PATCH" and dryrun == "All" for method, _, dryrun in calls) == 1
                assert deployment["spec"]["template"]["metadata"]["annotations"] == {}
                snapshot = await runtime.graph.aget_state(runtime._config(state.incident_id))
                assert snapshot.interrupts[0].value["arguments"]["target_context"] == observed
                restored = LangGraphIncidentWorkflow(tools=tools, checkpointer=saver, approval_signer=ApprovalReceiptSigner(SECRET))
                assert (await restored.get(state.incident_id)).model_dump() == state.model_dump()
                if replace_uid:
                    deployment["metadata"]["uid"] = "replacement-uid"
                completed = await restored.resume_with_approval(state.incident_id, ApprovalRequest(
                    decision="approve", actor="preflight-operator", reason="Mock Kubernetes only", idempotency_key="preflight-context-execution-key"))
                dispatch = next(call.args[1] for call in adapter._call_tool.await_args_list
                                if call.args[0] == tool and call.args[1].get("dry_run") is False)
                assert dispatch["target_context"] == observed
                payload = ApprovalReceiptVerifier(SECRET).verify(dispatch["approval_receipt"], tool=tool, service=service,
                    namespace="prod-sg", target="141" if tool == "rollback_release" else "prod-sg",
                    target_context_sha256=mcp_digest(observed))
                assert payload["version"] == 3 and payload["actor"] == "preflight-operator"
                persistent_patches = sum(method == "PATCH" and dryrun is None for method, _, dryrun in calls)
                assert persistent_patches == (0 if replace_uid else 1)
                assert completed.status == "remediation_pending"
                assert completed.recovery["status"] == "pending"
                assert completed.recovery["applied_generation"] is None
                intent = next(event.payload for event in completed.trace if event.event == "execution_intent_prepared")
                assert intent["arguments"]["target_context"] == observed
                # Fresh MCP process: inspection reconstructs the original context-bound fingerprint.
                monkeypatch.setattr(mcp_server, "registry", ToolRegistry(ApprovalReceiptVerifier(SECRET), remediator=backend))
                before = len(calls)
                if replace_uid:
                    with pytest.raises(McpToolError):
                        await restored.inspect_operation(state.incident_id)
                else:
                    inspected = await restored.inspect_operation(state.incident_id)
                    assert inspected["observation"]["status"] == "marker_matches"
                assert all(method == "GET" for method, _, _ in calls[before:])
                assert "apr_" not in completed.model_dump_json()
    asyncio.run(run())
