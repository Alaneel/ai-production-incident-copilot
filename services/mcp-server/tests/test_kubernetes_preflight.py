"""Mock Kubernetes admission/identity contracts, not live-cluster acceptance."""

import asyncio
import copy
import json

import httpx
import pytest

from incident_mcp.kubernetes import KubernetesConflictError, KubernetesRemediationError, KubernetesRemediator
from incident_mcp.target_context import KubernetesTargetContext, normalize_target_context, target_context_digest


SERVICE = "payment-service"
NAMESPACE = "incident-copilot"
KEY = "preflight-execution-key"


def deployment():
    return {"metadata": {"name": SERVICE, "namespace": NAMESPACE, "uid": "deployment-original-uid", "resourceVersion": "19"},
            "spec": {"template": {"metadata": {"annotations": {}, "labels": {"app": SERVICE}},
                                  "spec": {"containers": [{"name": SERVICE, "image": "example/payment:v142"}],
                                           "serviceAccountName": "payment-account"}}}}


def replicaset():
    return {"metadata": {"name": "payment-service-retained", "namespace": NAMESPACE, "uid": "replicaset-retained-uid",
                         "annotations": {"deployment.kubernetes.io/revision": "141"},
                         "ownerReferences": [{"apiVersion": "apps/v1", "kind": "Deployment", "name": SERVICE,
                                              "uid": "deployment-original-uid", "controller": True}]},
            "spec": {"template": {"metadata": {}, "spec": {"containers": [{"name": SERVICE, "image": "example/payment:v141"}],
                                                           "serviceAccountName": "historical-account"}}}}


class FakeKubernetes:
    def __init__(self):
        self.deployment = deployment()
        self.replicasets = {"metadata": {}, "items": [replicaset()]}
        self.requests = []
        self.get_status = 200
        self.patch_status = 200
        self.patch_response = None
        self.conflict = None

    def __call__(self, request):
        self.requests.append(request)
        if request.method == "GET":
            resource = self.replicasets if request.url.path.endswith("/replicasets") else self.deployment
            return httpx.Response(self.get_status, json=resource)
        assert request.method == "PATCH"
        if self.patch_status != 200:
            return httpx.Response(self.patch_status, json={"message": "test admission refused"})
        if self.conflict is not None and request.url.params.get("dryRun") != "All":
            callback, self.conflict = self.conflict, None
            callback(self, request)
            return httpx.Response(409, json={"message": "test optimistic concurrency conflict"})
        payload = json.loads(request.content)
        result = copy.deepcopy(self.deployment)
        annotations = payload["spec"]["template"]["metadata"]["annotations"]
        result["spec"]["template"]["metadata"]["annotations"].update(annotations)
        images = payload["spec"]["template"].get("spec", {}).get("containers", [])
        for image in images:
            existing = next(item for item in result["spec"]["template"]["spec"]["containers"] if item["name"] == image["name"])
            existing["image"] = image["image"]
        if request.url.params.get("dryRun") != "All":
            result["metadata"]["resourceVersion"] = "20"
            self.deployment = copy.deepcopy(result)
        if self.patch_response is not None:
            self.patch_response(result)
        return httpx.Response(200, json=result)

    @property
    def ordinary_patches(self):
        return [request for request in self.requests if request.method == "PATCH" and request.url.params.get("dryRun") != "All"]


async def use_backend(api, callback, **configuration):
    async with httpx.AsyncClient(base_url="https://kubernetes.test", transport=httpx.MockTransport(api)) as client:
        backend = KubernetesRemediator(api_url=configuration.pop("api_url", "https://kubernetes.test"),
                                       allowed_namespaces={NAMESPACE}, client=client, **configuration)
        await callback(backend)


async def preflight(backend, tool="restart_deployment"):
    return await backend.preflight(tool=tool, service=SERVICE, namespace=NAMESPACE, idempotency_key=KEY,
                                   **({"revision": "141"} if tool == "rollback_release" else {}))


