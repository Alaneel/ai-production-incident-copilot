export type OperationInspection = {
  incidentId: string;
  incidentStatus: string;
  tool: "restart_deployment" | "rollback_release";
  status: string;
  source: string;
  observedAt: string | null;
};

const sourceStatuses: Record<string, Set<string>> = {
  kubernetes_deployment_marker: new Set(["marker_matches", "not_observed", "unverified_marker", "conflicting_marker"]),
  process_cache: new Set(["unknown", "recorded_response", "conflicting_record"]),
  fixture: new Set(["unknown"]),
};

export const operationLabels: Record<string, string> = {
  marker_matches: "Operation marker matches — rollout health unverified",
  not_observed: "Operation marker not observed — execution may still have occurred",
  unverified_marker: "Legacy marker — operation identity unverified",
  conflicting_marker: "Conflicting operation marker — manual review required",
  recorded_response: "Process cache has a response — not durable execution proof",
  conflicting_record: "Conflicting cached operation — manual review required",
  unknown: "Operation outcome unknown",
};

export function normalizeOperationObservation(raw: unknown): Pick<OperationInspection, "status" | "source" | "observedAt"> {
  if (!raw || typeof raw !== "object") throw new Error("Invalid observation");
  const item = raw as Record<string, unknown>;
  if (item.read_only !== true || item.rollout_health_verified !== false ||
      typeof item.source !== "string" || typeof item.status !== "string" || !sourceStatuses[item.source]?.has(item.status) ||
      (item.observed_at != null && (typeof item.observed_at !== "string" || !Number.isFinite(Date.parse(item.observed_at))))) {
    throw new Error("Invalid observation");
  }
  return { status: item.status, source: item.source, observedAt: typeof item.observed_at === "string" ? item.observed_at : null };
}

export async function inspectOperation(
  url: string, incidentId: string, expectedTool: string, transport: typeof fetch = fetch,
): Promise<{ observation: OperationInspection | null; error: string | null }> {
  try {
    const response = await transport(`${url}/v1/incidents/${encodeURIComponent(incidentId)}/operation-status`, {
      method: "GET", cache: "no-store", signal: AbortSignal.timeout(20_000),
    });
    if (response.status === 401 || response.status === 403) {
      return { observation: null, error: consoleAccessDenied(response.status, "operator") };
    }
    if (response.status === 409) {
      return { observation: null, error: "No matching approved execution intent is available. Review the incident trace; no action was retried." };
    }
    if (!response.ok) throw new Error("Inspection unavailable");
    const raw: unknown = await response.json();
    if (!raw || typeof raw !== "object") throw new Error("Invalid inspection");
    const result = raw as Record<string, unknown>;
    if (result?.incident_id !== incidentId || result?.tool !== expectedTool ||
        (result.tool !== "restart_deployment" && result.tool !== "rollback_release") ||
        typeof result.incident_status !== "string" ||
        !["mitigating", "remediation_pending", "remediation_failed", "resolved"].includes(result.incident_status) ||
        result?.read_only !== true) {
      throw new Error("Invalid inspection");
    }
    return { observation: { incidentId, incidentStatus: result.incident_status, tool: result.tool,
                           ...normalizeOperationObservation(result.observation) }, error: null };
  } catch (error) {
    if (error instanceof ConsoleAuthError) return { observation: null, error: error.message };
    return { observation: null, error: "Read-only inspection is unavailable. Incident status is unchanged and no action was retried." };
  }
}
import { ConsoleAuthError, consoleAccessDenied } from "./console-auth.ts";
