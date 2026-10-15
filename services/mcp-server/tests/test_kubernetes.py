import asyncio
import copy
import json

import httpx
import pytest

from incident_mcp.kubernetes import KubernetesRemediationError, KubernetesRemediator


def deployment():
    return {
        "metadata": {"name": "payment-service", "namespace": "incident-copilot",
                     "uid": "payment-owned-uid", "resourceVersion": "16"},
        "spec": {"template": {"metadata": {"annotations": {}},
                              "spec": {"containers": [{"name": "payment-service", "image": "example/payment:v142"}]}}},
    }


def replicaset():
    return {
        "metadata": {"name": "payment-service-old", "namespace": "incident-copilot", "uid": "old-replicaset-uid",
                     "ownerReferences": [{"apiVersion": "apps/v1", "kind": "Deployment", "name": "payment-service",
                                          "uid": "payment-owned-uid", "controller": True}],
                     "annotations": {"deployment.kubernetes.io/revision": "141"}},
        "spec": {"template": {"metadata": {},
                              "spec": {"containers": [{"name": "payment-service", "image": "example/payment:v141"}]}}},
    }


@pytest.mark.parametrize("tool", ["restart_deployment", "rollback_release"])
def test_preflight_and_execution_share_conditional_patch_structure(tool):
    async def scenario():
        current = deployment()
        requests = []

        def handler(request):
            requests.append(request)
            if request.url.path.endswith("/replicasets"):
                return httpx.Response(200, json={"items": [replicaset()]})
            if request.method == "GET":
                return httpx.Response(200, json=current)
            response = copy.deepcopy(current)
            if request.url.params.get("dryRun") != "All":
                response["metadata"]["resourceVersion"] = "17"
            return httpx.Response(200, json=response)

        async with httpx.AsyncClient(base_url="https://kubernetes.test", transport=httpx.MockTransport(handler)) as client:
            backend = KubernetesRemediator(api_url="https://kubernetes.test", allowed_namespaces={"incident-copilot"}, client=client)
            arguments = {"service": "payment-service", "namespace": "incident-copilot", "idempotency_key": "execute-k8s-0001"}
            if tool == "rollback_release":
                arguments["revision"] = "141"
            preflight = await backend.preflight(tool=tool, **arguments)
            assert preflight["executed"] is False and preflight["preflight_mode"] == "kubernetes-server-dry-run"
            result = await getattr(backend, tool)(**arguments, target_context=preflight["target_context"])
            assert result["kubernetes_resource_version"] == "17" and result["idempotent_replay"] is False
            patches = [request for request in requests if request.method == "PATCH"]
            assert len(patches) == 2
            assert patches[0].url.params.get("dryRun") == "All"
            assert not patches[1].url.params
            before, after = [json.loads(request.content) for request in patches]
            assert before["metadata"] == after["metadata"] == {"resourceVersion": "16"}
            for body in (before, after):
                annotations = body["spec"]["template"]["metadata"]["annotations"]
                assert annotations["incident-copilot.openai/idempotency-key"] == "execute-k8s-0001"
                assert len(annotations["incident-copilot.openai/operation-fingerprint"]) == 64
            if tool == "rollback_release":
                assert before["spec"]["template"]["spec"] == after["spec"]["template"]["spec"]
                assert result["container_images"] == [{"name": "payment-service", "image": "example/payment:v141"}]
                assert result["rollback_scope"] == "container_images_only_not_full_release"
            else:
                assert "restarted_at" in result
            with pytest.raises(KubernetesRemediationError, match="allow-list"):
                await getattr(backend, tool)(**{**arguments, "namespace": "kube-system"}, target_context=preflight["target_context"])

    asyncio.run(scenario())


def test_duplicate_key_on_same_approved_uid_is_cluster_level_no_op():
    async def scenario():
        current = deployment()
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(200, json=current)

        async with httpx.AsyncClient(base_url="https://kubernetes.test", transport=httpx.MockTransport(handler)) as client:
            backend = KubernetesRemediator(api_url="https://kubernetes.test", allowed_namespaces={"incident-copilot"}, client=client)
            context = (await backend.preflight(tool="restart_deployment", service="payment-service", namespace="incident-copilot",
                                               idempotency_key="restart-duplicate"))["target_context"]
            current["spec"]["template"]["metadata"]["annotations"].update({
                "incident-copilot.openai/idempotency-key": "restart-duplicate",
                "incident-copilot.openai/operation-fingerprint": backend.operation_fingerprint(
                    "restart_deployment", "payment-service", "incident-copilot", None, "approval-binding"),
            })
            current["metadata"]["resourceVersion"] = "22"
            requests.clear()
            result = await backend.restart_deployment(service="payment-service", namespace="incident-copilot",
                                                      idempotency_key="restart-duplicate", operation_binding="approval-binding", target_context=context)
            assert result["idempotent_replay"] is True
            assert [request.method for request in requests] == ["GET"]

    asyncio.run(scenario())
