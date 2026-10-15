"""Canonical, backend-observed Kubernetes approval target identity.

``cluster_context`` binds configuration (API origin and CA-file content), not an
authoritative Kubernetes cluster UID. Resource versions are observations, not a
promise that controllers will leave the resource unchanged until execution.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class KubernetesTargetContext(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    tool: Literal["restart_deployment", "rollback_release"]
    service: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    namespace: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    cluster_context: str = Field(pattern=r"^[0-9a-f]{64}$")
    deployment_uid: str = Field(min_length=1, max_length=128, pattern=r"^\S+$")
    resource_version: str = Field(min_length=1, max_length=128, pattern=r"^\S+$")
    deployment_template_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    revision: str | None = Field(default=None, pattern=r"^[1-9][0-9]{0,11}$")
    replicaset_uid: str | None = Field(default=None, min_length=1, max_length=128, pattern=r"^\S+$")
    rollback_template_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def action_target(self) -> KubernetesTargetContext:
        rollback_fields = (self.revision, self.replicaset_uid, self.rollback_template_sha256)
        if self.tool == "rollback_release" and any(value is None for value in rollback_fields):
            raise ValueError("Rollback target requires revision, ReplicaSet UID, and template digest.")
        if self.tool == "restart_deployment" and any(value is not None for value in rollback_fields):
            raise ValueError("Restart target cannot include rollback identity.")
        return self


def normalize_target_context(value: KubernetesTargetContext | dict[str, Any]) -> dict[str, Any]:
    """Validate strictly and normalize absent optional restart fields to null."""
    return KubernetesTargetContext.model_validate(value).model_dump(mode="json")


def target_context_digest(value: KubernetesTargetContext | dict[str, Any]) -> str:
    normalized = normalize_target_context(value)
    return hashlib.sha256(json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
