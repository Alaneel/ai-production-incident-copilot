"""GET-only rollout snapshots and mock PATCH acknowledgement contracts, not live acceptance."""

import asyncio
import hashlib
import json
from copy import deepcopy
from unittest.mock import patch

import httpx
import pytest

from incident_mcp import server
from incident_mcp.kubernetes import KubernetesRemediationError, KubernetesRemediator
from incident_mcp.schemas import RestartDeploymentArguments
from incident_mcp.security import ApprovalReceiptVerifier, AuthorizationError, Principal, TokenAuthenticator
from incident_mcp.target_context import normalize_target_context, target_context_digest
from incident_mcp.tools import ToolRegistry


SERVICE = "payment-service"
NAMESPACE = "incident-copilot"
KEY = "health-operation-key"
PRINCIPAL = Principal("incident-operator", frozenset({"production:execute", "telemetry:read"}))


def deployment():
    return {
        "metadata": {"name": SERVICE, "namespace": NAMESPACE, "uid": "health-deployment-uid",
                     "resourceVersion": "20", "generation": 7},
        "spec": {"replicas": 2, "template": {"metadata": {"annotations": {}},
                 "spec": {"containers": [{"name": SERVICE, "image": "payment:test-v2"}]}}},
        "status": {"observedGeneration": 7, "replicas": 2, "updatedReplicas": 2, "readyReplicas": 2,
                   "availableReplicas": 2, "unavailableReplicas": 0},
    }


def observed_context(backend, resource):
    return normalize_target_context({
        "tool": "restart_deployment", "service": SERVICE, "namespace": NAMESPACE,
        "cluster_context": backend.cluster_context, "deployment_uid": resource["metadata"]["uid"],
        "resource_version": resource["metadata"]["resourceVersion"],
        "deployment_template_sha256": backend._digest(resource["spec"]["template"]),
    })


def query_arguments(context=None):
    return {"service": SERVICE, "namespace": NAMESPACE, "tool": "restart_deployment",
            "idempotency_key": KEY, "approval_actor": "test-operator", "incident_id": "INC-HEALTH",
            "target_context": context}


