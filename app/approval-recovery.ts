type Snapshot = { incident_id: string; status: string };
const terminal = new Set([
  "resolved", "rejected", "diagnosed", "insufficient_evidence",
  "remediation_failed", "remediation_pending",
]);
const statuses = new Set([...terminal, "investigating", "waiting_approval", "mitigating"]);

export type ApprovalResult<T> = {
  incident: T | null;
  uncertain: boolean;
  error: string | null;
};

function validate<T extends Snapshot>(value: unknown, incidentId: string): T {
  const state = value as T;
  if (!state || state.incident_id !== incidentId || !statuses.has(state.status)) {
    throw new Error("Invalid incident response.");
  }
  return state;
}

// Recovery is read-only. An ambiguous POST must never be automatically replayed.
export async function refreshApproval<T extends Snapshot>(
  url: string, incidentId: string, transport: typeof fetch = fetch,
): Promise<ApprovalResult<T>> {
  try {
    const response = await transport(`${url}/v1/incidents/${encodeURIComponent(incidentId)}`, {
      method: "GET", cache: "no-store", signal: AbortSignal.timeout(10_000),
    });
    if (response.status === 401 || response.status === 403) return {
      incident: null, uncertain: true, error: consoleAccessDenied(response.status, "read"),
    };
    if (!response.ok) throw new Error("Status unavailable.");
    const incident = validate<T>(await response.json(), incidentId);
    return { incident, uncertain: !terminal.has(incident.status), error: null };
  } catch (error) {
    if (error instanceof ConsoleAuthError) return { incident: null, uncertain: true, error: error.message };
    return { incident: null, uncertain: true, error: "Could not refresh incident status. Execution may already have started; do not resubmit the action." };
  }
}

export async function sendApproval<T extends Snapshot>(
  url: string, incidentId: string, body: Record<string, unknown>, transport: typeof fetch = fetch,
): Promise<ApprovalResult<T>> {
  try {
    const response = await transport(`${url}/v1/incidents/${encodeURIComponent(incidentId)}/approval`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body), signal: AbortSignal.timeout(30_000),
    });
    if (response.status === 401 || response.status === 403) return {
      incident: null, uncertain: false, error: consoleAccessDenied(response.status, "operator") + " No approval decision was accepted.",
    };
    if (!response.ok) throw new Error("Approval response unavailable.");
    const incident = validate<T>(await response.json(), incidentId);
    return { incident, uncertain: !terminal.has(incident.status), error: null };
  } catch (error) {
    if (error instanceof ConsoleAuthError) return { incident: null, uncertain: false, error: error.message };
    const recovered = await refreshApproval<T>(url, incidentId, transport);
    return {
      ...recovered,
      error: recovered.uncertain
        ? "Approval outcome is not yet confirmed. Refresh status instead of approving or rejecting again."
        : "Approval response was interrupted; the retained incident status was recovered without replaying the action.",
    };
  }
}
import { ConsoleAuthError, consoleAccessDenied } from "./console-auth.ts";
