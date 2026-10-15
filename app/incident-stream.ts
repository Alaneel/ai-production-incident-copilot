import { ConsoleAuthError, consoleAccessDenied } from "./console-auth.ts";

const MAX_FRAME_BYTES = 8 * 1024 * 1024;
const MAX_WIRE_BYTES = 32 * 1024 * 1024;
const MAX_FRAMES = 128;
const encoder = new TextEncoder();
const incidentId = /^INC-[A-Za-z0-9_-]{1,100}$/;
const service = /^[a-z0-9][a-z0-9-]{0,62}$/;
const steps = new Set(["classify_incident", "select_services", "query_telemetry", "retrieve_context",
  "generate_hypotheses", "validate_evidence", "propose_remediation", "human_approval",
  "prepare_execution", "execute_or_simulate", "verify_recovery", "wait_for_recovery", "complete"]);
const statuses = new Set(["investigating", "waiting_approval", "mitigating", "diagnosed",
  "insufficient_evidence", "remediation_failed", "remediation_pending", "resolved", "rejected"]);
const diagnosticFinal = new Set(["waiting_approval", "diagnosed", "insufficient_evidence", "remediation_failed"]);
const kinds = new Set(["metric", "log", "trace", "deployment", "deployment_event", "runbook", "past_incident", "technical_doc"]);
const errorMessages: Record<string, string> = {
  input_rejected: "The diagnosis request was rejected by input safety checks.",
  tool_unavailable: "A diagnostic tool is unavailable; diagnosis completion was not confirmed.",
  model_unavailable: "The diagnosis model is unavailable; diagnosis completion was not confirmed.",
  persistence_unavailable: "The diagnostic checkpoint could not be confirmed.",
  diagnosis_unavailable: "Diagnosis is unavailable; completion was not confirmed.",
};
type RecordValue = Record<string, unknown>;
type Frame = { event: string; id: string | null; data: string };
export type IncidentStreamEvent<T> = { event: "incident" | "complete"; cursor: number; incident: T }
  | { event: "error"; code: string; incidentId: string | null };