def operation_binding(query, principal=PRINCIPAL):
    operation = RestartDeploymentArguments.model_validate({
        "service": query["service"], "namespace": query["namespace"], "dry_run": False,
        "idempotency_key": query["idempotency_key"], "target_context": query["target_context"],
    })
    binding = {"tool": query["tool"], "arguments": operation.model_dump(exclude={"approval_receipt", "idempotency_key"}),
               "principal": principal.subject, "actor": query["approval_actor"], "incident_id": query["incident_id"]}
    return hashlib.sha256(json.dumps(binding, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def mark_applied(backend, resource, query):
    resource["spec"]["template"]["metadata"]["annotations"].update({
        "incident-copilot.openai/idempotency-key": query["idempotency_key"],
        "incident-copilot.openai/operation-fingerprint": backend.operation_fingerprint(
            query["tool"], query["service"], query["namespace"], None, operation_binding(query)),
        "incident-copilot.openai/restarted-at": "2026-10-07T12:00:00+00:00",
    })


async def backend_query(backend, context, **changes):
    return await backend.query_deployment_health(
        service=SERVICE, namespace=NAMESPACE, tool="restart_deployment", revision=None,
        idempotency_key=KEY, operation_binding="binding", target_context=context, **changes)


def test_fresh_registry_health_is_get_only_and_uses_original_execution_binding():
    async def scenario():
        current = deployment()
        requests = []

        def api(request):
            requests.append(request)
            assert request.method == "GET" and not request.url.params
            assert request.url.path.endswith(f"/deployments/{SERVICE}")
            return httpx.Response(200, json=current)

        async with httpx.AsyncClient(base_url="https://kubernetes.test", transport=httpx.MockTransport(api)) as client:
            backend = KubernetesRemediator(api_url="https://kubernetes.test", allowed_namespaces={NAMESPACE}, client=client)
            query = query_arguments(observed_context(backend, current))
            mark_applied(backend, current, query)
            current["metadata"]["generation"] = 8
            current["status"]["observedGeneration"] = 7
            current["status"]["readyReplicas"] = 1
            registry = ToolRegistry(ApprovalReceiptVerifier("health-test-only-secret"), remediator=backend)
            result = await registry.call("query_deployment_health", query, PRINCIPAL)
            assert result["source"] == "kubernetes_deployment_health" and result["read_only"] is True
            assert result["marker_status"] == "marker_matches"
            assert result["deployment_uid"] == "health-deployment-uid"
            assert result["cluster_context"] == query["target_context"]["cluster_context"]
            assert result["generation"] == 8 and result["observed_generation"] == 7
            assert result["desired_replicas"] == 2 and result["ready_replicas"] == 1
            assert result["template_sha256"] == backend._digest(current["spec"]["template"])
            assert result["template_sha256"] != query["target_context"]["deployment_template_sha256"]
            assert "healthy" not in result and "executed" not in result
            assert registry._executions == {} and registry._execution_bindings == {}
            current["status"].update({"observedGeneration": 8, "readyReplicas": 2})
            fresh = ToolRegistry(registry.approval_verifier, remediator=backend)
            assert (await fresh.call("query_deployment_health", query, PRINCIPAL))["ready_replicas"] == 2
            for field in ("approval_actor", "incident_id"):
                changed = {**query, field: "another-value"}
                assert (await fresh.call("query_deployment_health", changed, PRINCIPAL))["marker_status"] == "conflicting_marker"
            other = Principal("another-principal", PRINCIPAL.scopes)
            assert (await fresh.call("query_deployment_health", query, other))["marker_status"] == "conflicting_marker"
            assert len(requests) == 5 and all(request.method == "GET" for request in requests)
    asyncio.run(scenario())


@pytest.mark.parametrize("marker,expected", [
    ({}, "not_observed"),
    ({"incident-copilot.openai/idempotency-key": KEY}, "unverified_marker"),
    ({"incident-copilot.openai/idempotency-key": KEY, "incident-copilot.openai/operation-fingerprint": "wrong"}, "conflicting_marker"),
])
def test_health_does_not_promote_absent_or_conflicting_marker(marker, expected):
    async def scenario():
        current = deployment()
        current["spec"]["template"]["metadata"]["annotations"] = marker
        methods = []
        def api(request):
            methods.append(request.method)
            return httpx.Response(200, json=current)
        async with httpx.AsyncClient(base_url="https://kubernetes.test", transport=httpx.MockTransport(api)) as client:
            backend = KubernetesRemediator(api_url="https://kubernetes.test", allowed_namespaces={NAMESPACE}, client=client)
            result = await backend_query(backend, observed_context(backend, current))
            assert result["marker_status"] == expected and "healthy" not in result
            assert methods == ["GET"]
    asyncio.run(scenario())


@pytest.mark.parametrize("change,io", [
    ("missing_context", 0), ("cluster_context", 0), ("namespace", 0), ("service", 0),
    ("tool", 0), ("deployment_uid", 1), ("forbidden_namespace", 0),
])
def test_health_rejects_wrong_approved_target_without_mutation(change, io):
    async def scenario():
        current = deployment()
        methods = []
        def api(request):
            methods.append(request.method)
            return httpx.Response(200, json=current)
        async with httpx.AsyncClient(base_url="https://kubernetes.test", transport=httpx.MockTransport(api)) as client:
            backend = KubernetesRemediator(api_url="https://kubernetes.test", allowed_namespaces={NAMESPACE}, client=client)
            context = observed_context(backend, current)
            query = {"service": SERVICE, "namespace": NAMESPACE, "tool": "restart_deployment", "revision": None,
                     "idempotency_key": KEY, "operation_binding": "binding", "target_context": context}
            if change == "missing_context":
                query["target_context"] = None
            elif change == "forbidden_namespace":
                query["namespace"] = "kube-system"
            elif change == "deployment_uid":
                current["metadata"]["uid"] = "replacement-uid"
            elif change == "tool":
                context["tool"] = "rollback_release"
            else:
                context[change] = "f" * 64 if change == "cluster_context" else "another-target"
            with pytest.raises(KubernetesRemediationError):
                await backend.query_deployment_health(**query)
            assert methods == ["GET"] * io
    asyncio.run(scenario())


@pytest.mark.parametrize("section,field,value", [
    ("metadata", "generation", None), ("metadata", "generation", 0),
    ("metadata", "generation", True), ("metadata", "generation", "8"),
    ("metadata", "generation", 8.0), ("metadata", "generation", -1),
    ("metadata", "generation", 1 << 63),
    ("status", "observedGeneration", "8"), ("status", "observedGeneration", -1),
    ("status", "observedGeneration", True), ("status", "observedGeneration", 8),
    ("spec", "replicas", False), ("spec", "replicas", "2"), ("spec", "replicas", -1),
    ("status", "updatedReplicas", -1), ("status", "readyReplicas", "2"),
    ("status", "availableReplicas", 2.0), ("status", "unavailableReplicas", True),
    ("status", "unavailableReplicas", None), ("status", "unavailableReplicas", "0"),
    ("status", "replicas", None), ("status", "replicas", True),
    ("status", "replicas", "2"), ("status", "replicas", -1),
])
def test_health_fails_closed_on_malformed_generation_or_replica_fields(section, field, value):
    async def scenario():
        current = deployment()
        current[section][field] = value
        methods = []
        def api(request):
            methods.append(request.method)
            return httpx.Response(200, json=current)
        async with httpx.AsyncClient(base_url="https://kubernetes.test", transport=httpx.MockTransport(api)) as client:
            backend = KubernetesRemediator(api_url="https://kubernetes.test", allowed_namespaces={NAMESPACE}, client=client)
            with pytest.raises(KubernetesRemediationError, match="malformed|inconsistent"):
                await backend_query(backend, observed_context(backend, current))
            assert methods == ["GET"]
    asyncio.run(scenario())


@pytest.mark.parametrize("case", ["missing_status", "partial_status", "zero_replicas", "stale_controller", "insufficient_replicas"])
def test_health_preserves_unknown_zero_stale_and_insufficient_counts_without_claiming_recovery(case):
    async def scenario():
        current = deployment()
        if case == "missing_status":
            del current["status"]
            del current["spec"]["replicas"]
        elif case == "partial_status":
            current["status"] = {"observedGeneration": 7}
        elif case == "zero_replicas":
            current["spec"]["replicas"] = 0
            current["status"].update({"updatedReplicas": 0, "readyReplicas": 0, "availableReplicas": 0})
        elif case == "stale_controller":
            current["status"]["observedGeneration"] = 6
        else:
            current["status"].update({"updatedReplicas": 1, "readyReplicas": 1, "availableReplicas": 0, "unavailableReplicas": 2})
        async with httpx.AsyncClient(base_url="https://kubernetes.test", transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=current))) as client:
            backend = KubernetesRemediator(api_url="https://kubernetes.test", allowed_namespaces={NAMESPACE}, client=client)
            result = await backend_query(backend, observed_context(backend, current))
            assert "healthy" not in result and "recovered" not in result
            if case in {"missing_status", "partial_status"}:
                assert all(result[key] is None for key in ("total_replicas", "updated_replicas", "ready_replicas", "available_replicas"))
                assert result["unavailable_replicas"] == (None if case == "missing_status" else 0)
                if case == "missing_status":
                    assert result["desired_replicas"] is None and result["observed_generation"] is None
            elif case == "zero_replicas":
                assert result["desired_replicas"] == result["ready_replicas"] == 0
            elif case == "stale_controller":
                assert result["observed_generation"] < result["generation"]
            else:
                assert result["available_replicas"] < result["desired_replicas"]
    asyncio.run(scenario())


