import asyncio
from contextlib import nullcontext
from copy import deepcopy
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver

from incident_mcp.deployments import KubernetesDeploymentReader, REVISION
from incident_mcp.kubernetes import KubernetesRemediationError, KubernetesRemediator
from incident_mcp.security import ApprovalReceiptVerifier, Principal
from incident_mcp.server import app, registry
from incident_mcp.tools import ToolRegistry
from incident_copilot.approval import ApprovalReceiptSigner
from incident_copilot.langgraph_workflow import LangGraphIncidentWorkflow
from incident_copilot.mcp_client import McpTelemetryTools
from incident_copilot.models import IncidentQuestion
from current_metric_fixture import current_metric_fixture


def fixtures():
    deployment = {"metadata": {"name": "payment-service", "namespace": "prod-sg", "uid": "owned-uid",
        "resourceVersion": "100", "annotations": {REVISION: "4"}},
        "spec": {"template": {"spec": {"containers": [{"name": "payment", "image": "test:v4"}]}}}}
    def rs(revision, owner="owned-uid"):
        return {"metadata": {"namespace": "prod-sg", "name": f"payment-r{revision}",
            "uid": f"replicaset-r{revision}",
            "annotations": {REVISION: revision}, "creationTimestamp": "2026-01-01T00:00:00Z",
            "ownerReferences": [{"apiVersion": "apps/v1", "kind": "Deployment", "name": "payment-service",
                                 "uid": owner, "controller": True}]},
            "spec": {"template": {"spec": {"containers": [{"name": "payment", "image": f"test:v{revision}"}],
                "private_ignored_field": "must-not-be-returned"}}}}
    return deployment, {"metadata": {}, "items": [rs("4"), rs("2"), rs("3", "foreign-uid")]}


def reader(client):
    return KubernetesDeploymentReader(api_url="https://kubernetes.test", allowed_namespaces={"prod-sg"}, client=client)


def handler_for(deployment, replicasets, requests, *, after=None):
    count = [0]
    def handler(request):
        requests.append(request)
        assert request.method == "GET"
        if request.url.path.endswith("/replicasets"):
            assert request.url.params["limit"] == "200"
            return httpx.Response(200, json=replicasets)
        count[0] += 1
        return httpx.Response(200, json=after if count[0] > 1 and after is not None else deployment)
    return handler


def test_get_only_owned_history_has_observation_not_rollout_time_and_no_raw_pod_spec():
    async def run():
        deployment, sets = fixtures()
        requests = []
        async with httpx.AsyncClient(base_url="https://kubernetes.test", transport=httpx.MockTransport(handler_for(deployment, sets, requests))) as client:
            result = await reader(client).recent_deployments(service="payment-service", namespace="prod-sg")
        row, = result["deployments"]
        assert row["revision"] == "4" and row["previous_revision"] == "2"  # Foreign r3 is not eligible.
        assert row["retained_revisions"] == ["4", "2"]
        assert row["timestamp_semantics"] == "query_observation_not_deployment_time"
        assert row["deployed_at"] != row["revision_created_at"]
        assert row["deployment_uid"] == "owned-uid" and row["namespace"] == "prod-sg"
        assert result["data_origin"] == "kubernetes_api" and len(requests) == 3
        assert "must-not-be-returned" not in str(result)
    asyncio.run(run())


@pytest.mark.parametrize("invalid", ["uid", "namespace", "current", "revision", "duplicate", "page", "oversize", "changed", "controller", "owner_namespace"])
def test_incomplete_ambiguous_changed_or_unowned_history_is_rejected(invalid):
    async def run():
        deployment, sets = fixtures()
        after = None
        if invalid == "uid":
            deployment["metadata"].pop("uid")
        elif invalid == "namespace":
            deployment["metadata"]["namespace"] = "other"
        elif invalid == "current":
            deployment["metadata"]["annotations"][REVISION] = "99"
        elif invalid == "revision":
            sets["items"][0]["metadata"]["annotations"][REVISION] = "bad"
        elif invalid == "duplicate":
            sets["items"].append(deepcopy(sets["items"][0]))
        elif invalid == "page":
            sets["metadata"]["continue"] = "private-cursor"
        elif invalid == "oversize":
            sets["items"] *= 100
        elif invalid == "changed":
            after = deepcopy(deployment)
            after["metadata"]["resourceVersion"] = "101"
        elif invalid == "controller":
            sets["items"][0]["metadata"]["ownerReferences"][0]["controller"] = False
        else:
            sets["items"][0]["metadata"]["namespace"] = "other"
        async with httpx.AsyncClient(base_url="https://kubernetes.test", transport=httpx.MockTransport(handler_for(deployment, sets, [], after=after))) as client:
            with pytest.raises(KubernetesRemediationError):
                await reader(client).recent_deployments(service="payment-service", namespace="prod-sg")
    asyncio.run(run())