async def execute(backend, context, tool="restart_deployment"):
    return await getattr(backend, tool)(service=SERVICE, namespace=NAMESPACE, idempotency_key=KEY,
                                       operation_binding="bound-human-approval", target_context=context,
                                       **({"revision": "141"} if tool == "rollback_release" else {}))


@pytest.mark.parametrize("tool", ["restart_deployment", "rollback_release"])
def test_preflight_uses_server_dryrun_and_leaves_no_persisted_marker(tool):
    api = FakeKubernetes()
    before = copy.deepcopy(api.deployment)

    async def check(backend):
        result = await preflight(backend, tool)
        assert result["accepted"] is True and result["executed"] is False and result["dry_run"] is True
        context = KubernetesTargetContext.model_validate(result["target_context"])
        assert context.deployment_uid == before["metadata"]["uid"]
        assert context.resource_version == "19"
        assert context.deployment_template_sha256 == backend._digest(before["spec"]["template"])
        assert result["cluster_context_semantics"].endswith("not_cluster_uid")
        assert api.deployment == before
        patches = [request for request in api.requests if request.method == "PATCH"]
        assert len(patches) == 1 and dict(patches[0].url.params) == {"dryRun": "All"}
        assert patches[0].headers["Content-Type"] == "application/strategic-merge-patch+json"
        assert json.loads(patches[0].content)["metadata"] == {"resourceVersion": "19"}
        assert api.ordinary_patches == []
        if tool == "rollback_release":
            assert context.revision == "141" and context.replicaset_uid == "replicaset-retained-uid"
            assert context.rollback_template_sha256 == backend._digest(api.replicasets["items"][0]["spec"]["template"])
            assert result["rollback_scope"] == "container_images_only_not_full_release"
            # No full historical pod-template replacement is performed.
            assert set(json.loads(patches[0].content)["spec"]["template"]["spec"]) == {"containers"}
        else:
            assert context.revision is None and context.replicaset_uid is None and context.rollback_template_sha256 is None

    asyncio.run(use_backend(api, check))


@pytest.mark.parametrize("tool", ["restart_deployment", "rollback_release"])
def test_forbidden_namespace_rejected_before_any_io(tool):
    api = FakeKubernetes()

    async def check(backend):
        with pytest.raises(KubernetesRemediationError, match="allow-list"):
            await backend.preflight(tool=tool, service=SERVICE, namespace="kube-system", idempotency_key=KEY,
                                    **({"revision": "141"} if tool == "rollback_release" else {}))
        with pytest.raises(KubernetesRemediationError, match="allow-list"):
            await getattr(backend, tool)(service=SERVICE, namespace="kube-system", idempotency_key=KEY,
                                        **({"revision": "141"} if tool == "rollback_release" else {}))
        assert api.requests == []

    asyncio.run(use_backend(api, check))


@pytest.mark.parametrize("tool", ["restart_deployment", "rollback_release"])
def test_missing_execution_context_fails_closed_without_io(tool):
    api = FakeKubernetes()

    async def check(backend):
        with pytest.raises(KubernetesRemediationError, match="approved Kubernetes target context"):
            await execute(backend, None, tool)
        assert api.requests == []

    asyncio.run(use_backend(api, check))


@pytest.mark.parametrize("stage,status", [("get", 403), ("get", 404), ("patch", 403), ("patch", 422)])
def test_missing_or_forbidden_resource_and_admission_rejections_are_not_accepted(stage, status):
    api = FakeKubernetes()
    setattr(api, f"{stage}_status", status)

    async def check(backend):
        with pytest.raises(KubernetesRemediationError, match=str(status)):
            await preflight(backend)
        assert api.ordinary_patches == []
        assert [request.method for request in api.requests] == (["GET"] if stage == "get" else ["GET", "PATCH"])

    asyncio.run(use_backend(api, check))