@pytest.mark.parametrize("change", [{"status": []}, {"status": None}, {"metadata": {"deletionTimestamp": "2026-10-07T12:00:00Z"}}])
def test_health_rejects_invalid_status_envelope_or_deleting_deployment(change):
    async def scenario():
        current = deployment()
        if "metadata" in change:
            current["metadata"].update(change["metadata"])
        else:
            current.update(change)
        async with httpx.AsyncClient(base_url="https://kubernetes.test", transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=current))) as client:
            backend = KubernetesRemediator(api_url="https://kubernetes.test", allowed_namespaces={NAMESPACE}, client=client)
            with pytest.raises(KubernetesRemediationError, match="malformed|deleting"):
                await backend_query(backend, observed_context(backend, current))
    asyncio.run(scenario())


def test_actual_asgi_health_is_operator_only_and_get_only_without_receipt_or_dispatch(caplog):
    async def scenario():
        current = deployment()
        methods = []
        def api(request):
            methods.append(request.method)
            assert request.method == "GET"
            return httpx.Response(200, json=current)
        async with httpx.AsyncClient(base_url="https://kubernetes.test", transport=httpx.MockTransport(api)) as client:
            backend = KubernetesRemediator(api_url="https://kubernetes.test", allowed_namespaces={NAMESPACE}, client=client)
            query = query_arguments(observed_context(backend, current))
            mark_applied(backend, current, query)
            registry = ToolRegistry(ApprovalReceiptVerifier("health-test-only-secret"), remediator=backend)
            with patch.object(server, "registry", registry), patch.object(server, "authenticator", TokenAuthenticator("health-reader", "health-operator")):
                server._rate_windows.clear()
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://mcp.test") as http:
                    payload = {"jsonrpc": "2.0", "id": "health-read", "method": "tools/call", "params": {"name": "query_deployment_health", "arguments": query}}
                    denied = await http.post("/mcp", headers={"Authorization": "Bearer health-reader"}, json=payload)
                    assert denied.status_code == 403 and denied.json()["error"]["code"] == -32001
                    assert methods == []
                    response = await http.post("/mcp", headers={"Authorization": "Bearer health-operator"}, json=payload)
                    result = response.json()["result"]
                    assert result["isError"] is False and result["structuredContent"]["marker_status"] == "marker_matches"
                    assert result["structuredContent"]["source"] == "kubernetes_deployment_health"
                    assert methods == ["GET"]
                    definition = next(item for item in registry.list_definitions() if item["name"] == "query_deployment_health")
                    assert definition["annotations"]["readOnlyHint"] is True
                    assert definition["annotations"]["destructiveHint"] is False
                    current["status"]["readyReplicas"] = "private-invalid-value"
                    failed = await http.post("/mcp", headers={"Authorization": "Bearer health-operator"}, json=payload)
                    assert failed.json()["error"]["code"] == -32602 and "result" not in failed.json()
                    assert "private" not in failed.text and "private" not in caplog.text
                    assert methods == ["GET", "GET"]
    asyncio.run(scenario())


