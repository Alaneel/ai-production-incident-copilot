import asyncio
from copy import deepcopy
from json import loads
from unittest.mock import AsyncMock

import httpx
import pytest

from incident_mcp.kubernetes import KubernetesRemediationError, KubernetesRemediator
from incident_mcp.schemas import OperationStatusArguments
from incident_mcp.security import ApprovalReceiptVerifier, AuthorizationError, Principal
from incident_mcp.tools import ToolRegistry
from incident_mcp.target_context import target_context_digest


def deployment_fixture(annotations=None):
    return {"metadata": {"name": "payment-service", "namespace": "incident-copilot",
                         "uid": "deployment-status-uid", "resourceVersion": "9"},
            "spec": {"template": {"metadata": {"annotations": annotations or {}},
                                  "spec": {"containers": [{"name": "payment", "image": "test:v2"}]}}}}


def observed_context(backend, deployment):
    return {"tool": "restart_deployment", "service": "payment-service", "namespace": "incident-copilot",
            "cluster_context": backend.cluster_context, "deployment_uid": deployment["metadata"]["uid"],
            "resource_version": deployment["metadata"]["resourceVersion"],
            "deployment_template_sha256": backend._digest(deployment["spec"]["template"])}


def status_arguments():
    return {"service": "payment-service", "namespace": "incident-copilot", "tool": "restart_deployment",
            "idempotency_key": "operation-status-key", "approval_actor": "test-operator", "incident_id": "INC-STATUS"}


@pytest.mark.parametrize("marker,expected", [("different-key", "not_observed"), (None, "unverified_marker"),
                                            ("wrong-fingerprint", "conflicting_marker"), ("matches", "marker_matches")])
def test_kubernetes_status_is_get_only_and_never_claims_rollout_health(marker, expected):
    async def scenario():
        calls = []
        fingerprint = KubernetesRemediator.operation_fingerprint("restart_deployment", "payment-service", "incident-copilot", None, "binding")
        annotations = {"incident-copilot.openai/idempotency-key": "operation-status-key"}
        if marker == "different-key":
            annotations["incident-copilot.openai/idempotency-key"] = "another-operation"
        elif marker is not None:
            annotations["incident-copilot.openai/operation-fingerprint"] = fingerprint if marker == "matches" else marker
        def api(request):
            calls.append(request.method)
            return httpx.Response(200, json=deployment_fixture(annotations))
        async with httpx.AsyncClient(base_url="https://kubernetes.test", transport=httpx.MockTransport(api)) as client:
            backend = KubernetesRemediator(api_url="https://kubernetes.test", allowed_namespaces={"incident-copilot"}, client=client)
            result = await backend.query_operation_status(service="payment-service", namespace="incident-copilot", tool="restart_deployment",
                                                           revision=None, idempotency_key="operation-status-key", operation_binding="binding",
                                                           target_context=observed_context(backend, deployment_fixture(annotations)))
            assert result["status"] == expected
            assert result["rollout_health_verified"] is False
            assert result["kubernetes_resource_version"] == "9"
            assert calls == ["GET"]
            assert "executed" not in result
    asyncio.run(scenario())