@pytest.mark.parametrize("change", [
    {"name": "another-service"}, {"namespace": "another-namespace"}, {"uid": None}, {"uid": ""},
    {"resourceVersion": None}, {"resourceVersion": 19}, {"resourceVersion": " "},
])
def test_preflight_requires_complete_exact_deployment_identity(change):
    api = FakeKubernetes()
    api.deployment["metadata"].update(change)

    async def check(backend):
        with pytest.raises(KubernetesRemediationError, match="Deployment identity"):
            await preflight(backend)
        assert [request.method for request in api.requests] == ["GET"]

    asyncio.run(use_backend(api, check))


@pytest.mark.parametrize("template", [None, {}, {"spec": {}}, {"spec": {"containers": []}},
                                      {"spec": {"containers": [{"name": SERVICE}]}},
                                      {"metadata": {"annotations": []}, "spec": {"containers": [{"name": SERVICE, "image": "image"}]}}])
def test_preflight_requires_actual_well_formed_pod_template(template):
    api = FakeKubernetes()
    api.deployment["spec"]["template"] = template

    async def check(backend):
        with pytest.raises(KubernetesRemediationError, match="template"):
            await preflight(backend)
        assert [request.method for request in api.requests] == ["GET"]

    asyncio.run(use_backend(api, check))


@pytest.mark.parametrize("change", [{"name": "another-service"}, {"namespace": "another-namespace"},
                                    {"uid": "replacement-uid"}, {"uid": None}, {"resourceVersion": None}])
def test_dryrun_response_identity_must_still_match_observed_target(change):
    api = FakeKubernetes()
    api.patch_response = lambda response: response["metadata"].update(change)

    async def check(backend):
        with pytest.raises(KubernetesRemediationError, match="identity|UID"):
            await preflight(backend)
        assert api.ordinary_patches == []

    asyncio.run(use_backend(api, check))


@pytest.mark.parametrize("case", ["continued", "oversized", "ambiguous", "foreign", "missing_uid", "missing_template", "malformed"])
def test_rollback_preflight_rejects_incomplete_nonunique_unowned_or_invalid_retained_target(case):
    api = FakeKubernetes()
    if case == "continued":
        api.replicasets["metadata"]["continue"] = "next-page"
    elif case == "oversized":
        api.replicasets["items"] *= 201
    elif case == "ambiguous":
        api.replicasets["items"].append(copy.deepcopy(api.replicasets["items"][0]))
    elif case == "foreign":
        api.replicasets["items"][0]["metadata"]["ownerReferences"][0]["uid"] = "another-deployment-uid"
    elif case == "missing_uid":
        del api.replicasets["items"][0]["metadata"]["uid"]
    elif case == "missing_template":
        del api.replicasets["items"][0]["spec"]["template"]
    else:
        api.replicasets["items"][0]["metadata"]["ownerReferences"] = [None]

    async def check(backend):
        with pytest.raises(KubernetesRemediationError):
            await preflight(backend, "rollback_release")
        assert [request.method for request in api.requests] == ["GET", "GET"]
        assert api.requests[1].url.params["limit"] == "200"

    asyncio.run(use_backend(api, check))


@pytest.mark.parametrize("change", [{"service": "another-service"}, {"namespace": "another-namespace"}, {"cluster_context": "0" * 64}])
def test_changed_approved_scope_rejected_before_io(change):
    api = FakeKubernetes()

    async def check(backend):
        context = (await preflight(backend))["target_context"]
        api.requests.clear()
        with pytest.raises(KubernetesRemediationError, match="scope or cluster context"):
            await execute(backend, {**context, **change})
        assert api.requests == []

    asyncio.run(use_backend(api, check))


