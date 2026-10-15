import { rootCauseCandidatesView } from "./root-cause-candidates.ts";

export const recoveryReasonLabels: Record<string, string> = {
  execution_unconfirmed: "Execution has not been confirmed",
  recovery_not_checked: "Recovery has not yet been checked",
  observation_unavailable: "Read-only recovery observations are unavailable",
  recovery_state_missing: "Execution recovery state is missing",
  simulation_or_unknown_backend: "Simulation or an unknown backend cannot verify live recovery",
  execution_acknowledgement_invalid: "Execution acknowledgement is missing or invalid",
  applied_target_identity_missing: "Applied generation or template identity is unavailable",
  approved_target_context_invalid: "Approved Kubernetes target context is invalid",
  approved_target_scope_mismatch: "Approved target scope does not match",
  deployment_observation_untrusted: "No trusted Deployment health observation is available",
  deployment_target_mismatch: "Observed Deployment identity differs from the approved target",
  deployment_applied_target_mismatch: "Observed generation or template differs from the applied target",
  deployment_rollout_incomplete: "Deployment rollout has not completed",
  deployment_observation_stale_or_invalid: "Deployment observation is stale or invalid",
  metric_evidence_budget_exceeded: "Metric evidence exceeded the bounded query budget",
  metric_observation_invalid: "A metric observation is invalid",
  metric_observation_untrusted: "Metric provenance or service scope is unverified",
  metric_evidence_identity_invalid: "Metric evidence identity is invalid or duplicated",
  metric_units_mismatch: "Metric units do not match the required SLI",
  metric_observation_stale_or_invalid: "Metric observation is stale or invalid",
  metric_discarded_samples: "The metric query discarded samples",
  metric_series_missing_or_invalid: "Metric series is missing or invalid",
  metric_series_budget_exceeded: "Metric series exceeded its bounded budget",
  metric_sample_invalid: "A metric sample is invalid",
  metric_sample_future: "A metric sample is timestamped in the future",
  metric_sample_conflict: "Metric samples contain conflicting values",
  metric_threshold_failed: "An observed SLI is still outside the lab recovery threshold",
  metric_samples_missing: "Required post-execution metric samples are missing",
  metric_window_insufficient: "The sampled healthy interval is too short or has gaps",
  metric_window_stale: "The latest metric sample is stale",
  metric_shared_window_insufficient: "Latency and error SLIs lack a shared sustained observation window",
  rollout_and_sli_recovery_observed_not_causal_proof: "Applied rollout and sustained SLI recovery were observed; causality is not proven",
};

type Recovery = {
  status: "verified" | "pending";
  checked_at: string;
  reason_codes: string[];
  metric_evidence_ids: string[];
  observation_window: [string, string] | null;
  execution_acknowledged_at: string | null;
  applied_generation: number | null;
  applied_template_sha256: string | null;
  simulation: boolean;
  deployment_observation: Record<string, unknown>;
};
type RecoveryView = {
  mode: "verified" | "pending" | "simulation" | "unknown";
  label: string;
  canCheck: boolean;
  observation: Recovery | null;
};
const keys = ["status", "checked_at", "reason_codes", "metric_evidence_ids", "observation_window",
  "execution_acknowledged_at", "applied_generation", "applied_template_sha256", "simulation"] as const;
const deploymentFields = ["source", "read_only", "marker_status", "observed_at", "service", "namespace", "cluster_context",
  "deployment_uid", "generation", "template_sha256", "observed_generation", "desired_replicas", "total_replicas",
  "updated_replicas", "ready_replicas", "available_replicas", "unavailable_replicas"];
