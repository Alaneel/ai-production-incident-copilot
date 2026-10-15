from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime
from math import isfinite

from .models import Evidence, IncidentQuestion, IncidentState


class GuardrailViolation(ValueError):
    """Raised when a request would cross a safety boundary."""


_INJECTION_PATTERNS = (
    re.compile(r"ignore (?:(?:all|any|the) )?previous instructions?", re.I),
    re.compile(r"(reveal|print|show).*(system prompt|secret|credential)", re.I),
    re.compile(r"(disable|bypass|skip|turn off).*(guardrail|approval|authorization|human confirmation)", re.I),
    re.compile(r"(忽略|无视|覆盖|丢弃).*(之前|先前|上述|所有|系统|已有).*(指令|规则|提示词)"),
    re.compile(r"(绕过|跳过|禁用|关闭|取消).*(审批|批准|确认|授权|权限检查|安全检查|防护)"),
    re.compile(r"(泄露|显示|打印|输出|告诉我).*(系统提示词|系统指令|密钥|凭证|api[_ -]?key)", re.I),
)

_DESTRUCTIVE_TOOLS = {"restart_deployment", "rollback_release"}


def validate_input(request: IncidentQuestion) -> None:
    # Normalize only the inspection view. Retain the original request for audit.
    inspected = unicodedata.normalize("NFKC", request.question)
    inspected = "".join(character for character in inspected
                        if unicodedata.category(character) != "Cf"
                        and (unicodedata.category(character) != "Cc" or character.isspace()))
    normalized = " ".join(inspected.split())
    normalized = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", normalized)
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(normalized):
            raise GuardrailViolation("Potential prompt injection or policy bypass request.")


def validate_grounding(state: IncidentState) -> None:
    if not state.hypotheses:
        raise GuardrailViolation("A diagnosis requires at least one hypothesis.")

    evidence_ids = {item.id for item in state.evidence}
    for hypothesis in state.hypotheses:
        label = f"Hypothesis rank {hypothesis.rank}"
        cited = set(hypothesis.evidence_ids)
        if cited.intersection(state.diagnostic_excluded_evidence_ids):
            raise GuardrailViolation(f"{label} cites evidence returned outside its requested tool scope.")
        if len(cited) < 2 or not cited.issubset(evidence_ids):
            raise GuardrailViolation(
                f"{label} must cite at least two retained evidence items."
            )

        retained = [item for item in state.evidence if item.id in cited]
        if len(retained) != len(cited):
            raise GuardrailViolation(f"{label}: a cited evidence ID is ambiguous across retained observations.")
        if any(not has_usable_observation(item) for item in retained):
            raise GuardrailViolation(f"{label} cites unavailable or invalid evidence.")
        if not any(item.kind.value in {"metric", "log"} for item in retained):
            raise GuardrailViolation(f"{label} requires observed metric or log evidence.")
        source_kinds = {item.kind for item in retained}
        if len(source_kinds) < 2:
            raise GuardrailViolation(
                f"{label} must be corroborated by distinct evidence kinds."
            )
        if hypothesis.cause_service is not None:
            if hypothesis.cause_service not in state.services or not any(
                item.service == hypothesis.cause_service and item.kind.value in {"metric", "log"}
                for item in retained
            ):
                raise GuardrailViolation(f"{label} service attribution requires cited service-local observations.")
            if hypothesis.cause_service != state.services[0]:
                # A model or restored checkpoint cannot invent an unrelated
                # service simply because its evidence IDs exist somewhere.
                from .cause_family import check_cause_family
                matches = [candidate for candidate in state.root_cause_candidates
                           if candidate.service == hypothesis.cause_service
                           and cited.intersection(candidate.local_evidence_ids)
                           and check_cause_family(hypothesis.cause, candidate.incident_type)["status"] == "recognized_family_match"]
                if len(matches) != 1:
                    raise GuardrailViolation(f"{label} service attribution lacks an unambiguous retained local-failure candidate.")
        path = hypothesis.propagation_path
        if path:
            if (not state.services or hypothesis.cause_service is None
                    or path[0] != state.services[0] or path[-1] != hypothesis.cause_service
                    or not 1 <= len(path) <= 3 or len(set(path)) != len(path)):
                raise GuardrailViolation(f"{label} has an invalid complaint-to-candidate path.")
            if len(path) > 1 and not any(
                candidate.service == hypothesis.cause_service
                and candidate.propagation_path == path
                and cited.intersection(candidate.local_evidence_ids)
                and cited.intersection(candidate.symptom_evidence_ids)
                for candidate in state.root_cause_candidates
            ):
                raise GuardrailViolation(f"{label} call path requires retained local-failure and complaint-symptom citations.")


def has_usable_observation(item: Evidence) -> bool:
    if item.score <= 0 or not item.observation.strip():
        return False
    if item.kind.value == "metric" and "series" in item.attributes:
        series = item.attributes["series"]
        if not isinstance(series, list):
            return False
        for point in series:
            if not isinstance(point, dict) or isinstance(point.get("value"), bool):
                continue
            try:
                if isfinite(float(point["value"])):
                    return True
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
        return False
    for key in ("events", "traces"):
        if key in item.attributes:
            events = item.attributes[key]
            if not isinstance(events, list):
                return False
            return any(
                isinstance(event, dict) and bool(
                    event.get("message", "").strip()
                    if isinstance(event.get("message"), str)
                    else event.get("trace_id")
                )
                for event in events
            )
    return True


def validate_execution(state: IncidentState) -> None:
    remediation = state.remediation
    if remediation is None:
        raise GuardrailViolation("No remediation was proposed.")
    if remediation.tool not in _DESTRUCTIVE_TOOLS:
        return
    if (not state.services
            or state.complaint_service not in (None, state.services[0])
            or state.diagnosis_service not in (None, state.services[0])
            or any(candidate.service != state.services[0] for candidate in state.root_cause_candidates)
            or any(hypothesis.cause_service not in (None, state.services[0]) for hypothesis in state.hypotheses)):
        raise GuardrailViolation("Cross-service hypotheses require investigation, not production mutation authority.")
    from .cross_service_rca import assess_diagnostic_coverage
    if not assess_diagnostic_coverage(state, now=datetime.now(UTC))["complete"]:
        raise GuardrailViolation("Unresolved downstream diagnostics require investigation before a destructive operation.")
    if (remediation.arguments.get("service") != state.services[0]
            or remediation.arguments.get("namespace") != state.environment):
        raise GuardrailViolation("A destructive operation must match the complaint service and incident namespace.")
    if not remediation.dry_run_passed:
        raise GuardrailViolation("A destructive tool requires a successful dry-run.")
    if state.approval_decision != "approve" or not state.approval_actor:
        raise GuardrailViolation("A destructive tool requires named human approval.")