@pytest.mark.parametrize("change", ["uid", "image", "account", "annotations"])
def test_changed_deployment_uid_or_entire_template_rejected_before_mutation(change):
    api = FakeKubernetes()

    async def check(backend):
        context = (await preflight(backend))["target_context"]
        if change == "uid":
            api.deployment["metadata"]["uid"] = "replacement-deployment-uid"
        elif change == "image":
            api.deployment["spec"]["template"]["spec"]["containers"][0]["image"] = "different-image"
        elif change == "account":
            api.deployment["spec"]["template"]["spec"]["serviceAccountName"] = "different-account"
        else:
            api.deployment["spec"]["template"]["metadata"]["annotations"]["release"] = "different-release"
        api.requests.clear()
        with pytest.raises(KubernetesRemediationError, match="UID|template changed"):
            await execute(backend, context)
        assert [request.method for request in api.requests] == ["GET"]
        assert api.ordinary_patches == []

    asyncio.run(use_backend(api, check))


def test_controller_only_resource_version_drift_uses_fresh_conditional_version():
    api = FakeKubernetes()

    async def check(backend):
        context = (await preflight(backend))["target_context"]
        api.deployment["metadata"]["resourceVersion"] = "controller-updated-21"
        api.deployment["status"] = {"observedGeneration": 3}
        await execute(backend, context)
        assert json.loads(api.ordinary_patches[0].content)["metadata"]["resourceVersion"] == "controller-updated-21"

    asyncio.run(use_backend(api, check))


@pytest.mark.parametrize("change", ["uid", "template", "revision"])
def test_changed_rollback_replicaset_identity_or_template_rejected_before_mutation(change):
    api = FakeKubernetes()

    async def check(backend):
        context = (await preflight(backend, "rollback_release"))["target_context"]
        target = api.replicasets["items"][0]
        if change == "uid":
            target["metadata"]["uid"] = "replacement-replicaset-uid"
        elif change == "template":
            target["spec"]["template"]["spec"]["serviceAccountName"] = "different-historical-account"
        else:
            target["metadata"]["annotations"]["deployment.kubernetes.io/revision"] = "140"
        api.requests.clear()
        with pytest.raises(KubernetesRemediationError, match="ReplicaSet|unique owned"):
            await execute(backend, context, "rollback_release")
        assert [request.method for request in api.requests] == ["GET", "GET"]
        assert api.ordinary_patches == []

    asyncio.run(use_backend(api, check))


@pytest.mark.parametrize("change", ["uid", "template", "rollback_uid", "unchanged", "replacement_with_marker", "same_uid_marker"])
def test_conflict_refresh_rechecks_approval_identity_without_second_mutation(change):
    api = FakeKubernetes()
    tool = "rollback_release" if change == "rollback_uid" else "restart_deployment"

    async def check(backend):
        context = (await preflight(backend, tool))["target_context"]

        def conflict(current, request):
            if change in {"uid", "replacement_with_marker"}:
                current.deployment["metadata"]["uid"] = "replacement-deployment-uid"
            elif change == "template":
                current.deployment["spec"]["template"]["spec"]["serviceAccountName"] = "changed-account"
            elif change == "rollback_uid":
                current.replicasets["items"][0]["metadata"]["uid"] = "changed-replicaset-uid"
            if change.endswith("marker"):
                current.deployment["spec"]["template"]["metadata"]["annotations"].update(
                    json.loads(request.content)["spec"]["template"]["metadata"]["annotations"])

        api.conflict = conflict
        if change == "same_uid_marker":
            assert (await execute(backend, context, tool))["idempotent_replay"] is True
        else:
            expected = KubernetesConflictError if change == "unchanged" else KubernetesRemediationError
            with pytest.raises(expected, match="changed|ReplicaSet|UID"):
                await execute(backend, context, tool)
        assert len(api.ordinary_patches) == 1
        assert api.requests[-1].method == "GET"

    asyncio.run(use_backend(api, check))