@pytest.mark.parametrize("service,namespace", [("../../secrets", "prod-sg"), ("payment-service", "kube-system")])
def test_discovery_scope_rejected_before_request(service, namespace):
    async def run():
        def forbidden(request):
            raise AssertionError("Invalid scope must not send any request")
        async with httpx.AsyncClient(base_url="https://kubernetes.test", transport=httpx.MockTransport(forbidden)) as client:
            with pytest.raises(KubernetesRemediationError):
                await reader(client).recent_deployments(service=service, namespace=namespace)
    asyncio.run(run())


def test_size_budget_and_http_error_are_redacted():
    async def run():
        for response in (httpx.Response(403, text="private-api-token"), httpx.Response(200, content=b"x" * (512 * 1024 + 1))):
            async with httpx.AsyncClient(base_url="https://kubernetes.test", transport=httpx.MockTransport(lambda request: response)) as client:
                with pytest.raises(KubernetesRemediationError) as error:
                    await reader(client).recent_deployments(service="payment-service", namespace="prod-sg")
                assert "private-api-token" not in str(error.value)
    asyncio.run(run())


def test_enabled_reader_failure_does_not_fall_back_to_synthetic_gateway():
    async def run():
        live = AsyncMock()
        live.recent_deployments.side_effect = KubernetesRemediationError("Discovery unavailable")
        gateway = AsyncMock()
        tools = ToolRegistry(ApprovalReceiptVerifier("readonly-history-test-secret"), gateway=gateway, deployment_reader=live)
        with pytest.raises(KubernetesRemediationError):
            await tools.call("get_recent_deployments", {"service": "payment-service", "namespace": "prod-sg"},
                Principal("reader", frozenset({"telemetry:read"})))
        gateway.deployments.assert_not_awaited()
    asyncio.run(run())


@pytest.mark.parametrize("current_samples", [False, True])
def test_mock_kubernetes_through_actual_mcp_graph_retains_verified_namespace_and_pauses_before_execution(current_samples):
    async def run():
        deployment, sets = fixtures()
        requests = []
        async with httpx.AsyncClient(base_url="https://kubernetes.test", transport=httpx.MockTransport(handler_for(deployment, sets, requests))) as api:
            with patch.object(registry, "deployment_reader", reader(api)), \
                    (current_metric_fixture(registry) if current_samples else nullcontext()):
                async with httpx.AsyncClient(base_url="http://mcp.test", transport=httpx.ASGITransport(app=app)) as client:
                    tools = McpTelemetryTools(url="http://mcp.test/mcp", read_token="local-read-token",
                        operator_token="local-operator-token", client=client)
                    workflow = LangGraphIncidentWorkflow(tools=tools, checkpointer=InMemorySaver(),
                        approval_signer=ApprovalReceiptSigner("local-approval-secret-change-me"))
                    state = await workflow.start(IncidentQuestion(question="Why is payment-service slow?", environment="prod-sg"))
        if current_samples:
            assert state.status == "waiting_approval" and state.remediation.arguments["revision"] == "2"
        else:
            assert state.status == "insufficient_evidence" and state.remediation.tool == "none"
            check = next(item.payload for item in state.trace if item.event == "diagnostic_signal_time_checked")
            pool_ids = {item.id for item in state.evidence if item.query == "db_pool_active_ratio"
                        and item.service == "payment-service"}
            assert pool_ids
            assert not any(row["eligible_samples"] for row in check["samples"] if row["evidence_id"] in pool_ids)
            assert state.incident_type is None and state.hypotheses == []
            assert not any(item.event == "workflow_interrupted" for item in state.trace)
        retained = await workflow.get(state.incident_id)
        assert retained == state and len(requests) == 3
        assert all(request.method == "GET" for request in requests)
        assert not any(event.step.value in {"prepare_execution", "execute_or_simulate"} for event in state.trace)
    asyncio.run(run())


def test_rollback_executor_ignores_foreign_target_even_if_revision_matches():
    async def run():
        deployment, sets = fixtures()
        requests = []
        # Only foreign revision 3 exists; no mutation may be dispatched.
        async with httpx.AsyncClient(base_url="https://kubernetes.test", transport=httpx.MockTransport(handler_for(deployment, sets, requests))) as client:
            backend = KubernetesRemediator(api_url="https://kubernetes.test", allowed_namespaces={"prod-sg"}, client=client)
            with pytest.raises(KubernetesRemediationError, match="unique owned"):
                await backend.preflight(tool="rollback_release", service="payment-service", namespace="prod-sg", revision="3", idempotency_key="ownership-key-0001")
        assert all(request.method == "GET" for request in requests)
    asyncio.run(run())
