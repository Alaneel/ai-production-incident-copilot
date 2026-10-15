from __future__ import annotations

import os
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from .target_context import KubernetesTargetContext, normalize_target_context


class KubernetesRemediationError(RuntimeError):
    pass


class KubernetesConflictError(KubernetesRemediationError):
    pass


class KubernetesRemediator:
    def __init__(
        self,
        *,
        api_url: str,
        allowed_namespaces: set[str],
        token: str | None = None,
        token_file: str = "/var/run/secrets/kubernetes.io/serviceaccount/token",
        ca_file: str = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self.allowed_namespaces = allowed_namespaces
        configured_url = urlsplit(self.api_url)
        if (configured_url.scheme not in {"http", "https"} or not configured_url.hostname
                or configured_url.username is not None or configured_url.password is not None
                or configured_url.query or configured_url.fragment):
            raise KubernetesRemediationError("Kubernetes API configuration must identify an HTTP(S) origin without credentials.")
        # Configuration binding is deliberately not presented as a cluster UID.
        # Never hash the bearer token: credential rotation must not redefine the
        # approval target, and token material must not enter an audit record.
        api_origin = f"{configured_url.scheme}://{configured_url.netloc.lower()}"
        trust_file = Path(ca_file)
        trust_digest = hashlib.sha256(trust_file.read_bytes()).hexdigest() if trust_file.exists() else None
        self.cluster_context = self._digest({"api_origin": api_origin, "ca_file_sha256": trust_digest})
        self._owns_client = client is None
        if client is None:
            bearer = token or Path(token_file).read_text().strip()
            verify: bool | str = ca_file if Path(ca_file).exists() else True
            self._client = httpx.AsyncClient(
                base_url=self.api_url,
                verify=verify,
                timeout=httpx.Timeout(8.0),
                limits=httpx.Limits(
                    max_connections=10,
                    max_keepalive_connections=5,
                ),
                headers={"Authorization": f"Bearer {bearer}"},
            )
        else:
            self._client = client

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def preflight(
        self, *, tool: str, service: str, namespace: str,
        idempotency_key: str, revision: str | None = None,
    ) -> dict[str, Any]:
        """Ask Kubernetes admission to validate the same conditional patch.

        This is a server-side dry run, never an approval or cached execution.
        Every invocation observes a fresh Deployment and rollback target.
        """
        self._require_namespace(namespace)
        self._require_action_scope(tool, service, namespace, revision)
        deployment = await self._get_deployment(service, namespace)
        context: dict[str, Any] = {
            "tool": tool, "service": service, "namespace": namespace,
            "cluster_context": self.cluster_context,
            "deployment_uid": deployment["metadata"]["uid"],
            "resource_version": deployment["metadata"]["resourceVersion"],
            "deployment_template_sha256": self._digest(deployment["spec"]["template"]),
        }
        rollback_target = None
        if tool == "rollback_release":
            rollback_target = await self._rollback_target(deployment, service, namespace, revision)
            context.update({
                "revision": revision,
                "replicaset_uid": rollback_target["metadata"]["uid"],
                "rollback_template_sha256": self._digest(rollback_target["spec"]["template"]),
            })
        context = normalize_target_context(context)
        fingerprint = self.operation_fingerprint(tool, service, namespace, revision, None)
        patch, details = self._action_patch(tool, deployment, idempotency_key, fingerprint, revision, rollback_target)
        result = await self._request(
            "PATCH", self._deployment_path(service, namespace),
            params={"dryRun": "All"}, json=patch,
        )
        self._validate_deployment(result, service, namespace, expected_uid=context["deployment_uid"])
        return {
            "accepted": True, "executed": False, "dry_run": True,
            "preflight_mode": "kubernetes-server-dry-run", "backend": "kubernetes",
            "target_context": context,
            "kubernetes_resource_version": result["metadata"]["resourceVersion"],
            "cluster_context_semantics": "configured_api_origin_and_ca_file_sha256_not_cluster_uid",
            **details,
        }

    async def restart_deployment(
        self,
        *,
        service: str,
        namespace: str,
        idempotency_key: str,
        operation_binding: str | None = None,
        target_context: KubernetesTargetContext | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self._execute(
            tool="restart_deployment", service=service, namespace=namespace,
            idempotency_key=idempotency_key, operation_binding=operation_binding,
            target_context=target_context,
        )

    async def rollback_release(
        self,
        *,
        service: str,
        namespace: str,
        revision: str,
        idempotency_key: str,
        operation_binding: str | None = None,
        target_context: KubernetesTargetContext | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self._execute(
            tool="rollback_release", service=service, namespace=namespace, revision=revision,
            idempotency_key=idempotency_key, operation_binding=operation_binding,
            target_context=target_context,
        )

    async def _execute(
        self, *, tool: str, service: str, namespace: str, idempotency_key: str,
        operation_binding: str | None,
        target_context: KubernetesTargetContext | dict[str, Any] | None,
        revision: str | None = None,
    ) -> dict[str, Any]:
        self._require_namespace(namespace)
        context = self._approved_context(target_context, tool, service, namespace, revision)
        deployment = await self._get_deployment(service, namespace)
        self._require_target_identity(deployment, context)
        fingerprint = self.operation_fingerprint(tool, service, namespace, revision, operation_binding)
        if self._was_applied(deployment, idempotency_key, fingerprint):
            return self._replay_result(deployment)
        self._require_template_unchanged(deployment, context)
        target = None
        if tool == "rollback_release":
            target = await self._rollback_target(deployment, service, namespace, revision)
            self._require_rollback_unchanged(target, context)
        patch, details = self._action_patch(tool, deployment, idempotency_key, fingerprint, revision, target)
        try:
            result = await self._request("PATCH", self._deployment_path(service, namespace), json=patch)
        except KubernetesConflictError:
            latest = await self._get_deployment(service, namespace)
            # A matching marker on a replacement Deployment is not a replay.
            self._require_target_identity(latest, context)
            if self._was_applied(latest, idempotency_key, fingerprint):
                return self._replay_result(latest)
            self._require_template_unchanged(latest, context)
            if tool == "rollback_release":
                current_target = await self._rollback_target(latest, service, namespace, revision)
                self._require_rollback_unchanged(current_target, context)
            raise
        self._validate_deployment(result, service, namespace, expected_uid=context["deployment_uid"])
        return {"kubernetes_resource_version": result["metadata"]["resourceVersion"],
                "idempotent_replay": False, **self._applied_revision(result), **details}

    async def _rollback_target(
        self, deployment: dict[str, Any], service: str, namespace: str, revision: str | None,
    ) -> dict[str, Any]:
        replicasets = await self._request(
            "GET",
            f"/apis/apps/v1/namespaces/{namespace}/replicasets",
            params={"limit": 200},
        )
        from .deployments import owned_by
        if (not isinstance(replicasets.get("items"), list) or len(replicasets["items"]) > 200
                or not isinstance(replicasets.get("metadata", {}), dict)
                or replicasets.get("metadata", {}).get("continue")):
            raise KubernetesRemediationError("Rollback history is incomplete within the query budget.")
        if any(not isinstance(item, dict) or not isinstance(item.get("metadata"), dict)
               or not isinstance(item["metadata"].get("ownerReferences", []), list)
               or any(not isinstance(owner, dict) for owner in item["metadata"].get("ownerReferences", []))
               or not isinstance(item["metadata"].get("annotations", {}), dict)
               for item in replicasets["items"]):
            raise KubernetesRemediationError("Rollback history contains malformed resource identities.")
        targets = [
                item
                for item in replicasets.get("items", [])
                if owned_by(item, deployment, service, namespace) and item.get("metadata", {})
                .get("annotations", {})
                .get("deployment.kubernetes.io/revision")
                == revision
        ]
        if len(targets) != 1:
            raise KubernetesRemediationError(
                "A unique owned ReplicaSet target was not found for rollback."
            )
        target = targets[0]
        if not self._identity_value(target["metadata"].get("uid")):
            raise KubernetesRemediationError("Rollback ReplicaSet identity is incomplete.")
        self._template(target)
        return target

    async def query_operation_status(self, *, service: str, namespace: str, tool: str,
                                     revision: str | None, idempotency_key: str,
                                     operation_binding: str,
                                     target_context: KubernetesTargetContext | dict[str, Any] | None = None) -> dict[str, Any]:
        self._require_namespace(namespace)
        self._require_action_scope(tool, service, namespace, revision)
        context = self._approved_context(target_context, tool, service, namespace, revision) if target_context is not None else None
        deployment = await self._get_deployment(service, namespace)
        if context is not None:
            self._require_target_identity(deployment, context)
        fingerprint = self.operation_fingerprint(tool, service, namespace, revision, operation_binding)
        status = self._marker_status(deployment, idempotency_key, fingerprint)
        return {"status": status, "source": "kubernetes_deployment_marker", "read_only": True,
                "observed_at": datetime.now(UTC).isoformat(),
                "kubernetes_resource_version": deployment.get("metadata", {}).get("resourceVersion"),
                "rollout_health_verified": False,
                "note": "Latest marker only. Absence does not prove non-execution; a match does not prove rollout health."}

    async def query_deployment_health(
        self, *, service: str, namespace: str, tool: str,
        revision: str | None, idempotency_key: str, operation_binding: str,
        target_context: KubernetesTargetContext | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Read a fresh, identity-bound snapshot; do not infer recovery from it.

        The action changes the pod template, so its preflight template must not
        be compared here. The caller must compare this snapshot to the actual
        PATCH acknowledgement, then separately evaluate post-action telemetry.
        """
        self._require_namespace(namespace)
        context = self._approved_context(target_context, tool, service, namespace, revision)
        deployment = await self._get_deployment(service, namespace)
        self._require_target_identity(deployment, context)
        if deployment["metadata"].get("deletionTimestamp") is not None:
            raise KubernetesRemediationError("A deleting Deployment cannot provide rollout-health evidence.")
        generation = self._integer_field(deployment["metadata"], "generation", minimum=1, required=True)
        status = deployment.get("status", {})
        if not isinstance(status, dict):
            raise KubernetesRemediationError("Deployment status is malformed.")
        observed_generation = self._integer_field(status, "observedGeneration")
        if observed_generation is not None and observed_generation > generation:
            raise KubernetesRemediationError("Deployment controller generation is inconsistent.")
        fingerprint = self.operation_fingerprint(tool, service, namespace, revision, operation_binding)
        return {
            "source": "kubernetes_deployment_health", "read_only": True,
            "marker_status": self._marker_status(deployment, idempotency_key, fingerprint),
            "observed_at": datetime.now(UTC).isoformat(),
            "service": service, "namespace": namespace, "cluster_context": self.cluster_context,
            "deployment_uid": deployment["metadata"]["uid"], "generation": generation,
            "template_sha256": self._digest(deployment["spec"]["template"]),
            "observed_generation": observed_generation,
            "desired_replicas": self._integer_field(deployment["spec"], "replicas"),
            "total_replicas": self._integer_field(status, "replicas"),
            "updated_replicas": self._integer_field(status, "updatedReplicas"),
            "ready_replicas": self._integer_field(status, "readyReplicas"),
            "available_replicas": self._integer_field(status, "availableReplicas"),
            # Kubernetes serializes this int32 with omitempty. Only omission
            # within an observed status object means zero; absent status or a
            # present malformed/null field cannot establish rollout health.
            "unavailable_replicas": (0 if "status" in deployment and "unavailableReplicas" not in status
                                     else self._integer_field(status, "unavailableReplicas")),
        }

    @staticmethod
    def _integer_field(resource: dict[str, Any], field: str, *, minimum: int = 0,
                       required: bool = False) -> int | None:
        if field not in resource and not required:
            return None
        value = resource.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < minimum or value > (1 << 63) - 1:
            raise KubernetesRemediationError("Deployment generation or replica count is malformed.")
        return value

    @staticmethod
    def _marker_status(deployment: dict[str, Any], idempotency_key: str, fingerprint: str) -> str:
        annotations = deployment["spec"]["template"].get("metadata", {}).get("annotations", {})
        if annotations.get("incident-copilot.openai/idempotency-key") != idempotency_key:
            return "not_observed"
        observed = annotations.get("incident-copilot.openai/operation-fingerprint")
        return "marker_matches" if observed == fingerprint else "unverified_marker" if observed is None else "conflicting_marker"

    async def _get_deployment(
        self,
        service: str,
        namespace: str,
    ) -> dict[str, Any]:
        deployment = await self._request(
            "GET",
            self._deployment_path(service, namespace),
        )
        self._validate_deployment(deployment, service, namespace)
        return deployment

    @staticmethod
    def _deployment_path(service: str, namespace: str) -> str:
        return f"/apis/apps/v1/namespaces/{namespace}/deployments/{service}"

    @staticmethod
    def _identity_value(value: Any) -> bool:
        return isinstance(value, str) and 1 <= len(value) <= 128 and re.fullmatch(r"\S+", value) is not None

    @classmethod
    def _template(cls, resource: dict[str, Any]) -> dict[str, Any]:
        spec = resource.get("spec")
        template = spec.get("template") if isinstance(spec, dict) else None
        pod_spec = template.get("spec") if isinstance(template, dict) else None
        containers = pod_spec.get("containers") if isinstance(pod_spec, dict) else None
        if (not isinstance(template, dict) or not isinstance(template.get("metadata", {}), dict)
                or not isinstance(containers, list) or not containers
                or any(not isinstance(item, dict) or not cls._identity_value(item.get("name"))
                       or not isinstance(item.get("image"), str) or not item["image"].strip()
                       for item in containers)
                or len({item["name"] for item in containers}) != len(containers)):
            raise KubernetesRemediationError("Kubernetes resource has no valid pod template/container images.")
        if not isinstance(template.get("metadata", {}).get("annotations", {}), dict):
            raise KubernetesRemediationError("Kubernetes pod template annotations are malformed.")
        return template

    @classmethod
    def _validate_deployment(
        cls, deployment: dict[str, Any], service: str, namespace: str, *, expected_uid: str | None = None,
    ) -> None:
        metadata = deployment.get("metadata")
        if (not isinstance(metadata, dict) or metadata.get("name") != service
                or metadata.get("namespace") != namespace or not cls._identity_value(metadata.get("uid"))
                or not cls._identity_value(metadata.get("resourceVersion"))):
            raise KubernetesRemediationError("Deployment identity is missing, malformed, or differs from the requested target.")
        if expected_uid is not None and metadata["uid"] != expected_uid:
            raise KubernetesRemediationError("Deployment UID changed from the approved target.")
        cls._template(deployment)

    @staticmethod
    def _digest(value: Any) -> str:
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()

    @staticmethod
    def _require_action_scope(tool: str, service: str, namespace: str, revision: str | None) -> None:
        if (tool not in {"restart_deployment", "rollback_release"}
                or re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", service) is None
                or re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", namespace) is None
                or (tool == "restart_deployment" and revision is not None)
                or (tool == "rollback_release" and (not isinstance(revision, str)
                    or re.fullmatch(r"[1-9][0-9]{0,11}", revision) is None))):
            raise KubernetesRemediationError("Invalid Kubernetes remediation scope or revision.")

    def _approved_context(
        self, value: KubernetesTargetContext | dict[str, Any] | None,
        tool: str, service: str, namespace: str, revision: str | None,
    ) -> dict[str, Any]:
        self._require_action_scope(tool, service, namespace, revision)
        if value is None:
            raise KubernetesRemediationError("Execution requires an approved Kubernetes target context.")
        try:
            context = normalize_target_context(value)
        except (ValueError, TypeError) as error:
            raise KubernetesRemediationError("Approved Kubernetes target context is invalid.") from error
        if any(context[key] != expected for key, expected in {
            "tool": tool, "service": service, "namespace": namespace,
            "cluster_context": self.cluster_context, "revision": revision,
        }.items()):
            raise KubernetesRemediationError("Approved Kubernetes target scope or cluster context changed.")
        return context

    @staticmethod
    def _require_target_identity(deployment: dict[str, Any], context: dict[str, Any]) -> None:
        if deployment["metadata"]["uid"] != context["deployment_uid"]:
            raise KubernetesRemediationError("Deployment UID changed from the approved target.")

    @classmethod
    def _require_template_unchanged(cls, deployment: dict[str, Any], context: dict[str, Any]) -> None:
        if cls._digest(deployment["spec"]["template"]) != context["deployment_template_sha256"]:
            raise KubernetesRemediationError("Deployment pod template changed since preflight; a new approval is required.")

    @classmethod
    def _require_rollback_unchanged(cls, target: dict[str, Any], context: dict[str, Any]) -> None:
        if (target["metadata"]["uid"] != context["replicaset_uid"]
                or target["metadata"].get("annotations", {}).get("deployment.kubernetes.io/revision") != context["revision"]
                or cls._digest(target["spec"]["template"]) != context["rollback_template_sha256"]):
            raise KubernetesRemediationError("Rollback ReplicaSet identity or pod template changed since preflight.")

    @staticmethod
    def _action_patch(
        tool: str, deployment: dict[str, Any], idempotency_key: str, fingerprint: str,
        revision: str | None, rollback_target: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        annotations = {
            "incident-copilot.openai/idempotency-key": idempotency_key,
            "incident-copilot.openai/operation-fingerprint": fingerprint,
        }
        template: dict[str, Any] = {"metadata": {"annotations": annotations}}
        details: dict[str, Any] = {}
        timestamp = datetime.now(UTC).isoformat()
        if tool == "restart_deployment":
            annotations["incident-copilot.openai/restarted-at"] = timestamp
            details["restarted_at"] = timestamp
        else:
            if rollback_target is None:
                raise KubernetesRemediationError("Rollback requires an observed ReplicaSet target.")
            images = [{"name": item["name"], "image": item["image"]}
                      for item in rollback_target["spec"]["template"]["spec"]["containers"]]
            template["spec"] = {"containers": images}
            annotations.update({"incident-copilot.openai/rollback-revision": revision,
                                "incident-copilot.openai/rollback-at": timestamp})
            details.update({"container_images": images, "rollback_scope": "container_images_only_not_full_release"})
        return ({"metadata": {"resourceVersion": deployment["metadata"]["resourceVersion"]},
                 "spec": {"template": template}}, details)

    @staticmethod
    def operation_fingerprint(tool: str, service: str, namespace: str,
                              revision: str | None, operation_binding: str | None) -> str:
        payload = {"tool": tool, "service": service, "namespace": namespace,
                   "revision": revision, "approval_binding": operation_binding}
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    @staticmethod
    def _was_applied(deployment: dict[str, Any], idempotency_key: str, fingerprint: str) -> bool:
        annotations = (
            deployment.get("spec", {})
            .get("template", {})
            .get("metadata", {})
            .get("annotations", {})
        )
        if annotations.get("incident-copilot.openai/idempotency-key") != idempotency_key:
            return False
        if annotations.get("incident-copilot.openai/operation-fingerprint") != fingerprint:
            raise KubernetesRemediationError("Existing idempotency marker has a missing or conflicting operation fingerprint; manual outcome review required.")
        return True

    @classmethod
    def _applied_revision(cls, deployment: dict[str, Any]) -> dict[str, Any]:
        # Never synthesize the PATCH generation by incrementing a stale GET.
        # Missing acknowledgement fields cannot become recovery evidence.
        return {"applied_generation": cls._integer_field(deployment["metadata"], "generation", minimum=1),
                "applied_template_sha256": cls._digest(deployment["spec"]["template"])}

    @classmethod
    def _replay_result(cls, deployment: dict[str, Any]) -> dict[str, Any]:
        return {
            "kubernetes_resource_version": (
                deployment.get("metadata", {}).get("resourceVersion")
            ),
            "idempotent_replay": True,
            **cls._applied_revision(deployment),
        }

    def _require_namespace(self, namespace: str) -> None:
        if namespace not in self.allowed_namespaces:
            raise KubernetesRemediationError(
                f"Namespace {namespace!r} is not in the remediation allow-list."
            )

    async def _request(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        try:
            response = await self._client.request(
                method,
                path,
                headers={
                    "Content-Type": "application/strategic-merge-patch+json"
                    if method == "PATCH"
                    else "application/json"
                },
                **kwargs,
            )
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict):
                raise KubernetesRemediationError(
                    "Kubernetes API response must be an object."
                )
            return result
        except (ValueError, TypeError) as error:
            raise KubernetesRemediationError("Malformed Kubernetes API response.") from error
        except (httpx.TimeoutException, httpx.TransportError) as error:
            raise KubernetesRemediationError(
                "Kubernetes API transport failure."
            ) from error
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 409:
                raise KubernetesConflictError(
                    "Kubernetes resource changed during remediation."
                ) from error
            raise KubernetesRemediationError(
                f"Kubernetes API failure: {error.response.status_code}"
            ) from error


def in_cluster_api_url() -> str:
    host = os.getenv("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc")
    port = os.getenv("KUBERNETES_SERVICE_PORT_HTTPS", "443")
    return os.getenv("KUBERNETES_API_URL", f"https://{host}:{port}")