const sha256 = /^[0-9a-f]{64}$/;
const servicePattern = /^[a-z0-9][a-z0-9-]{0,62}$/;
function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
}
function exact(value: unknown, pattern: RegExp): value is string {
  return typeof value === "string" && pattern.exec(value)?.[0] === value;
}
function boundedText(value: unknown, limit = 200): value is string {
  return typeof value === "string" && value.length > 0 && value.length <= limit;
}
function ids(value: unknown, max = 32): value is string[] {
  return Array.isArray(value) && value.length <= max && value.every(id => boundedText(id)) && new Set(value).size === value.length;
}
function utcTime(value: unknown): value is string {
  if (!exact(value, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)$/)) return false;
  const time = Date.parse(value);
  return Number.isFinite(time) && new Date(time).toISOString().slice(0, 19) === value.slice(0, 19);
}
function window(value: unknown): value is [string, string] {
  return Array.isArray(value) && value.length === 2 && value.every(utcTime) && Date.parse(value[0]) <= Date.parse(value[1]);
}
function normalizeRecovery(value: unknown): Recovery | null {
  const raw = record(value);
  if (!raw || keys.some(key => !Object.hasOwn(raw, key)) || (raw.status !== "verified" && raw.status !== "pending")
      || !utcTime(raw.checked_at) || !ids(raw.reason_codes) || !raw.reason_codes.every(code => Object.hasOwn(recoveryReasonLabels, code))
      || !ids(raw.metric_evidence_ids) || typeof raw.simulation !== "boolean"
      || (raw.observation_window !== null && !window(raw.observation_window))
      || (raw.execution_acknowledged_at !== null && (!utcTime(raw.execution_acknowledged_at)
        || Date.parse(raw.execution_acknowledged_at) > Date.parse(raw.checked_at)))
      || (raw.applied_generation !== null && (!Number.isSafeInteger(raw.applied_generation) || Number(raw.applied_generation) < 1))
      || (raw.applied_template_sha256 !== null && !exact(raw.applied_template_sha256, sha256))) return null;
  const deployment = record(raw.deployment_observation);
  return { ...Object.fromEntries(keys.map(key => [key, raw[key]])), deployment_observation:
    deployment ? Object.fromEntries(deploymentFields.filter(key => Object.hasOwn(deployment, key)).map(key => [key, deployment[key]])) : {} } as Recovery;
}
function retainedRollout(state: Record<string, unknown>, observation: Recovery): boolean {
  const remediation = record(state.remediation), arguments_ = record(remediation?.arguments);
  const context = record(arguments_?.target_context), deployment = observation.deployment_observation;
  if (!arguments_ || !context || context.tool !== remediation?.tool || context.service !== arguments_.service
      || context.namespace !== arguments_.namespace || !exact(context.service, servicePattern) || !exact(context.namespace, servicePattern)
      || !exact(context.cluster_context, sha256) || !boundedText(context.deployment_uid, 128) || /\s/.test(context.deployment_uid)
      || deployment.source !== "kubernetes_deployment_health" || deployment.read_only !== true || deployment.marker_status !== "marker_matches"
      || ["service", "namespace", "cluster_context", "deployment_uid"].some(field => deployment[field] !== context[field])
      || (state.environment !== undefined && state.environment !== context.namespace)
      || deployment.generation !== observation.applied_generation || deployment.template_sha256 !== observation.applied_template_sha256
      || !Number.isSafeInteger(deployment.observed_generation) || Number(deployment.observed_generation) < observation.applied_generation!
      || !Number.isSafeInteger(deployment.desired_replicas) || Number(deployment.desired_replicas) < 1
      || ["total_replicas", "updated_replicas", "ready_replicas", "available_replicas"].some(field =>
        !Number.isSafeInteger(deployment[field]) || deployment[field] !== deployment.desired_replicas)
      || deployment.unavailable_replicas !== 0 || !utcTime(deployment.observed_at)
      || Date.parse(deployment.observed_at) <= Date.parse(observation.execution_acknowledged_at!)
      || Date.parse(deployment.observed_at) > Date.parse(observation.checked_at)
      || Date.parse(observation.checked_at) - Date.parse(deployment.observed_at) > 30_000) return false;
  return true;
}
function retainedMetrics(state: Record<string, unknown>, observation: Recovery, verified: boolean): boolean {
  if (!Array.isArray(state.evidence)) return false;
  const arguments_ = record(record(state.remediation)?.arguments);
  if (!exact(arguments_?.service, servicePattern)) return false;
  const queries = new Set<string>();
  const samples = new Map<string, Map<number, number>>();
  for (const id of observation.metric_evidence_ids) {
    const matches = state.evidence.map(record).filter(item => item?.id === id);
    const item = matches[0], attributes = record(item?.attributes);
    if (matches.length !== 1 || !item || item.kind !== "metric" || item.service !== arguments_.service) return false;
    if (verified) {
      if (item.source !== "prometheus-via-telemetry-gateway" || attributes?.data_origin !== "prometheus_api"
          || (item.query !== "request_latency_p95" && item.query !== "request_error_rate")
          || attributes.unit !== (item.query === "request_latency_p95" ? "milliseconds" : "ratio")
          || !utcTime(item.observed_at) || Date.parse(item.observed_at) <= Date.parse(observation.execution_acknowledged_at!)
          || Date.parse(item.observed_at) > Date.parse(observation.checked_at)
          || Date.parse(observation.checked_at) - Date.parse(item.observed_at) > 30_000
          || (attributes.discarded_samples !== undefined && attributes.discarded_samples !== 0)
          || !Array.isArray(attributes.series) || attributes.series.length > 2048) return false;
      const query = item.query, points = samples.get(query) ?? new Map<number, number>();
      for (const raw of attributes.series) {
        const point = record(raw);
        if (!point || !utcTime(point.timestamp) || typeof point.value !== "number" || !Number.isFinite(point.value)
            || point.value < 0 || (query === "request_error_rate" && point.value > 1)) return false;
        const time = Date.parse(point.timestamp);
        if (time > Date.parse(observation.checked_at)) return false;
        if (time <= Date.parse(observation.execution_acknowledged_at!)
            || time < Math.max(Date.parse(observation.execution_acknowledged_at!), Date.parse(observation.checked_at) - 60_000)) continue;
        // These are the project's current fault-lab thresholds, not universal SLOs.
        if (point.value > (query === "request_latency_p95" ? 1000 : 0.05)
            || (points.has(time) && points.get(time) !== point.value)) return false;
        points.set(time, point.value);
      }
      samples.set(query, points);
      queries.add(item.query);
    }
  }
  if (!verified) return true;
  if (queries.size !== 2 || !observation.observation_window) return false;
  const [start, end] = observation.observation_window.map(Date.parse);
  return [...samples.values()].every(series => {
    const all = [...series.keys()].sort((left, right) => left - right);
    const shared = all.filter(time => start <= time && time <= end);
    return all.length >= 3 && shared.length >= 3 && all[0] <= start && all[all.length - 1] >= end
      && all[all.length - 1] - all[0] >= 30_000
      && Date.parse(observation.checked_at) - all[all.length - 1] <= 30_000
      && all.every((time, index) => index === 0 || time - all[index - 1] <= 30_000);
  });
}

