import { observedTopologyProjection } from "./observed-topology.ts";

const familyLabels: Record<string, string> = {
  database_connection_pool_exhaustion: "Database connection pool exhaustion",
  downstream_timeout: "Downstream dependency timeout",
  cpu_saturation: "CPU saturation",
  memory_leak: "Memory leak",
  deployment_regression: "Deployment regression",
  kafka_consumer_lag: "Kafka consumer lag",
  retry_storm: "Retry amplification",
  cache_penetration: "Cache penetration",
};
const metrics = new Set(["request_latency_p95", "request_error_rate", "db_pool_active_ratio", "cpu_utilization",
  "memory_working_set", "kafka_consumer_lag", "retry_rate", "cache_miss_rate"]);
const metricUnits: Record<string, string> = {
  request_latency_p95: "milliseconds", request_error_rate: "ratio", db_pool_active_ratio: "ratio", cpu_utilization: "ratio",
  memory_working_set: "bytes", kafka_consumer_lag: "messages", retry_rate: "retries_per_second", cache_miss_rate: "misses_per_second",
};
const thresholds: Record<string, number> = {
  request_latency_p95: 1000, request_error_rate: 0.05, db_pool_active_ratio: 0.9, cpu_utilization: 0.8,
  kafka_consumer_lag: 100, retry_rate: 1, cache_miss_rate: 1,
};
const profileMetrics: Record<string, string> = {
  database_connection_pool_exhaustion: "db_pool_active_ratio", cpu_saturation: "cpu_utilization", memory_leak: "memory_working_set",
  kafka_consumer_lag: "kafka_consumer_lag", retry_storm: "retry_rate", cache_penetration: "cache_miss_rate",
};
const profileLogs: Record<string, string> = {
  database_connection_pool_exhaustion: "acquisition timed out", deployment_regression: "invalid ledger response shape",
  downstream_timeout: "downstream request timed out",
};
const pathLabels: Record<string, string> = {
  primary: "Complaint-service evidence; no cross-service call path",
  observed_trace: "Sampled CLIENT → SERVER calls; not verified failure propagation",
  catalog: "Retained catalog calls; not trace-verified failure propagation",
  mixed: "Catalog and sampled-call support; not verified failure propagation",
};
type Candidate = {
  rank: number;
  service: string;
  incidentType: string;
  familyLabel: string;
  localEvidenceIds: string[];
  symptomEvidenceIds: string[];
  path: string[];
  pathBasis: string;
  pathLabel: string;
  temporalLabel: string;
  temporalOverlap: boolean | null;
};
type CandidateView = {
  available: boolean;
  complaintService: string | null;
  diagnosisService: string | null;
  candidates: Candidate[];
};
type Signal = { id: string; times: number[]; query: string | null; values: number[]; messages: string[] };
type Point = { time: number; value: number; ids: Set<string> };
type SignalSupport = { ids: Set<string>; windows: [number, number][] };
const unavailable: CandidateView = { available: false, complaintService: null, diagnosisService: null, candidates: [] };
const freshness = 5 * 60 * 1000;
function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
}
function exact(value: unknown, pattern: RegExp): value is string {
  return typeof value === "string" && pattern.exec(value)?.[0] === value;
}
function service(value: unknown): value is string {
  return exact(value, /^[a-z0-9][a-z0-9-]{1,79}$/);
}
function ids(value: unknown, minimum = 0): value is string[] {
  return Array.isArray(value) && value.length >= minimum && value.length <= 32
    && value.every(id => typeof id === "string" && id.length > 0 && id.length <= 200) && new Set(value).size === value.length;
}
function timestamp(value: unknown): number | null {
  if (!exact(value, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/)) return null;
  const parts = value.slice(0, 19).split(/[-T:]/).map(Number);
  const calendar = new Date(Date.UTC(parts[0], parts[1] - 1, parts[2], parts[3], parts[4], parts[5]));
  if ([calendar.getUTCFullYear(), calendar.getUTCMonth() + 1, calendar.getUTCDate(), calendar.getUTCHours(), calendar.getUTCMinutes(), calendar.getUTCSeconds()]
    .some((part, index) => part !== parts[index])) return null;
  const time = Date.parse(value);
  return Number.isFinite(time) ? time : null;
}
function current(time: number | null, now: number): time is number {
  return time !== null && now - freshness <= time && time <= now;
}
function preciseTime(value: unknown): string | null {
  const time = timestamp(value);
  if (time === null || typeof value !== "string") return null;
  // Date.parse truncates microseconds. Preserve them when comparing one
  // physical span identity across independently queried topology envelopes.
  const fraction = /\.(\d{1,6})(?:Z|[+-]\d{2}:\d{2})$/.exec(value)?.[1] ?? "";
  return String(BigInt(time) * BigInt(1000) + BigInt(fraction.padEnd(6, "0").slice(3)));
}
function currentSampleTime(value: unknown, now: number): number | null {
  const precise = preciseTime(value), time = precise === null ? NaN : Number(precise);
  return Number.isSafeInteger(time) && (now - freshness) * 1000 <= time && time <= now * 1000 ? time : null;
}
function usableSignal(raw: unknown, expectedService: string, now: number): Signal | null {
  const item = record(raw), attributes = record(item?.attributes);
  if (!item || typeof item.id !== "string" || !item.id.length || item.id.length > 200 || item.service !== expectedService || !attributes || typeof item.score !== "number"
      || !Number.isFinite(item.score) || item.score <= 0 || item.score > 1 || typeof item.observation !== "string"
      || !item.observation.trim() || item.observation.length > 64000 || !current(timestamp(item.observed_at), now)) return null;
  if (item.kind === "metric") {
    if (typeof item.query !== "string" || !metrics.has(item.query) || !Array.isArray(attributes.series)
        || attributes.series.length > 2048 || (Object.hasOwn(attributes, "unit") && attributes.unit !== metricUnits[item.query])) return null;
    const samples = new Map<number, number>();
    for (const raw of attributes.series) {
      const point = record(raw), parsed = timestamp(point?.timestamp);
      if (!point || parsed === null || parsed > now || typeof point.value !== "number" || !Number.isFinite(point.value)
          || point.value < 0 || (["request_error_rate", "cpu_utilization", "db_pool_active_ratio"].includes(item.query) && point.value > 1)) return null;
      const time = currentSampleTime(point.timestamp, now);
      if (time === null) continue;
      if (samples.has(time) && samples.get(time) !== point.value) return null;
      samples.set(time, point.value);
    }
    const times = [...samples.keys()].sort((left, right) => left - right);
    return times.length ? { id: item.id, times, values: times.map(time => samples.get(time)!), query: item.query, messages: [] } : null;
  }
  if (item.kind === "log") {
    if (!Array.isArray(attributes.events) || attributes.events.length > 2048) return null;
    const times = [], messages = [];
    for (const raw of attributes.events) {
      const event = record(raw), parsed = timestamp(event?.timestamp);
      if (!event || parsed === null || parsed > now || typeof event.message !== "string" || !event.message.length
          || event.message.length > 64000) return null;
      const time = currentSampleTime(event.timestamp, now);
      if (time !== null) { times.push(time); messages.push(event.message.toLowerCase()); }
    }
    return times.length ? { id: item.id, times, values: [], query: null, messages } : null;
  }
  return null;
}
function metricSupport(query: string, points: Point[]): SignalSupport {
  let selected = points;
  if (query === "memory_working_set") {
    selected = selected.slice(-5);
    const differences = selected.slice(1).map((point, index) => point.value - selected[index].value);
    const growth = selected.length ? selected.at(-1)!.value - selected[0].value : 0;
    if (selected.length < 3 || differences.some(delta => delta < 0) || differences.filter(delta => delta > 0).length < 2
        || growth < 1024 * 1024 || growth < selected[0].value * 0.2) return { ids: new Set(), windows: [] };
  } else {
    const threshold = thresholds[query];
    if (threshold === undefined || selected.length < 2 || selected.slice(-2).some(point => point.value <= threshold)) return { ids: new Set(), windows: [] };
    let start = selected.length - 2;
    while (start > 0 && selected[start - 1].value > threshold) start--;
    selected = selected.slice(start);
  }
  return { ids: new Set(selected.flatMap(point => [...point.ids])), windows: [[selected[0].time, selected.at(-1)!.time]] };
}
function signalSupport(evidence: unknown[], requested: string[], expectedService: string, now: number, excluded: string[], profile: string | null): SignalSupport | null {
  if (!requested.length) return { ids: new Set(), windows: [] };
  const records = evidence.map(record), counts = new Map<string, number>();
  for (const item of records) if (typeof item?.id === "string") counts.set(item.id, (counts.get(item.id) ?? 0) + 1);
  const signals = records.filter(item => item && typeof item.id === "string" && counts.get(item.id) === 1 && !excluded.includes(item.id))
    .map(item => usableSignal(item, expectedService, now)).filter((signal): signal is Signal => signal !== null);
  if (!requested.every(id => signals.some(signal => signal.id === id))) return null;
  const queries = new Map<string, Map<number, Point>>(), conflicts = new Set<string>();
  for (const signal of signals) {
    if (!signal.query) continue;
    const points = queries.get(signal.query) ?? new Map<number, Point>();
    signal.times.forEach((time, index) => {
      const value = signal.values[index], old = points.get(time);
      if (old && old.value !== value) conflicts.add(signal.query!);
      else points.set(time, { time, value, ids: new Set([...(old?.ids ?? []), signal.id]) });
    });
    queries.set(signal.query, points);
  }
  const support: SignalSupport = { ids: new Set(), windows: [] };
  const supportedMetrics = profile === null ? ["request_latency_p95", "request_error_rate"] : [profileMetrics[profile]].filter(Boolean);
  for (const query of supportedMetrics) {
    let points = conflicts.has(query) ? [] : [...(queries.get(query)?.values() ?? [])].sort((left, right) => left.time - right.time);
    let start = 0;
    for (let index = 1; index < points.length; index++) if (points[index].time - points[index - 1].time > 120_000_000) start = index;
    points = points.slice(start);
    const currentSupport = metricSupport(query, points);
    currentSupport.ids.forEach(id => support.ids.add(id)); support.windows.push(...currentSupport.windows);
  }
  const signature = profile === null ? undefined : profileLogs[profile];
  if (signature) for (const signal of signals) {
    if (signal.query !== null) continue;
    signal.messages.forEach((message, index) => {
      if (message.includes(signature)) { support.ids.add(signal.id); support.windows.push([signal.times[index], signal.times[index]]); }
    });
  }
  // Recompute from all retained service-local signals, including uncited newer
  // green/conflicting samples. Every published ID must truly contribute, and
  // the retained references must cover the independently recomputed windows.
  return requested.length === support.ids.size && requested.every(id => support.ids.has(id)) ? support : null;
}
function topologyEdges(services: string[], raw: unknown, now: number): { catalog: Set<string>; observed: Set<string> } | null {
  if (!Array.isArray(raw) || raw.length > 5) return null;
  const owners = new Set<string>(), catalog = new Set<string>(), observed = new Set<string>();
  const calls: { identity: string; claim: string; edge: string }[] = [], identities = new Map<string, Set<string>>();
  for (const value of raw) {
    const topology = record(value);
    if (!topology || !service(topology.service) || !services.includes(topology.service) || owners.has(topology.service)
        || !Array.isArray(topology.edges) || topology.edges.length > 64) return null;
    owners.add(topology.service);
    if ((topology.source === "service-catalog" || topology.source === "service-catalog-via-telemetry-gateway")
        && topology.catalog_status === "available") {
      for (const rawEdge of topology.edges) {
        const edge = record(rawEdge);
        if (edge && service(edge.source) && service(edge.target) && edge.source !== edge.target
            && [edge.source, edge.target].includes(topology.service) && services.includes(edge.source) && services.includes(edge.target)) {
          catalog.add(`${edge.source}\0${edge.target}`);
        }
      }
    }
    const orderedServices = [topology.service, ...services.filter(service => service !== topology.service)];
    const projected = observedTopologyProjection(orderedServices, topology);
    const sample = record(topology.observed);
    const start = preciseTime(sample?.start), preciseEnd = preciseTime(sample?.end), end = timestamp(sample?.end);
    if (projected.status === "available" && start !== null && preciseEnd !== null && end !== null
        && now - freshness <= end && end <= now + 5000) {
      for (const call of projected.calls) {
        const original = Array.isArray(sample?.calls) ? sample.calls.map(record).find(raw => raw?.trace_id === call.trace_id
          && raw?.client_span_id === call.client_span_id && raw?.server_span_id === call.server_span_id) : null;
        const at = preciseTime(original?.observed_at);
        if (!original || at === null || BigInt(at) < BigInt(start) || BigInt(at) > BigInt(preciseEnd)
            || currentSampleTime(original.observed_at, now) === null
            || !exact(call.trace_id, /^[0-9a-f]{32}$/) || !exact(call.client_span_id, /^[0-9a-f]{16}$/)
            || !exact(call.server_span_id, /^[0-9a-f]{16}$/)) continue;
        const identity = `${call.trace_id}\0${call.client_span_id}\0${call.server_span_id}`;
        const claim = `${call.source}\0${call.target}\0${at}`;
        identities.set(identity, new Set([...(identities.get(identity) ?? []), claim]));
        calls.push({ identity, claim, edge: `${call.source}\0${call.target}` });
      }
    }
  }
  // One physical span can be read from both endpoints, but conflicting owner,
  // endpoint or precise UTC-time claims remove all of its sampled support.
  for (const call of calls) if (identities.get(call.identity)?.size === 1) observed.add(call.edge);
  return { catalog, observed };
}
function pathSupported(path: string[], basis: string, edges: { catalog: Set<string>; observed: Set<string> }): boolean {
  const hops = path.slice(1).map((target, index) => `${path[index]}\0${target}`);
  if (basis === "primary") return hops.length === 0;
  if (!hops.length) return false;
  const graph = new Map<string, Set<string>>();
  for (const edge of [...edges.catalog, ...edges.observed]) {
    const [source, target] = edge.split("\0");
    graph.set(source, new Set([...(graph.get(source) ?? []), target]));
  }
  // A simple path can still pass through a larger directed feedback cycle.
  for (const node of path) {
    const queue = [...(graph.get(node) ?? [])], seen = new Set<string>();
    while (queue.length) {
      const next = queue.shift()!;
      if (next === node) return false;
      if (seen.has(next)) continue;
      seen.add(next); queue.push(...(graph.get(next) ?? []));
    }
  }
  if (hops.some(hop => !edges.observed.has(hop) && !edges.catalog.has(hop))) return false;
  // A retained valid sample takes priority over independent catalog fallback.
  const observedHops = hops.filter(hop => edges.observed.has(hop)).length;
  const preciseBasis = observedHops === hops.length ? "observed_trace" : observedHops ? "mixed" : "catalog";
  return basis === preciseBasis;
}