def test_live_registry_requires_target_context_and_simulation_never_proves_health():
    async def scenario():
        from unittest.mock import AsyncMock
        backend = AsyncMock()
        registry = ToolRegistry(ApprovalReceiptVerifier("health-test-only-secret"), remediator=backend)
        with pytest.raises(AuthorizationError, match="verified Kubernetes target context"):
            await registry.call("query_deployment_health", query_arguments(), PRINCIPAL)
        backend.query_deployment_health.assert_not_awaited()
        simulated = ToolRegistry(registry.approval_verifier)
        simulated._executions[KEY] = {"executed": True, "accepted": True}
        result = await simulated.call("query_deployment_health", query_arguments(), PRINCIPAL)
        assert result["source"] == "simulation" and result["read_only"] is True
        assert result["marker_status"] == "unknown"
        assert all(result[key] is None for key in ("deployment_uid", "cluster_context", "generation", "template_sha256",
                   "observed_generation", "desired_replicas", "total_replicas", "updated_replicas", "ready_replicas", "available_replicas", "unavailable_replicas"))
    asyncio.run(scenario())


@pytest.mark.parametrize("tool", ["restart_deployment", "rollback_release"])
def test_registry_preserves_actual_patch_ack_generation_and_template_digest_on_mock_execute_and_replay(tool):
    async def scenario():
        current = deployment()
        retained = {"metadata": {"name": "payment-retained", "namespace": NAMESPACE, "uid": "retained-rs-uid",
                    "annotations": {"deployment.kubernetes.io/revision": "141"},
                    "ownerReferences": [{"apiVersion": "apps/v1", "kind": "Deployment", "name": SERVICE,
                                         "uid": current["metadata"]["uid"], "controller": True}]},
                    "spec": {"template": {"metadata": {}, "spec": {"containers": [{"name": SERVICE, "image": "payment:test-v1"}]}}}}
        requests = []
        def api(request):
            requests.append(request)
            if request.url.path.endswith("/replicasets"):
                return httpx.Response(200, json={"metadata": {}, "items": [retained]})
            if request.method == "PATCH":
                candidate = deepcopy(current)
                payload = json.loads(request.content)["spec"]["template"]
                candidate["spec"]["template"]["metadata"]["annotations"].update(payload["metadata"]["annotations"])
                if "spec" in payload:
                    candidate["spec"]["template"]["spec"]["containers"] = payload["spec"]["containers"]
                candidate["spec"]["template"]["metadata"]["annotations"]["admission.test/observed"] = "actual-server-response"
                candidate["metadata"].update({"resourceVersion": "23", "generation": 12})
                current.update(candidate)
            return httpx.Response(200, json=current)
        async with httpx.AsyncClient(base_url="https://kubernetes.test", transport=httpx.MockTransport(api)) as client:
            backend = KubernetesRemediator(api_url="https://kubernetes.test", allowed_namespaces={NAMESPACE}, client=client)
            context = observed_context(backend, current)
            if tool == "rollback_release":
                context = normalize_target_context({**context, "tool": tool, "revision": "141",
                    "replicaset_uid": retained["metadata"]["uid"], "rollback_template_sha256": backend._digest(retained["spec"]["template"])})
            verifier = ApprovalReceiptVerifier("health-test-only-secret")
            registry = ToolRegistry(verifier, remediator=backend)
            receipt = verifier.issue(incident_id="INC-HEALTH", actor="test-operator", tool=tool,
                service=SERVICE, namespace=NAMESPACE, target="141" if tool == "rollback_release" else NAMESPACE,
                target_context_sha256=target_context_digest(context))
            args = {"service": SERVICE, "namespace": NAMESPACE, "dry_run": False, "idempotency_key": KEY,
                    "target_context": context, "approval_receipt": receipt}
            if tool == "rollback_release":
                args["revision"] = "141"
            result = await registry.call(tool, args, PRINCIPAL)
            assert result["applied_generation"] == 12  # Not preflight 7 + guessed increment.
            assert result["applied_template_sha256"] == backend._digest(current["spec"]["template"])
            assert result["applied_template_sha256"] != context["deployment_template_sha256"]
            assert result["idempotent_replay"] is False and result["execution_backend"] == "kubernetes"
            replay = await ToolRegistry(verifier, remediator=backend).call(tool, args, PRINCIPAL)
            assert replay["idempotent_replay"] is True
            assert replay["applied_generation"] == 12 and replay["applied_template_sha256"] == result["applied_template_sha256"]
            assert [request.method for request in requests] == (["GET", "GET", "PATCH", "GET"] if tool == "rollback_release" else ["GET", "PATCH", "GET"])
    asyncio.run(scenario())


