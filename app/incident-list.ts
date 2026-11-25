import { ConsoleAuthError, consoleAccessDenied } from "./console-auth.ts";

export type IncidentSummary = {
  incident_id: string;
  status: string;
  environment: string;
  service: string | null;
  severity: string | null;
  question: string;
  leading_cause: string | null;
  proposed_action: string | null;
  updated_at: string;
};

export type IncidentListResult = { incidents: IncidentSummary[]; error: string | null };

const statuses = new Set(["investigating", "waiting_approval", "mitigating", "diagnosed", "insufficient_evidence",
  "remediation_failed", "remediation_pending", "resolved", "rejected"]);
const text = (value: unknown, limit: number): value is string => typeof value === "string" && value.length <= limit;
const optional = (value: unknown, limit: number): value is string | null => value === null || text(value, limit);

// Summaries are untrusted backend output: accept only the bounded, expected
// shape, and drop the whole response rather than render a partial one.
function summary(value: unknown): IncidentSummary {
  const row = value as Record<string, unknown>;
  if (!row || typeof row !== "object" || Array.isArray(row)
      || typeof row.incident_id !== "string" || !/^INC-[A-Za-z0-9_-]{1,100}$/.test(row.incident_id)
      || typeof row.status !== "string" || !statuses.has(row.status)
      || !text(row.environment, 40) || !optional(row.service, 80) || !optional(row.severity, 40)
      || !text(row.question, 240) || !optional(row.leading_cause, 240) || !optional(row.proposed_action, 160)
      || !text(row.updated_at, 40) || !Number.isFinite(Date.parse(row.updated_at))) {
    throw new Error("Invalid incident list response.");
  }
  return { incident_id: row.incident_id, status: row.status, environment: row.environment, service: row.service,
    severity: row.severity, question: row.question, leading_cause: row.leading_cause,
    proposed_action: row.proposed_action, updated_at: row.updated_at };
}

// Read-only. Opening a listed incident is a separate read-only GET by ID.
export async function loadIncidentList(url: string | undefined, transport: typeof fetch = fetch): Promise<IncidentListResult> {
  try {
    if (!url) throw new ConsoleAuthError("Configure the incident backend to list incidents. No request was sent.");
    const response = await transport(`${url}/v1/incidents`, { method: "GET", cache: "no-store", signal: AbortSignal.timeout(10_000) });
    if (response.status === 401 || response.status === 403) return { incidents: [], error: consoleAccessDenied(response.status, "read") };
    if (!response.ok) throw new Error("Incident list unavailable.");
    const body = await response.json() as { incidents?: unknown };
    if (!body || !Array.isArray(body.incidents) || body.incidents.length > 100) throw new Error("Invalid incident list response.");
    return { incidents: body.incidents.map(summary), error: null };
  } catch (error) {
    if (error instanceof ConsoleAuthError) return { incidents: [], error: error.message };
    return { incidents: [], error: "Could not load the incident list. Nothing was changed." };
  }
}

export function attentionCount(incidents: IncidentSummary[]): number {
  return incidents.filter((incident) => incident.status === "waiting_approval").length;
}

export function relativeTime(iso: string, now: number): string {
  const seconds = Math.max(0, Math.round((now - Date.parse(iso)) / 1000));
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86_400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86_400)}d ago`;
}
