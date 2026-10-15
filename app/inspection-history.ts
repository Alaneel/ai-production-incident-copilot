import { normalizeOperationObservation, type OperationInspection } from "./operation-inspection.ts";
import { ConsoleAuthError, consoleAccessDenied } from "./console-auth.ts";

export type InspectionAudit = {
  id: string; startedAt: string; completedAt: string | null;
  state: "completed" | "unavailable" | "unfinished";
  tool: "restart_deployment" | "rollback_release";
  service: string; namespace: string; revision: string | null;
  requester: string; approvedActor: string; operationKey: string;
  authMode: "required" | "disabled";
  observation: Pick<OperationInspection, "status" | "source" | "observedAt"> | null;
};

function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Invalid audit record");
  return value as Record<string, unknown>;
}
function text(value: unknown, limit: number): string {
  if (typeof value !== "string" || !value.trim() || value.length > limit) throw new Error("Invalid audit field");
  return value;
}
function timestamp(value: unknown): string {
  const date = text(value, 80);
  if (!Number.isFinite(Date.parse(date))) throw new Error("Invalid audit time");
  return date;
}

export function normalizeInspectionHistory(raw: unknown, incidentId: string): InspectionAudit[] {
  if (!Array.isArray(raw) || raw.length > 100) throw new Error("Invalid history container");
  const ids = new Set<string>();
  return raw.map((value) => {
    const row = object(value);
    const request = object(row.request);
    const args = object(request.arguments);
    const id = text(row.audit_id, 64);
    if (!/^inspection_[a-f0-9]{32}$/.test(id) || ids.has(id) || row.incident_id !== incidentId || request.incident_id !== incidentId ||
        request.read_only !== true || (request.auth_mode !== "required" && request.auth_mode !== "disabled") ||
        (request.tool !== "restart_deployment" && request.tool !== "rollback_release")) throw new Error("Invalid audit binding");
    ids.add(id);
    const service = text(args.service, 63);
    const namespace = text(args.namespace, 63);
    const revision = args.revision == null ? null : text(args.revision, 80);
    if (!/^[a-z0-9-]{2,63}$/.test(service) || !/^[a-z0-9-]{2,63}$/.test(namespace) ||
        (request.tool === "rollback_release") !== (revision !== null) ||
        (revision && !/^[a-zA-Z0-9._-]{1,80}$/.test(revision))) throw new Error("Invalid audit target");
    let state: InspectionAudit["state"] = "unfinished";
    let completedAt: string | null = null;
    let observation: InspectionAudit["observation"] = null;
    if (row.outcome !== null) {
      const outcome = object(row.outcome);
      if (outcome.status !== "completed" && outcome.status !== "unavailable") throw new Error("Invalid audit outcome");
      state = outcome.status;
      completedAt = timestamp(outcome.completed_at);
      if (state === "completed") observation = normalizeOperationObservation(outcome.observation);
    }
    return { id, startedAt: timestamp(row.started_at), completedAt, state, tool: request.tool,
             service, namespace, revision, requester: text(request.request_principal, 120),
             approvedActor: text(request.approval_actor, 120), operationKey: text(request.idempotency_key, 200),
             authMode: request.auth_mode, observation };
  });
}

export async function loadInspectionHistory(url: string, incidentId: string, transport: typeof fetch = fetch):
Promise<{ records: InspectionAudit[] | null; error: string | null }> {
  try {
    const response = await transport(`${url}/v1/incidents/${encodeURIComponent(incidentId)}/operation-inspections`, {
      method: "GET", cache: "no-store", signal: AbortSignal.timeout(15_000),
    });
    if (response.status === 401 || response.status === 403) return {
      records: null, error: consoleAccessDenied(response.status, "read"),
    };
    if (!response.ok) throw new Error("History unavailable");
    return { records: normalizeInspectionHistory(await response.json(), incidentId), error: null };
  } catch (error) {
    if (error instanceof ConsoleAuthError) return { records: null, error: error.message };
    return { records: null, error: "Inspection history is unavailable or invalid. Incident status is unchanged; no operation was queried or retried." };
  }
}
