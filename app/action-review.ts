import { resolveCitations } from "./evidence-citations.ts";

type Evidence = { id: string; kind: string; service?: string };
type Hypothesis = { id?: string; rank?: number; cause: string; evidence_ids: string[] };
type Trace = { sequence: number; event: string; payload: Record<string, unknown> };
type ReviewInput = { services: string[]; incident_type: string | null; evidence: Evidence[];
  hypotheses: Hypothesis[]; trace: Trace[]; remediation: { tool: string } | null };

const familyLabels: Record<string, string> = {
  recognized_family_match: "Known-family phrase match only — not semantic validation",
  explicit_family_mismatch: "Known-family contradiction detected",
  unrecognized: "Cause wording unrecognized — not validated",
  unresolved_negation: "Negated cause wording unresolved — not validated",
  multiple_families_unresolved: "Multiple cause families unresolved — not validated",
};
function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
}
function ids(value: unknown): string[] | null {
  return Array.isArray(value) && value.length <= 100 && value.every(id => typeof id === "string" && id.length <= 128)
    ? [...new Set(value)] : null;
}

export function actionReviewView(incident: ReviewInput) {
  const fallback = { signalLabel: "No current, scoped signal assessment retained", familyLabel: "Cause-family assessment unavailable",
    evidenceIds: [] as string[], scoped: false };
  const checks = incident.trace.filter(item => item.event === "action_signal_applicability_checked");
  if (!checks.length || !incident.services.length) return fallback;
  const ordered = [...checks].sort((a, b) => b.sequence - a.sequence);
  const latest = ordered[0];
  if (!Number.isSafeInteger(latest.sequence) || latest.sequence < 0
      || ordered.filter(item => item.sequence === latest.sequence).length !== 1) return fallback;
  const payload = record(latest.payload);
  const leading = incident.hypotheses.filter(item => item.rank === 1);
  const cited = ids(payload?.leading_evidence_ids), signalIds = ids(payload?.cited_primary_signal_ids);
  if (!payload || leading.length !== 1 || !cited || !signalIds
      || payload.leading_hypothesis_id !== leading[0].id || typeof leading[0].id !== "string"
      || payload.leading_cause !== leading[0].cause || payload.primary_service !== incident.services[0]
      || payload.profile !== incident.incident_type
      || [...new Set(leading[0].evidence_ids)].sort().join("\0") !== [...cited].sort().join("\0")
      || signalIds.some(id => !cited.includes(id)) || typeof payload.allowed !== "boolean"
      || (payload.allowed && !signalIds.length)
      || !["restart_deployment", "rollback_release"].includes(String(payload.tool))
      || (incident.remediation?.tool !== "none" && payload.tool !== incident.remediation?.tool)) return fallback;
  const resolved = resolveCitations(incident.evidence, signalIds);
  if (resolved.missing.length || resolved.ambiguous.length || resolved.cited.some(item =>
      item.service !== incident.services[0] || !["metric", "log"].includes(item.kind))) return fallback;
  const family = record(payload.cause_family_check);
  const familyLabel = typeof family?.status === "string" && Object.hasOwn(familyLabels, family.status)
    ? familyLabels[family.status] : fallback.familyLabel;
  return { scoped: true, evidenceIds: signalIds, familyLabel,
    signalLabel: payload.allowed === false ? "Proposal blocked by the applicability check"
      : payload.simulation_only_summary_compatibility === true ? "Offline summary compatibility only — not production verification"
      : "Retained signal/profile check passed — efficacy not established" };
}