/** Observed recovery is distinct from execution acknowledgement and causal proof. */
export function recoveryVerificationView(incident: unknown): RecoveryView {
  const unknown: RecoveryView = { mode: "unknown", label: "Recovery not verified — retained observation missing or inconsistent", canCheck: false, observation: null };
  const state = record(incident), observation = normalizeRecovery(state?.recovery);
  if (!state || !observation || !retainedMetrics(state, observation, false)) return unknown;
  const remediation = record(state.remediation);
  if (!remediation || (remediation.tool !== "restart_deployment" && remediation.tool !== "rollback_release")) return unknown;
  if (observation.simulation) return remediation.preflight_mode === "simulation"
    ? { mode: "simulation", label: "Simulation — live recovery is not verified", canCheck: false, observation } : unknown;
  if (remediation.preflight_mode !== "kubernetes-server-dry-run") return unknown;
  const acknowledged = observation.execution_acknowledged_at !== null && observation.applied_generation !== null
    && observation.applied_template_sha256 !== null;
  if (observation.status === "pending" && state.status === "remediation_pending") {
    return { mode: "pending", label: "Execution and recovery are separate — recovery verification pending", canCheck: acknowledged, observation };
  }
  const sampled = observation.observation_window;
  if (observation.status !== "verified" || state.status !== "resolved" || !acknowledged || !sampled
      || Date.parse(sampled[0]) < Date.parse(observation.execution_acknowledged_at!)
      || Date.parse(sampled[1]) > Date.parse(observation.checked_at)
      || Date.parse(sampled[1]) - Date.parse(sampled[0]) < 30_000
      || observation.reason_codes.length !== 1
      || observation.reason_codes[0] !== "rollout_and_sli_recovery_observed_not_causal_proof"
      || !retainedRollout(state, observation)
      || !retainedMetrics(state, observation, true)) return unknown;
  return { mode: "verified", label: "Applied rollout and sustained SLI recovery observed", canCheck: false, observation };
}

