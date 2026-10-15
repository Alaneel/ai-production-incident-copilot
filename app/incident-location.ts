export type IncidentLocation = { incidentId: string; approvalPending: boolean };

export function readIncidentLocation(url: string): IncidentLocation | null {
  const params = new URL(url).searchParams;
  const incidentId = params.get("incident");
  if (incidentId === null) return null;
  if (!/^INC-[A-Za-z0-9_-]{1,100}$/.test(incidentId)) {
    throw new Error("Invalid incident link.");
  }
  return { incidentId, approvalPending: params.has("approval_pending") };
}

// Persist only an incident reference and ambiguity marker, never credentials,
// evidence, approval receipts, or a command that could be replayed.
export function incidentLocationUrl(url: string, incidentId: string, approvalPending: boolean): string {
  const next = new URL(url);
  next.searchParams.set("incident", incidentId);
  if (approvalPending) next.searchParams.set("approval_pending", "1");
  else next.searchParams.delete("approval_pending");
  return next.toString();
}

export function restoredApprovalUncertain(status: string | null, hadPendingApproval: boolean): boolean {
  if (status === null) return true;
  if (["resolved", "rejected", "diagnosed", "insufficient_evidence", "remediation_failed", "remediation_pending"].includes(status)) return false;
  if (status === "waiting_approval") return hadPendingApproval;
  return status !== "investigating" || hadPendingApproval;
}