def test_unknown_patch_generation_is_not_synthesized_and_transport_loss_has_no_ack():
    async def scenario():
        current = deployment()
        del current["metadata"]["generation"]
        loss = False
        def api(request):
            if request.method == "PATCH" and loss:
                raise httpx.ReadTimeout("test response loss", request=request)
            return httpx.Response(200, json=current)
        async with httpx.AsyncClient(base_url="https://kubernetes.test", transport=httpx.MockTransport(api)) as client:
            backend = KubernetesRemediator(api_url="https://kubernetes.test", allowed_namespaces={NAMESPACE}, client=client)
            context = observed_context(backend, current)
            result = await backend.restart_deployment(service=SERVICE, namespace=NAMESPACE, idempotency_key=KEY, target_context=context)
            assert result["applied_generation"] is None
            loss = True
            with pytest.raises(KubernetesRemediationError, match="transport failure"):
                await backend.restart_deployment(service=SERVICE, namespace=NAMESPACE, idempotency_key="another-health-key", target_context=context)
    asyncio.run(scenario())


@pytest.mark.parametrize("total", [2, 3])
def test_health_preserves_total_replicas_and_kubernetes_omitted_zero_unavailable(total):
    async def scenario():
        current = deployment()
        current["status"]["replicas"] = total
        del current["status"]["unavailableReplicas"]
        async with httpx.AsyncClient(base_url="https://kubernetes.test", transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=current))) as client:
            backend = KubernetesRemediator(api_url="https://kubernetes.test", allowed_namespaces={NAMESPACE}, client=client)
            result = await backend_query(backend, observed_context(backend, current))
            assert result["total_replicas"] == total
            assert result["desired_replicas"] == result["updated_replicas"] == 2
            assert result["unavailable_replicas"] == 0
            assert "healthy" not in result
            if total > 2:
                assert result["total_replicas"] > result["updated_replicas"]
    asyncio.run(scenario())
