from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .dependency_topology import ServiceTopology


class WorkflowStep(StrEnum):
    CLASSIFY = "classify_incident"
    SELECT_SERVICES = "select_services"
    QUERY_TELEMETRY = "query_telemetry"
    RETRIEVE_CONTEXT = "retrieve_context"
    GENERATE_HYPOTHESES = "generate_hypotheses"
    VALIDATE_EVIDENCE = "validate_evidence"
    PROPOSE_REMEDIATION = "propose_remediation"
    HUMAN_APPROVAL = "human_approval"
    PREPARE_EXECUTION = "prepare_execution"
    EXECUTE = "execute_or_simulate"
    VERIFY_RECOVERY = "verify_recovery"
    WAIT_FOR_RECOVERY = "wait_for_recovery"
    COMPLETE = "complete"


class IncidentStatus(StrEnum):
    INVESTIGATING = "investigating"
    WAITING_APPROVAL = "waiting_approval"
    MITIGATING = "mitigating"
    DIAGNOSED = "diagnosed"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    REMEDIATION_FAILED = "remediation_failed"
    REMEDIATION_PENDING = "remediation_pending"
    RESOLVED = "resolved"
    REJECTED = "rejected"


class EvidenceKind(StrEnum):
    METRIC = "metric"
    LOG = "log"
    TRACE = "trace"
    DEPLOYMENT = "deployment"
    DEPLOYMENT_EVENT = "deployment_event"
    RUNBOOK = "runbook"
    INCIDENT = "past_incident"
    TECHNICAL_DOC = "technical_doc"


class ApprovalDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class IncidentQuestion(BaseModel):
    question: str = Field(min_length=5, max_length=2_000)
    environment: str = Field(default="prod-sg", pattern=r"^[a-z0-9-]{2,40}$")


class Evidence(BaseModel):
    id: str = Field(default_factory=lambda: f"ev_{uuid4().hex[:12]}")
    kind: EvidenceKind
    source: str
    title: str
    observation: str
    observed_at: datetime
    service: str
    score: float = Field(ge=0, le=1)
    query: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class Hypothesis(BaseModel):
    id: str = Field(default_factory=lambda: f"hyp_{uuid4().hex[:10]}")
    rank: int = Field(ge=1)
    cause: str
    confidence: float = Field(ge=0, le=1)
    evidence_ids: list[str]
    conflicting_evidence_ids: list[str] = Field(default_factory=list)
    validation_notes: list[str] = Field(default_factory=list)
    cause_service: str | None = None
    propagation_path: list[str] = Field(default_factory=list)
    causal_direction_verified: Literal[False] = False


class RootCauseCandidate(BaseModel):
    """A scoped investigation hypothesis, never an execution authorization."""

    rank: int = Field(ge=1)
    service: str
    incident_type: str
    local_evidence_ids: list[str]
    symptom_evidence_ids: list[str] = Field(default_factory=list)
    propagation_path: list[str] = Field(default_factory=list)
    path_basis: Literal["primary", "observed_trace", "catalog", "mixed"]
    temporal_overlap: bool | None = None
    causal_direction_verified: Literal[False] = False
    validation_notes: list[str] = Field(default_factory=list)


class KubernetesTargetContext(BaseModel):
    """Wire contract shared with MCP, without a cross-service package dependency.

    Cluster context fingerprints API origin/CA configuration, not a cluster UID.
    Optional restart fields normalize to null so both services sign the same bytes.
    """

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
            raise ValueError("Rollback requires revision, ReplicaSet UID and template digest.")
        if self.tool == "restart_deployment" and any(value is not None for value in rollback_fields):
            raise ValueError("Restart cannot include rollback identity.")
        return self


def normalize_target_context(value: KubernetesTargetContext | dict[str, Any]) -> dict[str, Any]:
    return KubernetesTargetContext.model_validate(value).model_dump(mode="json")


def target_context_digest(value: KubernetesTargetContext | dict[str, Any]) -> str:
    normalized = normalize_target_context(value)
    return hashlib.sha256(json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class Remediation(BaseModel):
    id: str = Field(default_factory=lambda: f"rem_{uuid4().hex[:10]}")
    title: str
    tool: str
    arguments: dict[str, Any]
    command_preview: str
    risk: str
    requires_approval: bool
    dry_run_passed: bool
    preflight_mode: Literal["simulation", "kubernetes-server-dry-run"] | None = None
    expected_result: str


class TraceEvent(BaseModel):
    sequence: int
    step: WorkflowStep
    event: str
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    duration_ms: int | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class IncidentState(BaseModel):
    incident_id: str = Field(default_factory=lambda: f"INC-{uuid4().hex[:6].upper()}")
    trace_id: str = Field(default_factory=lambda: f"tr_{uuid4().hex}")
    question: str
    environment: str
    status: IncidentStatus = IncidentStatus.INVESTIGATING
    current_step: WorkflowStep = WorkflowStep.CLASSIFY
    incident_type: str | None = None
    candidate_incident_types: list[str] = Field(default_factory=list)
    service_impacts: list[dict[str, Any]] = Field(default_factory=list)
    service_topology: ServiceTopology | None = None
    # services[0] and incident_type remain complaint-local for old checkpoints
    # and exact approval target binding. Candidate attribution is separate.
    complaint_service: str | None = None
    diagnosis_service: str | None = None
    service_topologies: list[ServiceTopology] = Field(default_factory=list)
    root_cause_candidates: list[RootCauseCandidate] = Field(default_factory=list)
    service_investigations: list[dict[str, Any]] = Field(default_factory=list)
    severity: str | None = None
    services: list[str] = Field(default_factory=list)
    anomalous_window: tuple[datetime, datetime] | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    diagnostic_excluded_evidence_ids: list[str] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    remediation: Remediation | None = None
    recovery: dict[str, Any] | None = None
    approval_decision: ApprovalDecision | None = None
    approval_actor: str | None = None
    approval_reason: str | None = None
    trace: list[TraceEvent] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ApprovalRequest(BaseModel):
    decision: ApprovalDecision
    actor: str = Field(min_length=2, max_length=120)
    reason: str = Field(min_length=3, max_length=1_000)
    idempotency_key: str = Field(min_length=8, max_length=200)