def test_fresh_registry_query_matches_patch_binding_without_dispatch_or_receipt():
    async def scenario():
        deployment = deployment_fixture()
        calls = []
        def api(request):
            calls.append((request.method, request.url.params.get("dryRun")))
            if request.method == "PATCH":
                candidate = deepcopy(deployment)
                candidate["spec"]["template"]["metadata"]["annotations"].update(loads(request.content)["spec"]["template"]["metadata"]["annotations"])
                candidate["metadata"]["resourceVersion"] = "10"
                if request.url.params.get("dryRun") == "All":
                    return httpx.Response(200, json=candidate)
                deployment.update(candidate)
            return httpx.Response(200, json=deployment)
        async with httpx.AsyncClient(base_url="https://kubernetes.test", transport=httpx.MockTransport(api)) as client:
            verifier = ApprovalReceiptVerifier("status-test-only-secret")
            principal = Principal("operator", frozenset({"production:execute", "telemetry:read"}))
            def registry():
                return ToolRegistry(verifier, remediator=KubernetesRemediator(api_url="https://kubernetes.test", allowed_namespaces={"incident-copilot"}, client=client))
            query = status_arguments()
            preflight = await registry().call("restart_deployment", {
                "service": query["service"], "namespace": query["namespace"],
                "idempotency_key": "status-preflight-key", "dry_run": True,
            }, principal)
            query["target_context"] = preflight["target_context"]
            await registry().call("restart_deployment", {
                "service": query["service"], "namespace": query["namespace"], "idempotency_key": query["idempotency_key"], "dry_run": False,
                "target_context": query["target_context"],
                "approval_receipt": verifier.issue(incident_id=query["incident_id"], actor=query["approval_actor"], tool=query["tool"],
                                                    service=query["service"], namespace=query["namespace"], target=query["namespace"],
                                                    target_context_sha256=target_context_digest(query["target_context"])),
            }, principal)
            fresh = registry()
            assert fresh._executions == {}
            result = await fresh.call("query_operation_status", query, principal)
            assert result["status"] == "marker_matches"
            query["approval_actor"] = "different-actor"
            assert (await fresh.call("query_operation_status", query, principal))["status"] == "conflicting_marker"
            assert calls == [("GET", None), ("PATCH", "All"), ("GET", None), ("PATCH", None), ("GET", None), ("GET", None)]
            definition = next(item for item in fresh.list_definitions() if item["name"] == "query_operation_status")
            assert definition["annotations"]["readOnlyHint"] is True
            assert definition["annotations"]["destructiveHint"] is False
    asyncio.run(scenario())


def test_live_registry_status_requires_original_target_context_before_backend():
    async def scenario():
        backend = AsyncMock()
        registry = ToolRegistry(ApprovalReceiptVerifier("status-test-only-secret"), remediator=backend)
        with pytest.raises(AuthorizationError, match="verified Kubernetes target context"):
            await registry.call("query_operation_status", status_arguments(), Principal("operator", frozenset({"production:execute"})))
        backend.query_operation_status.assert_not_awaited()
    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["deployment_uid", "cluster_context"])
def test_readonly_status_never_accepts_a_marker_on_a_different_target(change):
    async def scenario():
        deployment = deployment_fixture()
        calls = []
        def api(request):
            calls.append(request.method)
            return httpx.Response(200, json=deployment)
        async with httpx.AsyncClient(base_url="https://kubernetes.test", transport=httpx.MockTransport(api)) as client:
            backend = KubernetesRemediator(api_url="https://kubernetes.test", allowed_namespaces={"incident-copilot"}, client=client)
            context = observed_context(backend, deployment)
            if change == "deployment_uid":
                deployment["metadata"]["uid"] = "replacement-deployment"
            else:
                context["cluster_context"] = "f" * 64
            with pytest.raises(KubernetesRemediationError, match="UID changed|cluster context changed"):
                await backend.query_operation_status(service="payment-service", namespace="incident-copilot", tool="restart_deployment",
                    revision=None, idempotency_key="operation-status-key", operation_binding="binding", target_context=context)
            assert calls == (["GET"] if change == "deployment_uid" else [])
    asyncio.run(scenario())


def test_reader_cannot_query_operator_scoped_operation_record():
    async def scenario():
        backend = AsyncMock()
        registry = ToolRegistry(ApprovalReceiptVerifier("status-test-only-secret"), remediator=backend)
        with pytest.raises(AuthorizationError):
            await registry.call("query_operation_status", status_arguments(), Principal("reader", frozenset({"telemetry:read"})))
        backend.query_operation_status.assert_not_awaited()
    asyncio.run(scenario())


@pytest.mark.parametrize("change", [{"tool": "rollback_release"}, {"revision": "141"}, {"extra": "arbitrary"}])
def test_status_schema_rejects_missing_or_wrong_action_target(change):
    with pytest.raises(ValueError):
        OperationStatusArguments.model_validate({**status_arguments(), **change})


def test_empty_simulation_cache_reports_unknown_not_nonexecution():
    async def scenario():
        registry = ToolRegistry(ApprovalReceiptVerifier("status-test-only-secret"))
        result = await registry.call("query_operation_status", status_arguments(), Principal("operator", frozenset({"production:execute"})))
        assert result["status"] == "unknown"
        assert result["durable"] is False
        assert result["read_only"] is True
    asyncio.run(scenario())
