export type ObservedCall = {
  source: string;
  target: string;
  trace_id: string;
  client_span_id: string;
  server_span_id: string;
  observed_at: string;
};

export type ObservedTopologyView = {
  status: "available" | "unavailable" | "not_supported" | "not_requested";
  label: string;
  calls: ObservedCall[];
  window: string | null;
  counts: string | null;
};

function unavailable(): ObservedTopologyView {
  return { status: "unavailable", label: "Trace topology unavailable or invalid · no absence claim", calls: [], window: null, counts: null };
}

function timestamp(raw: unknown): number | null {
  if (typeof raw !== "string" || !/(Z|[+-]\d{2}:\d{2})$/.test(raw)) return null;
  const value = Date.parse(raw);
  return Number.isFinite(value) ? value : null;
}

function record(raw: unknown): Record<string, unknown> | null {
  return raw !== null && typeof raw === "object" && !Array.isArray(raw) ? raw as Record<string, unknown> : null;
}

export function observedTopologyProjection(services: string[], topology: unknown): ObservedTopologyView {
  const owner = record(topology);
  if (!owner || owner.observed == null) return { status: "not_requested", label: "Trace topology not requested for this incident", calls: [], window: null, counts: null };
  if (owner.service !== services[0]) return unavailable();
  const raw = record(owner.observed);
  if (!raw) return unavailable();
  if (raw.status === "not_requested" || raw.status === "not_supported") return {
    status: raw.status, label: raw.status === "not_requested" ? "Trace topology not requested for this incident" : "Trace topology not supported by this adapter",
    calls: [], window: null, counts: null,
  };
  if (raw.status !== "available" || raw.source !== "tempo_client_server_parentage") return unavailable();
  if (!Array.isArray(raw.calls) || raw.calls.length > 64) return unavailable();
  const { traces_read: read, traces_failed: failed, calls_omitted: omitted, calls_discarded: discarded } = raw;
  const count = (value: unknown): value is number => typeof value === "number" && Number.isInteger(value) && value >= 0 && value <= 16384;
  if (!count(read) || !count(failed) || !count(omitted) || !count(discarded) || read + failed > 8 || discarded > 64) return unavailable();
  if (!["sampled_not_exhaustive", "partial_sample_not_exhaustive"].includes(String(raw.coverage))
    || (failed > 0) !== (raw.coverage === "partial_sample_not_exhaustive")) return unavailable();
  const start = timestamp(raw.start), end = timestamp(raw.end);
  if (start === null || end === null || end <= start || end - start > 30 * 60 * 1000 || (raw.calls.length > 0 && read === 0)) return unavailable();
  // Historical incident windows stay readable; never compare them with today's clock.
  const identities = new Map<string, number>();
  for (const value of raw.calls) {
    const call = record(value);
    if (call && [call.trace_id, call.client_span_id, call.server_span_id].every(id => typeof id === "string")) {
      const key = `${call.trace_id}:${call.client_span_id}:${call.server_span_id}`;
      identities.set(key, (identities.get(key) ?? 0) + 1);
    }
  }
  const calls: ObservedCall[] = [];
  for (const value of raw.calls) {
    const call = record(value);
    if (!call) continue;
    const validService = (name: unknown): name is string => typeof name === "string" && /^[a-z0-9][a-z0-9-]{1,79}$/.test(name);
    if (!validService(call.source) || !validService(call.target) || call.source === call.target
      || ![call.source, call.target].includes(services[0]) || !services.includes(call.source) || !services.includes(call.target)) continue;
    const validID = (id: unknown, length: number): id is string => typeof id === "string" && new RegExp(`^[0-9a-f]{${length}}$`).test(id) && /[1-9a-f]/.test(id);
    if (!validID(call.trace_id, 32) || !validID(call.client_span_id, 16) || !validID(call.server_span_id, 16) || call.client_span_id === call.server_span_id) continue;
    const at = timestamp(call.observed_at);
    if (at === null || at < start || at > end || identities.get(`${call.trace_id}:${call.client_span_id}:${call.server_span_id}`) !== 1) continue;
    calls.push({ source: call.source, target: call.target, trace_id: call.trace_id,
      client_span_id: call.client_span_id, server_span_id: call.server_span_id, observed_at: new Date(at).toISOString() });
  }
  if (new Set(calls.map(call => call.trace_id)).size > read) return unavailable();
  const hidden = raw.calls.length - calls.length;
  return { status: "available", calls,
    label: failed > 0 ? "Partial trace sample · not exhaustive" : "Trace sample · not exhaustive",
    window: `${new Date(start).toISOString()} — ${new Date(end).toISOString()}`,
    counts: `${read} traces read · ${failed} failed · ${omitted} call pairs omitted · ${discarded + hidden} discarded`,
  };
}

export function observedDirection(primary: string, service: string, calls: ObservedCall[]): string {
  const upstream = calls.some(call => call.source === service && call.target === primary);
  const downstream = calls.some(call => call.source === primary && call.target === service);
  return upstream && downstream ? "Observed bidirectional calls (sample)"
    : upstream ? "Observed upstream caller (sample)" : downstream ? "Observed downstream dependency (sample)" : "No retained call pair · sample is not exhaustive";
}