export function recoveryCheckEndpoint(url: string | undefined, incidentId: string): string | null {
  if (!url || !boundedText(incidentId)) return null;
  try {
    const base = new URL(url);
    if (!["http:", "https:"].includes(base.protocol) || base.username || base.password || base.search || base.hash) return null;
    return `${base.href.replace(/\/$/, "")}/v1/incidents/${encodeURIComponent(incidentId)}/recovery-check`;
  } catch { return null; }
}
export function validRecoveryOperatorToken(token: unknown): token is string {
  return typeof token === "string" && token.length <= 8192 && exact(token, /^[A-Za-z0-9._~+/-]+={0,2}$/);
}
function validIncidentUpdate(value: unknown, incidentId: string): Record<string, unknown> | null {
  const state = record(value);
  if (!state || state.incident_id !== incidentId || !boundedText(state.trace_id) || !boundedText(state.question, 2000)
      || !boundedText(state.current_step) || !utcTime(state.created_at) || !Array.isArray(state.services)
      || !state.services.length || state.services.length > 64 || !state.services.every(service => exact(service, servicePattern))
      || (state.severity !== null && !boundedText(state.severity)) || (state.incident_type !== null && !boundedText(state.incident_type))
      || (state.anomalous_window !== null && !window(state.anomalous_window))
      || !Array.isArray(state.hypotheses) || state.hypotheses.length > 32 || !state.hypotheses.every(value => {
        const item = record(value);
        return item && boundedText(item.cause, 16000) && typeof item.confidence === "number" && Number.isFinite(item.confidence)
          && item.confidence >= 0 && item.confidence <= 1 && ids(item.evidence_ids, 100);
      }) || !Array.isArray(state.evidence) || state.evidence.length > 2048 || !state.evidence.every(value => {
        const item = record(value);
        return item && boundedText(item.id) && boundedText(item.kind) && boundedText(item.source, 2000)
          && boundedText(item.title, 16000) && boundedText(item.observation, 64000) && utcTime(item.observed_at)
          && exact(item.service, servicePattern) && (item.query === null || boundedText(item.query, 64000)) && record(item.attributes);
      }) || !Array.isArray(state.trace) || state.trace.length > 4096 || !state.trace.every(value => {
        const item = record(value);
        return item && Number.isSafeInteger(item.sequence) && Number(item.sequence) >= 0 && boundedText(item.step)
          && boundedText(item.event) && (item.duration_ms === null || (typeof item.duration_ms === "number"
            && Number.isFinite(item.duration_ms) && item.duration_ms >= 0)) && record(item.payload);
      })) return null;
  const remediation = record(state.remediation);
  if (!remediation || (remediation.tool !== "restart_deployment" && remediation.tool !== "rollback_release")
      || !boundedText(remediation.title, 16000) || !boundedText(remediation.command_preview, 16000)
      || !boundedText(remediation.expected_result, 16000) || typeof remediation.requires_approval !== "boolean"
      || typeof remediation.dry_run_passed !== "boolean" || !record(remediation.arguments)) return null;
  if (state.service_impacts !== undefined && (!Array.isArray(state.service_impacts) || !state.service_impacts.every(value => {
    const item = record(value);
    return item && exact(item.service, servicePattern) && boundedText(item.status) && ids(item.evidence_ids, 100)
      && (item.overlaps_primary_window === null || typeof item.overlaps_primary_window === "boolean");
  }))) return null;
  if (state.service_topology !== undefined && state.service_topology !== null && !record(state.service_topology)) return null;
  if (state.candidate_incident_types !== undefined && (!Array.isArray(state.candidate_incident_types)
    || !state.candidate_incident_types.every(value => boundedText(value)))) return null;
  const view = recoveryVerificationView(state);
  if (!["verified", "pending"].includes(view.mode) || !view.observation) return null;
  // Only fields consumed by the console are returned; arbitrary top-level/provider notes are not injected.
  const fields = ["incident_id", "trace_id", "question", "status", "current_step", "environment", "incident_type", "candidate_incident_types",
    "service_impacts", "service_topology", "severity", "anomalous_window", "created_at", "services", "evidence", "hypotheses", "remediation", "trace"];
  const rca = rootCauseCandidatesView(state);
  const rcaFields = rca.available ? {
    complaint_service: rca.complaintService, diagnosis_service: rca.diagnosisService,
    diagnostic_excluded_evidence_ids: state.diagnostic_excluded_evidence_ids ?? [],
    root_cause_candidates: rca.candidates.map(candidate => ({ rank: candidate.rank, service: candidate.service,
      incident_type: candidate.incidentType, local_evidence_ids: candidate.localEvidenceIds, symptom_evidence_ids: candidate.symptomEvidenceIds,
      propagation_path: candidate.path, path_basis: candidate.pathBasis, temporal_overlap: candidate.temporalOverlap,
      causal_direction_verified: false, validation_notes: [] })),
    service_topologies: (state.service_topologies as unknown[]).map(value => {
      const topology = record(value)!;
      const fields = ["service", "source", "topology_kind", "catalog_status", "edges", "dependencies"];
      const observed = record(topology.observed);
      const observedFields = ["status", "source", "calls", "start", "end", "coverage", "traces_read", "traces_failed", "calls_omitted", "calls_discarded"];
      const edges = (topology.edges as unknown[]).map(record).filter(edge => edge && typeof edge.source === "string" && typeof edge.target === "string")
        .map(edge => ({ source: edge!.source, target: edge!.target }));
      const calls = Array.isArray(observed?.calls) ? observed.calls.map(record).filter(Boolean).map(call => Object.fromEntries(
        ["source", "target", "trace_id", "client_span_id", "server_span_id", "observed_at"].filter(field => Object.hasOwn(call!, field)).map(field => [field, call![field]]))) : [];
      return { ...Object.fromEntries(fields.filter(field => Object.hasOwn(topology, field)).map(field => [field, topology[field]])), edges,
        ...(observed ? { observed: { ...Object.fromEntries(observedFields.filter(field => Object.hasOwn(observed, field)).map(field => [field, observed[field]])), calls } } : {}) };
    }),
  } : {};
  // Invalid RCA metadata cannot hide a valid recovery observation; omitting it
  // makes the read-only candidate panel unavailable instead of confirming cause.
  return { ...Object.fromEntries(fields.filter(field => Object.hasOwn(state, field)).map(field => [field, state[field]])),
    ...rcaFields, recovery: view.observation };
}