export type IncidentStreamResult<T> = { incident: T | null; incidentId: string | null; complete: boolean; uncertain: boolean; error: string | null };
export class IncidentStreamProtocolError extends Error {
  constructor() { super("Invalid diagnostic stream. Completion was not confirmed; use read-only incident refresh, not another diagnosis POST."); this.name = "IncidentStreamProtocolError"; }
}
function invalid(): never { throw new IncidentStreamProtocolError(); }
function record(value: unknown): value is RecordValue {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
function text(value: unknown, max = 65_536, empty = false): value is string {
  return typeof value === "string" && value.length <= max && (empty || value.length > 0);
}
function exact(value: string, pattern: RegExp): boolean { return pattern.exec(value)?.[0] === value; }
function names(value: unknown, max = 64): value is string[] {
  return Array.isArray(value) && value.length <= max && value.every(item => text(item, 63) && exact(item, service))
    && new Set(value).size === value.length;
}
function strings(value: unknown, max = 4096): value is string[] {
  return Array.isArray(value) && value.length <= max && value.every(item => text(item, 2048));
}
function time(value: unknown): value is string {
  if (typeof value !== "string" || !exact(value, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/)
      || !Number.isFinite(Date.parse(value))) return false;
  const [year, month, day, hour, minute, second] = value.slice(0, 19).split(/[-T:]/).map(Number);
  return new Date(Date.UTC(year, month - 1, day, hour, minute, second)).toISOString().slice(0, 19) === value.slice(0, 19);
}
function json(value: unknown, depth = 0): boolean {
  if (depth > 24) return false;
  if (value === null || typeof value === "boolean" || typeof value === "string") return true;
  if (typeof value === "number") return Number.isFinite(value);
  if (Array.isArray(value)) return value.length <= 16_384 && value.every(item => json(item, depth + 1));
  return record(value) && Object.keys(value).length <= 4096 && Object.entries(value).every(([key, item]) =>
    !["__proto__", "prototype", "constructor"].includes(key) && json(item, depth + 1));
}
function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (record(value)) return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}`;
  return JSON.stringify(value);
}

function snapshot(value: unknown, question: string, environment: string): RecordValue {
  if (!record(value) || !json(value) || !text(value.incident_id, 104) || !exact(value.incident_id, incidentId)
      || !text(value.trace_id, 128) || !exact(value.trace_id, /^tr_[A-Za-z0-9_-]{1,100}$/)
      || value.question !== question || value.environment !== environment || !time(value.created_at) || !time(value.updated_at)
      || !text(value.status, 40) || !statuses.has(value.status) || !text(value.current_step, 80) || !steps.has(value.current_step)
      || (value.incident_type !== null && !text(value.incident_type, 100))
      || (value.severity !== null && !text(value.severity, 40)) || !names(value.services, 9)
      || !Array.isArray(value.evidence) || value.evidence.length > 4096
      || !Array.isArray(value.hypotheses) || value.hypotheses.length > 64
      || !Array.isArray(value.trace) || value.trace.length > 8192) invalid();
  if (value.anomalous_window !== null && (!Array.isArray(value.anomalous_window) || value.anomalous_window.length !== 2
      || !value.anomalous_window.every(time) || Date.parse(value.anomalous_window[0]) > Date.parse(value.anomalous_window[1]))) invalid();
  const ids = new Set();
  for (const item of value.evidence) {
    if (!record(item) || !text(item.id, 2048) || ids.has(item.id) || !text(item.kind, 40) || !kinds.has(item.kind)
        || !text(item.source, 1024) || !text(item.title, 4096) || !text(item.observation, 65_536, true) || !time(item.observed_at)
        || !text(item.service, 63) || !exact(item.service, service) || typeof item.score !== "number" || item.score < 0 || item.score > 1
        // A broad log query is intentionally the empty string in the actual
        // Evidence(str | None) contract; it is metadata, not proof of a cause.
        || (item.query !== null && !text(item.query, 4096, true)) || !record(item.attributes)) invalid();
    ids.add(item.id);
  }
  for (const item of value.hypotheses) {
    if (!record(item) || !text(item.id, 2048) || !Number.isSafeInteger(item.rank) || Number(item.rank) < 1
        || !text(item.cause) || typeof item.confidence !== "number" || item.confidence < 0 || item.confidence > 1
        || !strings(item.evidence_ids) || !strings(item.conflicting_evidence_ids) || !strings(item.validation_notes)
        || (item.cause_service !== null && (!text(item.cause_service, 63) || !exact(item.cause_service, service)))
        || !names(item.propagation_path, 3) || item.causal_direction_verified !== false) invalid();
  }
  if (value.remediation !== null) {
    const item = value.remediation;
    if (!record(item) || !text(item.id, 2048) || !text(item.title, 4096) || !text(item.tool, 80) || !record(item.arguments)
        || !text(item.command_preview, 8192, true) || !text(item.expected_result) || !text(item.risk, 100)
        || typeof item.requires_approval !== "boolean" || typeof item.dry_run_passed !== "boolean"
        || (item.preflight_mode !== null && !["simulation", "kubernetes-server-dry-run"].includes(String(item.preflight_mode)))) invalid();
  }
  value.trace.forEach((event, index) => {
    if (!record(event) || event.sequence !== index + 1 || !text(event.step, 80) || !steps.has(event.step)
        || !text(event.event, 160) || !time(event.occurred_at) || !record(event.payload)
        || (event.duration_ms !== null && (!Number.isSafeInteger(event.duration_ms) || Number(event.duration_ms) < 0))) invalid();
  });
  // Optional forward-compatible projections are bounded and object-shaped;
  // their own display helpers independently validate semantics and citations.
  for (const field of ["service_impacts", "service_topologies", "root_cause_candidates", "service_investigations"]) {
    if (value[field] !== undefined && (!Array.isArray(value[field]) || value[field].length > 128 || !value[field].every(record))) invalid();
  }
  if (Array.isArray(value.service_impacts)) for (const item of value.service_impacts) {
    if (!record(item) || !text(item.service, 63) || !exact(item.service, service) || !text(item.status, 100)
        || !strings(item.evidence_ids) || (item.overlaps_primary_window !== null && typeof item.overlaps_primary_window !== "boolean")) invalid();
  }
  if (value.service_topology !== undefined && value.service_topology !== null && !record(value.service_topology)) invalid();
  for (const field of ["candidate_incident_types", "diagnostic_excluded_evidence_ids"]) {
    if (value[field] !== undefined && !strings(value[field])) invalid();
  }
  for (const field of ["complaint_service", "diagnosis_service"]) {
    if (value[field] !== undefined && value[field] !== null && (!text(value[field], 63) || !exact(value[field], service))) invalid();
  }
  return value;
}

/** A bounded SSE decoder, not an EventSource/reconnection client. */
export class IncidentStreamDecoder<T> {
  private buffer = "";
  private frameLines: string[] = [];
  private frameBytes = 0;
  private wireBytes = 0;
  private frames = 0;
  private cursor = -1;
  private identity: string | null = null;
  private trace: unknown[] = [];
  private previous: string | null = null;
  private terminal: "complete" | "error" | null = null;
  private errorIdentity: string | null = null;
  private question: string;
  private environment: string;
  lastIncident: T | null = null;
  constructor(question: string, environment = "prod-sg") { this.question = question; this.environment = environment; }
  push(chunk: string): IncidentStreamEvent<T>[] {
    this.wireBytes += encoder.encode(chunk).length;
    if (this.wireBytes > MAX_WIRE_BYTES) invalid();
    this.buffer += chunk;
    const result: IncidentStreamEvent<T>[] = [];
    let end;
    while ((end = this.buffer.indexOf("\n")) !== -1) {
      const line = this.buffer.slice(0, end).replace(/\r$/, "");
      this.buffer = this.buffer.slice(end + 1);
      this.frameBytes += encoder.encode(line).length + 1;
      if (this.frameBytes > MAX_FRAME_BYTES) invalid();
      if (line) this.frameLines.push(line);
      else {
        const event = this.parseFrame();
        this.frameLines = []; this.frameBytes = 0;
        if (event) result.push(event);
      }
    }
    if (this.frameBytes + encoder.encode(this.buffer).length > MAX_FRAME_BYTES) invalid();
    return result;
  }
  finish(): void {
    // An incomplete record or a clean EOF without complete/error is unknown.
    if (this.buffer || this.frameLines.length || !this.terminal) invalid();
  }
  private parseFrame(): IncidentStreamEvent<T> | null {
    const meaningful = this.frameLines.filter(line => !line.startsWith(":"));
    if (!meaningful.length) return null;
    if (++this.frames > MAX_FRAMES) invalid();
    const frame: Frame = { event: "", id: null, data: "" };
    const data: string[] = [];
    for (const line of meaningful) {
      const colon = line.indexOf(":"), field = colon === -1 ? line : line.slice(0, colon);
      const value = colon === -1 ? "" : line.slice(colon + 1).replace(/^ /, "");
      if (field === "data") data.push(value);
      else if (field === "event" && !frame.event) frame.event = value;
      else if (field === "id" && frame.id === null) frame.id = value;
      else invalid();
    }
    frame.data = data.join("\n");
    let raw: unknown;
    try { raw = JSON.parse(frame.data); } catch { invalid(); }
    if (frame.event === "error") {
      if (frame.id !== null || !record(raw) || !text(raw.code, 80) || !Object.hasOwn(errorMessages, raw.code)
          || !text(raw.message, 1024) || Object.keys(raw).some(key => !["code", "message", "incident_id"].includes(key))
          || (raw.incident_id !== undefined && (!text(raw.incident_id, 104) || !exact(raw.incident_id, incidentId)))) invalid();
      const id = raw.incident_id as string | undefined;
      if (id && this.identity && id !== (this.lastIncident as RecordValue).incident_id) invalid();
      const identity = canonical(raw);
      if (this.terminal === "error" && this.errorIdentity === identity) return null;
      if (this.terminal) invalid();
      this.terminal = "error"; this.errorIdentity = identity;
      return { event: "error", code: raw.code, incidentId: id ?? null };
    }
    if (!["incident", "complete"].includes(frame.event) || frame.id === null || !/^(?:0|[1-9]\d{0,4})$/.test(frame.id)) invalid();
    const state = snapshot(raw, this.question, this.environment), cursor = Number(frame.id);
    if (cursor !== (state.trace as unknown[]).length || cursor < this.cursor) invalid();
    const identity = canonical([state.incident_id, state.trace_id, state.question, state.environment, state.created_at]);
    if (this.identity && identity !== this.identity) invalid();
    const normalized = canonical(state);
    if (cursor === this.cursor && normalized !== this.previous) invalid();
    if (this.trace.some((event, index) => canonical(event) !== canonical((state.trace as unknown[])[index]))) invalid();
    if (this.terminal) {
      if (this.terminal === "complete" && frame.event === "complete" && normalized === this.previous) return null;
      invalid();
    }
    if (frame.event === "complete" && (!this.lastIncident || cursor !== this.cursor || normalized !== this.previous
        || !diagnosticFinal.has(String(state.status)) || !Array.isArray(state.services) || !state.services.length)) invalid();
    if (cursor === this.cursor && frame.event === "incident") return null;
    this.identity = identity; this.cursor = cursor; this.previous = normalized;
    this.trace = state.trace as unknown[]; this.lastIncident = state as T;
    if (frame.event === "complete") this.terminal = "complete";
    return { event: frame.event as "incident" | "complete", cursor, incident: state as T };
  }
}

export async function streamConsoleIncident<T>(backend: string | undefined, question: string,
  transport: typeof fetch, signal: AbortSignal, onSnapshot: (incident: T) => void,
): Promise<IncidentStreamResult<T>> {
  const decoder = new IncidentStreamDecoder<T>(question);
  let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
  const cancel = () => { void reader?.cancel().catch(() => {}); };
  let complete = false, serverError: string | null = null, errorIncidentId: string | null = null;
  const knownId = () => decoder.lastIncident ? String((decoder.lastIncident as RecordValue).incident_id) : errorIncidentId;
  try {
    if (!backend) throw new ConsoleAuthError("Configure the incident backend before diagnosis. No request was sent.");
    if (signal.aborted) throw new DOMException("Aborted", "AbortError");
    const response = await transport(`${backend}/v1/incidents/stream`, { method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify({ question, environment: "prod-sg" }), signal });
    if (response.status === 401 || response.status === 403) return {
      incident: null, incidentId: null, complete: false, uncertain: false, error: consoleAccessDenied(response.status, "read"),
    };
    if (response.status === 400 || response.status === 422) return { incident: null, incidentId: null,
      complete: false, uncertain: false, error: "Diagnosis request was rejected before streaming. Review the input; no demo fallback was used." };
    if (!response.ok || !/^text\/event-stream(?:\s*;|$)/i.test(response.headers.get("Content-Type") ?? "") || !response.body) invalid();
    reader = response.body.getReader();
    signal.addEventListener("abort", cancel, { once: true });
    const utf8 = new TextDecoder("utf-8", { fatal: true });
    let bytes = 0;
    while (true) {
      if (signal.aborted) throw new DOMException("Aborted", "AbortError");
      const chunk = await reader.read();
      if (signal.aborted) throw new DOMException("Aborted", "AbortError");
      if (chunk.done) {
        if (utf8.decode()) invalid();
        decoder.finish();
        break;
      }
      bytes += chunk.value.byteLength;
      if (bytes > MAX_WIRE_BYTES) invalid();
      for (const event of decoder.push(utf8.decode(chunk.value, { stream: true }))) {
        if (signal.aborted) throw new DOMException("Aborted", "AbortError");
        if (event.event === "error") { serverError = errorMessages[event.code]; errorIncidentId = event.incidentId; }
        else {
          onSnapshot(event.incident);
          if (event.event === "complete") complete = true;
        }
      }
    }
    if (serverError) return { incident: decoder.lastIncident, incidentId: knownId(), complete: false, uncertain: true,
      error: `${serverError} ${decoder.lastIncident || errorIncidentId ? "Refresh the retained incident with a read-only GET." : "No incident reference was confirmed; do not automatically resubmit the diagnosis."}` };
    return { incident: decoder.lastIncident, incidentId: knownId(), complete, uncertain: !complete, error: null };
  } catch (error) {
    return { incident: decoder.lastIncident, incidentId: knownId(), complete: false, uncertain: !(error instanceof ConsoleAuthError),
      error: error instanceof ConsoleAuthError ? error.message : error instanceof IncidentStreamProtocolError ? error.message
        : "Diagnostic stream was interrupted. Completion was not confirmed. Refresh the retained incident read-only; no diagnosis POST or demo fallback was retried." };
  } finally {
    signal.removeEventListener("abort", cancel);
    if (reader) { try { await reader.cancel(); } catch { /* Cancellation is best-effort, never a request retry. */ } reader.releaseLock(); }
  }
}
