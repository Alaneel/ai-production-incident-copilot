"""Read-only, bounded Kubernetes revision discovery; never fixture fallback."""
from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime

import httpx

from .kubernetes import KubernetesRemediationError, KubernetesRemediator
from .deployment_events import normalize_events


REVISION = "deployment.kubernetes.io/revision"


def owned_by(item, deployment, service, namespace):
    metadata = item.get("metadata", {})
    uid = deployment.get("metadata", {}).get("uid")
    return bool(uid) and metadata.get("namespace") == namespace and any(
        owner.get("uid") == uid and owner.get("name") == service and owner.get("kind") == "Deployment"
        and owner.get("apiVersion") == "apps/v1" and owner.get("controller") is True
        for owner in metadata.get("ownerReferences", [])
    )


class KubernetesDeploymentReader:
    def __init__(self, *, include_events=False, **parameters):
        # Reuse TLS/service-account setup, but expose GET-only discovery.
        # This reader is independent of the registry's mutation backend flag.
        self._transport = KubernetesRemediator(**parameters)
        self.include_events = include_events

    async def close(self):
        await self._transport.close()

    async def _get(self, path, *, params=None):
        try:
            async with self._transport._client.stream("GET", path, params=params) as response:
                response.raise_for_status()
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(body) + len(chunk) > 512 * 1024:
                        raise KubernetesRemediationError("Deployment history response exceeds its size budget")
                    body.extend(chunk)
            result = json.loads(body)
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except (httpx.HTTPError, ValueError) as error:
            raise KubernetesRemediationError("Read-only deployment history query failed") from error

    async def recent_deployments(self, *, service, namespace, limit=10):
        self._transport._require_namespace(namespace)
        if (re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", service) is None
                or re.fullmatch(r"[a-z0-9][a-z0-9-]{1,62}", namespace) is None or not 1 <= limit <= 50):
            raise KubernetesRemediationError("Invalid deployment discovery scope")
        try:
            async with asyncio.timeout(6):
                path = f"/apis/apps/v1/namespaces/{namespace}/deployments/{service}"
                deployment = await self._get(path)
                metadata = deployment.get("metadata", {})
                if metadata.get("name") != service or metadata.get("namespace") != namespace or not metadata.get("uid") or not metadata.get("resourceVersion"):
                    raise KubernetesRemediationError("Deployment identity is incomplete")
                replicasets = await self._get(f"/apis/apps/v1/namespaces/{namespace}/replicasets", params={"limit": 200})
                items = replicasets.get("items")
                if not isinstance(items, list) or len(items) > 200 or replicasets.get("metadata", {}).get("continue"):
                    raise KubernetesRemediationError("Deployment history is incomplete within the query budget")
                revisions = {}
                for item in items:
                    if not owned_by(item, deployment, service, namespace):
                        continue
                    revision = item.get("metadata", {}).get("annotations", {}).get(REVISION)
                    if not isinstance(revision, str) or re.fullmatch(r"[1-9][0-9]{0,11}", revision) is None:
                        raise KubernetesRemediationError("Owned ReplicaSet revision is invalid")
                    if revision in revisions:
                        raise KubernetesRemediationError("Owned ReplicaSet revisions are ambiguous")
                    revisions[revision] = item
                current = metadata.get("annotations", {}).get(REVISION)
                if current not in revisions:
                    raise KubernetesRemediationError("Current deployment revision is absent from retained history")
                events = {"status": "not_requested", "events": []}
                if self.include_events:
                    events = await self._event_observations(metadata["uid"], service, namespace)
                after = await self._get(path)
                if any(after.get("metadata", {}).get(key) != metadata.get(key) for key in ("uid", "resourceVersion")):
                    raise KubernetesRemediationError("Deployment changed while history was read")
                previous = max((int(value) for value in revisions if int(value) < int(current)), default=None)
                now = datetime.now(UTC).isoformat()
                # A reused ReplicaSet's creationTimestamp is not rollout time.
                # Return current state only, with explicit observation semantics.
                row = {"revision": current, "previous_revision": str(previous) if previous is not None else None,
                       "namespace": namespace, "deployment_uid": metadata["uid"],
                       "resource_version": metadata["resourceVersion"], "data_origin": "kubernetes_api",
                       "deployed_at": now, "timestamp_semantics": "query_observation_not_deployment_time",
                       "revision_created_at": revisions[current].get("metadata", {}).get("creationTimestamp"),
                       "retained_revisions": sorted(revisions, key=int, reverse=True)[:limit],
                       "event_timeline": events,
                       "summary": "Observed current Kubernetes revision and nearest lower retained owned revision; deployment causation is unverified."}
                return {"service": service, "namespace": namespace, "source": "kubernetes-api-owned-revision-history",
                        "data_origin": "kubernetes_api", "deployments": [row], "event_timeline": events}
        except TimeoutError as error:
            raise KubernetesRemediationError("Deployment discovery deadline exceeded") from error
        except (TypeError, AttributeError, KeyError) as error:
            raise KubernetesRemediationError("Malformed deployment discovery data") from error

    async def _event_observations(self, uid, service, namespace):
        # Optional context cannot replace/bypass authoritative revision checks.
        # A shorter sub-deadline leaves time for the final identity re-read.
        try:
            if not isinstance(uid, str) or re.fullmatch(r"[A-Za-z0-9-]{1,128}", uid) is None:
                raise KubernetesRemediationError("Invalid event subject identity")
            async with asyncio.timeout(1.5):
                result = await self._get(f"/api/v1/namespaces/{namespace}/events", params={
                    "fieldSelector": f"involvedObject.uid={uid}", "limit": 100})
            items = result.get("items")
            if not isinstance(items, list) or len(items) > 100 or result.get("metadata", {}).get("continue"):
                return {"status": "unavailable", "reason": "incomplete_event_list", "events": []}
            return normalize_events(items, uid=uid, service=service, namespace=namespace, now=datetime.now(UTC))
        except (KubernetesRemediationError, TimeoutError, TypeError, AttributeError):
            return {"status": "unavailable", "reason": "event_query_failed", "events": []}
