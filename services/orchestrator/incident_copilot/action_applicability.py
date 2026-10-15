"""Bounded signal/action consistency, not semantic entailment or causal proof."""
from datetime import UTC, datetime

from .action_signal_time import current_action_signals
from .guardrails import GuardrailViolation, validate_grounding
from .incident_profiles import PROFILES, infer_profiles_from_evidence
from .models import IncidentState
from .cause_family import check_cause_family
from .cross_service_rca import assess_diagnostic_coverage


def assess_action_applicability(incident: IncidentState, tool: str, *, allow_simulated_summaries: bool = False) -> dict:
    result = {"allowed": False, "tool": tool, "profile": incident.incident_type,
              "cited_primary_signal_ids": [], "matched_profiles": [],
              "semantic_entailment_verified": False, "causation_verified": False}
    profile = PROFILES.get(incident.incident_type)
    if profile is None or profile.action != tool or tool not in {"restart_deployment", "rollback_release"}:
        return {**result, "reason": "Action does not match the classified failure profile."}
    coverage = assess_diagnostic_coverage(incident, now=datetime.now(UTC))
    result["diagnostic_coverage"] = coverage
    if not coverage["complete"]:
        return {**result, "reason": "Downstream diagnostics are unresolved or unavailable; omitted evidence is not healthy evidence."}
    try:
        validate_grounding(incident)
    except GuardrailViolation:
        return {**result, "reason": "Retained hypothesis citations no longer pass grounding checks."}
    leading = [item for item in incident.hypotheses if item.rank == 1]
    if len(leading) != 1 or leading[0].conflicting_evidence_ids:
        return {**result, "reason": "Leading hypothesis is ambiguous or retains unresolved conflicting citations."}
    result.update(leading_hypothesis_id=leading[0].id, leading_cause=leading[0].cause,
                  leading_evidence_ids=list(leading[0].evidence_ids),
                  primary_service=incident.services[0] if incident.services else None)
    family = check_cause_family(leading[0].cause, profile.key)
    result["cause_family_check"] = family
    if family["explicit_mismatch"]:
        return {**result, "reason": "Leading cause explicitly names a different known failure family than the action profile."}
    if family["status"] != "recognized_family_match":
        return {**result, "reason": "Leading cause family is unresolved; investigate or refresh diagnosis before proposing a destructive action."}
    cited = set(leading[0].evidence_ids)
    primary = incident.services[0] if incident.services else None
    signals = [item for item in incident.evidence if item.id in cited
               and item.service == primary and item.kind.value in {"metric", "log"}]
    result.update(cited_primary_signal_ids=[item.id for item in signals])
    if allow_simulated_summaries and signals and not any(
            "series" in item.attributes or "events" in item.attributes for item in signals):
        return {**result, "allowed": True, "simulation_only_summary_compatibility": True,
                "reason": "Explicit offline provider summary fixture; not observed signal or production action validation."}
    now = datetime.now(UTC)
    current, time_check = current_action_signals(signals, now=now)
    matches = [item.key for item in infer_profiles_from_evidence(current, now=now)]
    result.update(matched_profiles=matches, signal_time_check=time_check,
                  temporal_eligible_signal_ids=[item.id for item in current])
    if matches != [profile.key]:
        return {**result, "reason": "Current cited primary-service signals do not uniquely reproduce the selected failure profile."}
    return {**result, "allowed": True,
            "reason": "Cited primary-service signals match the rule-selected action profile; causal efficacy remains unverified."}


def validate_action_applicability(incident: IncidentState, *, allow_simulated_summaries: bool = False) -> None:
    if incident.remediation is None or incident.remediation.tool not in {"restart_deployment", "rollback_release"}:
        return
    assessment = assess_action_applicability(incident, incident.remediation.tool, allow_simulated_summaries=allow_simulated_summaries)
    if not assessment["allowed"]:
        raise GuardrailViolation("Action applicability check failed: " + assessment["reason"])
