type Evidence = { id: string; kind: string; service?: string; attributes: Record<string, unknown> };
type Status = "available" | "empty" | "unavailable" | "not_requested" | "unknown";
export const eventStatusLabels: Record<Status, string> = {
  available: "Event observations retained",
  empty: "No accepted events in the bounded query",
  unavailable: "Event query unavailable",
  not_requested: "Event discovery not requested",
  unknown: "Event discovery status unknown",
};
const reasons = new Set(["ScalingReplicaSet", "NewReplicaSetCreated", "ProgressDeadlineExceeded", "FailedCreate", "ReplicaSetUpdated", "DeploymentRollback"]);

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function instant(value: unknown): string | null {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d{2}:\d{2})$/.test(value)) return null;
  const time = Date.parse(value);
  return Number.isFinite(time) && time >= 0 ? new Date(time).toISOString() : null;
}

export function deploymentEventView(services: string[], evidence: Evidence[], window: [string, string] | null) {
  const contexts = evidence.filter(item => services.length > 0 && item.kind === "deployment" && item.service === services[0]
    && record(item.attributes) !== null && "event_timeline" in item.attributes);
  const context = contexts.length === 1 ? contexts[0] : null;
  const timeline = record(context?.attributes.event_timeline);
  const rawStatus = timeline?.status;
  const status: Status = context?.attributes.data_origin === "kubernetes_api"
    && typeof rawStatus === "string" && Object.hasOwn(eventStatusLabels, rawStatus)
    ? rawStatus as Status : "unknown";
  const start = instant(window?.[0]), end = instant(window?.[1]);
  const validWindow = start && end && Date.parse(start) <= Date.parse(end) ? [start, end] : null;
  const candidates = evidence.filter(item => item.kind === "deployment_event" && item.service === services[0]);
  const ids = new Map<string, number>();
  for (const item of evidence) ids.set(item.id, (ids.get(item.id) ?? 0) + 1);
  const rows: Array<{ evidenceId: string; reason: string; first: string | null; last: string | null; relation: string }> = [];
  if (status === "available" && context?.attributes.data_origin === "kubernetes_api"
    && typeof context.attributes.namespace === "string" && typeof context.attributes.deployment_uid === "string"
    && candidates.length <= 100) {
    const eventIds = new Set<string>();
    for (const item of candidates) {
      const attrs = record(item.attributes);
      if (!attrs) continue;
      const first = instant(attrs.first_observed_at), last = instant(attrs.last_observed_at);
      if (ids.get(item.id) !== 1 || attrs.data_origin !== "kubernetes_api"
        || attrs.service !== services[0] || attrs.namespace !== context.attributes.namespace
        || typeof attrs.deployment_uid !== "string" || attrs.deployment_uid !== context.attributes.deployment_uid
        || typeof attrs.event_uid !== "string" || !/^[A-Za-z0-9-]{1,128}$/.test(attrs.event_uid)
        || typeof attrs.reason !== "string" || !reasons.has(attrs.reason)
        || attrs.timestamp_semantics !== "event_observation_not_rollout_time"
        || (attrs.first_observed_at != null && !first) || (attrs.last_observed_at != null && !last)
        || (!first && !last) || (first && last && Date.parse(first) > Date.parse(last))) continue;
      // A duplicate Event UID makes the projection ambiguous: do not pick one.
      if (eventIds.has(attrs.event_uid)) return { status: "unknown" as Status, window: validWindow, rows: [], omitted: candidates.length };
      eventIds.add(attrs.event_uid);
      let relation = "Anomaly window unavailable";
      if (validWindow) {
        relation = !first || !last ? "Event interval incomplete"
          : Date.parse(last) < Date.parse(validWindow[0]) ? "Before sampled anomaly window"
          : Date.parse(first) > Date.parse(validWindow[1]) ? "After sampled anomaly window"
          : "Interval overlaps sampled anomaly window";
      }
      rows.push({ evidenceId: item.id, reason: attrs.reason, first, last, relation });
    }
  }
  rows.sort((a, b) => (a.last ?? a.first ?? "").localeCompare(b.last ?? b.first ?? ""));
  return { status, window: validWindow, rows, omitted: candidates.length - rows.length };
}