export async function checkIncidentRecovery<T extends { incident_id: string; status: string }>(
  url: string, incident: T, operatorToken: string, transport: typeof fetch = fetch,
): Promise<{ incident: T | null; error: string | null }> {
  const state = record(incident);
  const endpoint = boundedText(state?.incident_id) ? recoveryCheckEndpoint(url, state.incident_id) : null;
  if (!endpoint || !validRecoveryOperatorToken(operatorToken) || !recoveryVerificationView(incident).canCheck) {
    return { incident: null, error: "A real pending recovery state and authenticated backend connection are required. No check was submitted." };
  }
  try {
    const response = await transport(endpoint, { method: "POST", headers: { Authorization: `Bearer ${operatorToken}` }, redirect: "error",
      cache: "no-store", signal: AbortSignal.timeout(30_000) });
    if (response.status === 401 || response.status === 403) return { incident: null, error: "Operator access is required. No remediation was retried." };
    if (response.status === 409) return { incident: null, error: "No matching recovery checkpoint is available. Review retained state; no remediation was retried." };
    if (!response.ok) throw new Error("Recovery check unavailable");
    const update = validIncidentUpdate(await response.json(), incident.incident_id);
    if (!update) throw new Error("Recovery response invalid");
    return { incident: update as T, error: null };
  } catch {
    return { incident: null, error: "Recovery verification is unavailable or returned inconsistent data. Retained incident state is unchanged; no remediation was retried." };
  }
}
