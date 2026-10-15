from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .target_context import KubernetesTargetContext


class StrictArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ServiceArguments(StrictArguments):
    service: str = Field(pattern=r"^[a-z0-9-]{2,80}$")


class QueryMetricsArguments(ServiceArguments):
    metric: Literal[
        "request_latency_p95",
        "request_error_rate",
        "db_pool_active_ratio",
        "cpu_utilization",
        "memory_working_set",
        "kafka_consumer_lag",
        "retry_rate",
        "cache_miss_rate",
    ]
    start: datetime
    end: datetime
    step_seconds: int = Field(default=60, ge=1, le=3600)


class SearchLogsArguments(ServiceArguments):
    query: str = Field(default="", max_length=500)
    start: datetime
    end: datetime
    limit: int = Field(default=100, ge=1, le=1000)


class SearchTracesArguments(ServiceArguments):
    start: datetime
    end: datetime
    limit: int = Field(default=20, ge=1, le=100)


class SearchRunbooksArguments(ServiceArguments):
    query: str = Field(min_length=2, max_length=300)
    limit: int = Field(default=5, ge=1, le=20)


class SearchKnowledgeArguments(SearchRunbooksArguments):
    content_types: list[Literal["runbook", "past_incident", "technical_doc"]] = Field(
        default_factory=lambda: [
            "runbook",
            "past_incident",
            "technical_doc",
        ],
        min_length=1,
        max_length=3,
    )


class RecentDeploymentsArguments(ServiceArguments):
    namespace: str = Field(default="prod-sg", pattern=r"^[a-z0-9-]{2,63}$")
    limit: int = Field(default=10, ge=1, le=50)


class RestartDeploymentArguments(ServiceArguments):
    namespace: str = Field(pattern=r"^[a-z0-9-]{2,63}$")
    dry_run: bool = True
    approval_receipt: str | None = None
    idempotency_key: str = Field(min_length=8, max_length=200)
    target_context: KubernetesTargetContext | None = None


class RollbackReleaseArguments(RestartDeploymentArguments):
    revision: str = Field(pattern=r"^[a-zA-Z0-9._-]{1,80}$")


class OperationStatusArguments(ServiceArguments):
    namespace: str = Field(pattern=r"^[a-z0-9-]{2,63}$")
    tool: Literal["restart_deployment", "rollback_release"]
    revision: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9._-]{1,80}$")
    idempotency_key: str = Field(min_length=8, max_length=200)
    approval_actor: str = Field(min_length=2, max_length=120)
    incident_id: str = Field(min_length=2, max_length=80)
    target_context: KubernetesTargetContext | None = None

    @model_validator(mode="after")
    def action_target(self):
        if (self.tool == "rollback_release") != (self.revision is not None):
            raise ValueError("Only rollback_release requires a revision.")
        return self