/** Candidate attribution is review metadata, not causal confirmation or authorization. */
export function rootCauseCandidatesView(incident: unknown, now = Date.now()): CandidateView {
  const state = record(incident);
  if (!state || !Number.isFinite(now) || !Array.isArray(state.services) || !state.services.length || state.services.length > 9
      || !state.services.every(service) || new Set(state.services).size !== state.services.length
      || state.complaint_service !== state.services[0] || !Object.hasOwn(state, "diagnosis_service")
      || (state.diagnosis_service !== null && !service(state.diagnosis_service))
      || !Array.isArray(state.root_cause_candidates) || state.root_cause_candidates.length > 8
      || !Array.isArray(state.evidence) || state.evidence.length > 512) return { ...unavailable };
  const excluded = state.diagnostic_excluded_evidence_ids === undefined ? [] : state.diagnostic_excluded_evidence_ids;
  if (!Array.isArray(excluded) || excluded.length > 512 || !excluded.every(id => typeof id === "string" && id.length > 0 && id.length <= 200)
      || new Set(excluded).size !== excluded.length) return { ...unavailable };
  const services = state.services;
  const edges = topologyEdges(services, state.service_topologies, now);
  if (!edges) return { ...unavailable };
  const ranks = new Set<number>(), identities = new Set<string>(), candidates: Candidate[] = [];
  for (const raw of state.root_cause_candidates) {
    const candidate = record(raw);
    if (!candidate || !Number.isSafeInteger(candidate.rank) || Number(candidate.rank) < 1 || Number(candidate.rank) > state.root_cause_candidates.length
        || ranks.has(Number(candidate.rank)) || !service(candidate.service) || !state.services.includes(candidate.service)
        || typeof candidate.incident_type !== "string" || !Object.hasOwn(familyLabels, candidate.incident_type)
        || !ids(candidate.local_evidence_ids, 1) || !ids(candidate.symptom_evidence_ids)
        || [...candidate.local_evidence_ids, ...candidate.symptom_evidence_ids].some(id => excluded.includes(id))
        || !Array.isArray(candidate.propagation_path) || !candidate.propagation_path.length || candidate.propagation_path.length > 3
        || !candidate.propagation_path.every(service) || new Set(candidate.propagation_path).size !== candidate.propagation_path.length
        || candidate.propagation_path.some(service => !services.includes(service))
        || candidate.propagation_path[0] !== state.complaint_service || candidate.propagation_path.at(-1) !== candidate.service
        || typeof candidate.path_basis !== "string" || !Object.hasOwn(pathLabels, candidate.path_basis)
        || candidate.causal_direction_verified !== false || (candidate.temporal_overlap !== null && typeof candidate.temporal_overlap !== "boolean")
        || !Array.isArray(candidate.validation_notes) || candidate.validation_notes.length > 32
        || !candidate.validation_notes.every(note => typeof note === "string" && note.length <= 4000)
        || !pathSupported(candidate.propagation_path, candidate.path_basis, edges)) return { ...unavailable };
    const crossService = candidate.service !== state.complaint_service;
    if ((crossService && (candidate.temporal_overlap !== true || !candidate.symptom_evidence_ids.length || candidate.path_basis === "primary"))
        || (!crossService && (candidate.propagation_path.length !== 1 || candidate.path_basis !== "primary"))) return { ...unavailable };
    if (crossService && candidate.incident_type === "downstream_timeout") return { ...unavailable };
    const local = signalSupport(state.evidence, candidate.local_evidence_ids, candidate.service, now, excluded, candidate.incident_type);
    const symptoms = signalSupport(state.evidence, candidate.symptom_evidence_ids, state.services[0], now, excluded, null);
    if (!local || !symptoms || (candidate.temporal_overlap === true && !local.windows.some(left =>
      symptoms.windows.some(right => left[0] <= right[1] && right[0] <= left[1])))) return { ...unavailable };
    const identity = `${candidate.service}\0${candidate.incident_type}`;
    if (identities.has(identity)) return { ...unavailable };
    identities.add(identity); ranks.add(Number(candidate.rank));
    candidates.push({ rank: Number(candidate.rank), service: candidate.service, incidentType: candidate.incident_type,
      familyLabel: familyLabels[candidate.incident_type], localEvidenceIds: [...candidate.local_evidence_ids],
      symptomEvidenceIds: [...candidate.symptom_evidence_ids], path: [...candidate.propagation_path], pathBasis: candidate.path_basis,
      pathLabel: pathLabels[candidate.path_basis], temporalLabel: candidate.temporal_overlap === true
        ? "Retained anomaly-window overlap — correlation only, not causal evidence"
        : candidate.temporal_overlap === false ? "No anomaly-window overlap claimed" : "Anomaly-window overlap not established",
      temporalOverlap: candidate.temporal_overlap as boolean | null });
  }
  const specific = candidates.filter(candidate => candidate.incidentType !== "downstream_timeout");
  const concrete = specific.length ? specific : candidates;
  if (state.diagnosis_service !== null && (concrete.length !== 1 || concrete[0].service !== state.diagnosis_service)) return { ...unavailable };
  return { available: true, complaintService: state.services[0], diagnosisService: state.diagnosis_service as string | null,
    candidates: candidates.sort((left, right) => left.rank - right.rank) };
}
