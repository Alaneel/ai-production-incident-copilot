import asyncio
import json
from copy import deepcopy

import httpx
import pytest

from incident_mcp.kubernetes import KubernetesRemediator, KubernetesRemediationError
from incident_mcp.security import ApprovalReceiptVerifier, Principal
from incident_mcp.tools import ToolRegistry
from incident_mcp.target_context import target_context_digest


@pytest.mark.parametrize("change", [None, "actor", "incident", "tool"])
def test_fresh_registry_uses_persisted_operation_binding_not_just_key(change):
    async def scenario():
        deployment = {"metadata": {"name": "payment-service", "namespace": "incident-copilot", "uid": "deployment-uid", "resourceVersion": "1"},
                      "spec": {"template": {"metadata": {"annotations": {}}, "spec": {"containers": [{"name": "payment", "image": "test:v2"}]}}}}
        requests = []
        def api(request):
            requests.append(request)
            if request.url.path.endswith("/replicasets"):
                return httpx.Response(200, json={"metadata": {}, "items": [{
                    "metadata": {"name": "payment-r141", "namespace": "incident-copilot", "uid": "rs-141",
                                 "annotations": {"deployment.kubernetes.io/revision": "141"},
                                 "ownerReferences": [{"apiVersion": "apps/v1", "kind": "Deployment", "name": "payment-service",
                                                      "uid": "deployment-uid", "controller": True}]},
                    "spec": {"template": {"spec": {"containers": [{"name": "payment", "image": "test:v1"}]}}}}]})
            if request.method == "PATCH":
                body = json.loads(request.content)
                candidate = deepcopy(deployment)
                candidate["spec"]["template"]["metadata"]["annotations"].update(body["spec"]["template"]["metadata"]["annotations"])
                candidate["metadata"]["resourceVersion"] = "2"
                if request.url.params.get("dryRun") == "All":
                    return httpx.Response(200, json=candidate)
                deployment.update(candidate)
            return httpx.Response(200, json=deployment)
        async with httpx.AsyncClient(base_url="https://kubernetes.test", transport=httpx.MockTransport(api)) as client:
            verifier = ApprovalReceiptVerifier("cross-replica-test-secret")
            operator = Principal("operator", frozenset({"production:execute"}))
            def registry():
                backend = KubernetesRemediator(api_url="https://kubernetes.test", allowed_namespaces={"incident-copilot"}, client=client)
                return ToolRegistry(verifier, remediator=backend)
            backend = KubernetesRemediator(api_url="https://kubernetes.test", allowed_namespaces={"incident-copilot"}, client=client)
            original_context = (await backend.preflight(tool="restart_deployment", service="payment-service", namespace="incident-copilot",
                                                         idempotency_key="preflight-original-key"))["target_context"]
            def arguments(*, actor="oncall", incident="INC-FIRST", tool="restart_deployment", context=original_context):
                body = {"service": "payment-service", "namespace": "incident-copilot", "dry_run": False,
                        "target_context": context,
                        "idempotency_key": "cluster-bound-key", "approval_receipt": verifier.issue(
                            incident_id=incident, actor=actor, tool=tool, service="payment-service", namespace="incident-copilot",
                            target="incident-copilot" if tool == "restart_deployment" else "141",
                            target_context_sha256=target_context_digest(context))}
                if tool == "rollback_release":
                    body["revision"] = "141"
                return body
            await registry().call("restart_deployment", arguments(), operator)
            tool = "rollback_release" if change == "tool" else "restart_deployment"
            next_context = (await backend.preflight(tool=tool, service="payment-service", namespace="incident-copilot", revision="141",
                                                    idempotency_key="preflight-rollback-key"))["target_context"] if change == "tool" else original_context
            body = arguments(actor="another-oncall" if change == "actor" else "oncall",
                             incident="INC-SECOND" if change == "incident" else "INC-FIRST", tool=tool, context=next_context)
            fresh = registry()
            assert fresh._executions == {}
            if change is None:
                result = await fresh.call(tool, body, operator)
                assert result["idempotent_replay"] is True
            else:
                with pytest.raises(KubernetesRemediationError, match="conflicting operation fingerprint"):
                    await fresh.call(tool, body, operator)
            assert len([request for request in requests if request.method == "PATCH" and not request.url.params.get("dryRun")]) == 1
            annotations = deployment["spec"]["template"]["metadata"]["annotations"]
            assert len(annotations["incident-copilot.openai/operation-fingerprint"]) == 64
            assert "approval_receipt" not in str(annotations)
    asyncio.run(scenario())


@pytest.mark.parametrize("fingerprint", [None, "conflicting-fingerprint"])
def test_legacy_or_conflicting_cluster_marker_is_not_reexecuted(fingerprint):
    async def scenario():
        calls = []
        annotations = {"incident-copilot.openai/idempotency-key": "legacy-operation-key"}
        if fingerprint:
            annotations["incident-copilot.openai/operation-fingerprint"] = fingerprint
        def api(request):
            calls.append(request.method)
            return httpx.Response(200, json={"metadata": {"name": "payment-service", "namespace": "incident-copilot", "uid": "deployment-uid", "resourceVersion": "1"},
                "spec": {"template": {"metadata": {"annotations": annotations}, "spec": {"containers": [{"name": "payment", "image": "test:v1"}]}}}})
        async with httpx.AsyncClient(base_url="https://kubernetes.test", transport=httpx.MockTransport(api)) as client:
            backend = KubernetesRemediator(api_url="https://kubernetes.test", allowed_namespaces={"incident-copilot"}, client=client)
            context = {"tool": "restart_deployment", "service": "payment-service", "namespace": "incident-copilot",
                       "cluster_context": backend.cluster_context, "deployment_uid": "deployment-uid", "resource_version": "1",
                       "deployment_template_sha256": backend._digest({"metadata": {"annotations": annotations},
                           "spec": {"containers": [{"name": "payment", "image": "test:v1"}]}})}
            with pytest.raises(KubernetesRemediationError, match="manual outcome review"):
                await backend.restart_deployment(service="payment-service", namespace="incident-copilot", idempotency_key="legacy-operation-key", target_context=context)
            assert calls == ["GET"]
    asyncio.run(scenario())


def test_fingerprint_binds_tool_service_namespace_revision_and_approval_identity():
    base = ("rollback_release", "payment-service", "incident-copilot", "141", "approval-identity-hash")
    fingerprint = KubernetesRemediator.operation_fingerprint(*base)
    for index in range(len(base)):
        changed = list(base)
        changed[index] = "different-value"
        assert KubernetesRemediator.operation_fingerprint(*changed) != fingerprint