def test_replacement_uid_cannot_replay_copied_matching_marker_or_status():
    api = FakeKubernetes()

    async def check(backend):
        context = (await preflight(backend))["target_context"]
        await execute(backend, context)
        api.deployment["metadata"]["uid"] = "replacement-with-copied-annotations"
        api.requests.clear()
        with pytest.raises(KubernetesRemediationError, match="UID changed"):
            await execute(backend, context)
        with pytest.raises(KubernetesRemediationError, match="UID changed"):
            await backend.query_operation_status(service=SERVICE, namespace=NAMESPACE, tool="restart_deployment", revision=None,
                                                  idempotency_key=KEY, operation_binding="bound-human-approval", target_context=context)
        assert [request.method for request in api.requests] == ["GET", "GET"]

    asyncio.run(use_backend(api, check))


def test_cluster_context_binds_api_origin_and_trust_bytes_but_never_bearer_token(tmp_path):
    ca = tmp_path / "ca.crt"
    ca.write_bytes(b"test trust anchor A")
    client = httpx.AsyncClient(base_url="https://kubernetes.test", transport=httpx.MockTransport(FakeKubernetes()))
    options = {"api_url": "https://kubernetes.test", "allowed_namespaces": {NAMESPACE}, "client": client, "ca_file": str(ca)}
    first = KubernetesRemediator(**options, token="first-test-only-token")
    assert first.cluster_context == KubernetesRemediator(**options, token="rotated-test-only-token").cluster_context
    assert first.cluster_context != KubernetesRemediator(**{**options, "api_url": "https://other-kubernetes.test"}).cluster_context
    ca.write_bytes(b"test trust anchor B")
    assert first.cluster_context != KubernetesRemediator(**options).cluster_context
    asyncio.run(client.aclose())


@pytest.mark.parametrize("drift", ["api_origin", "ca_file"])
def test_execution_on_different_configured_cluster_context_rejected_before_io(tmp_path, drift):
    api = FakeKubernetes()
    ca = tmp_path / "ca.crt"
    ca.write_bytes(b"test trust anchor A")

    async def check(backend):
        context = (await preflight(backend))["target_context"]
        if drift == "ca_file":
            ca.write_bytes(b"test trust anchor B")
        other = KubernetesRemediator(api_url="https://other-kubernetes.test" if drift == "api_origin" else backend.api_url,
                                     allowed_namespaces={NAMESPACE}, client=backend._client, ca_file=str(ca))
        api.requests.clear()
        with pytest.raises(KubernetesRemediationError, match="cluster context changed"):
            await execute(other, context)
        assert api.requests == []

    asyncio.run(use_backend(api, check, ca_file=str(ca)))


@pytest.mark.parametrize("change", [{"resource_version": 19}, {"deployment_uid": ""}, {"cluster_context": "A" * 64},
                                    {"deployment_template_sha256": "invalid"}, {"extra": "not allowed"}, {"revision": "141"},
                                    {"tool": "rollback_release"}])
def test_target_context_normalizer_rejects_coercion_extras_and_wrong_action_fields(change):
    context = {"tool": "restart_deployment", "service": SERVICE, "namespace": NAMESPACE, "cluster_context": "a" * 64,
               "deployment_uid": "observed-uid", "resource_version": "19", "deployment_template_sha256": "b" * 64}
    with pytest.raises(ValueError):
        normalize_target_context({**context, **change})


def test_target_context_digest_is_canonical_and_binds_all_observed_identity_fields():
    context = {"tool": "restart_deployment", "service": SERVICE, "namespace": NAMESPACE, "cluster_context": "a" * 64,
               "deployment_uid": "observed-uid", "resource_version": "19", "deployment_template_sha256": "b" * 64}
    normalized = normalize_target_context(context)
    assert normalized["revision"] is None and normalized["replicaset_uid"] is None and normalized["rollback_template_sha256"] is None
    assert target_context_digest(context) == target_context_digest(dict(reversed(list(normalized.items()))))
    for key, value in {"service": "another-service", "namespace": "another-namespace", "cluster_context": "c" * 64,
                       "deployment_uid": "other-uid", "resource_version": "20", "deployment_template_sha256": "d" * 64}.items():
        assert target_context_digest(context) != target_context_digest({**context, key: value})
