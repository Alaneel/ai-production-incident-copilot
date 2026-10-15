"use client";

import { useEffect, useRef, useState } from "react";
import { inspectOperation, operationLabels, type OperationInspection } from "./operation-inspection";
import { InspectionHistoryPanel } from "./InspectionHistoryPanel";

export function OperationInspectionPanel({ url, incidentId, tool, transport = fetch, operatorAccessError, readAccessError }: {
  url: string | undefined; incidentId: string; tool: string;
  transport?: typeof fetch; operatorAccessError?: string | null; readAccessError?: string | null;
}) {
  const [busy, setBusy] = useState(false);
  const [observation, setObservation] = useState<OperationInspection | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [checkedAt, setCheckedAt] = useState<string | null>(null);
  const active = useRef(true);
  const inFlight = useRef(false);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);

  async function inspect() {
    if (!url || inFlight.current || operatorAccessError) return;
    inFlight.current = true;
    setBusy(true);
    setObservation(null);
    setError(null);
    setCheckedAt(null);
    const result = await inspectOperation(url, incidentId, tool, transport);
    if (active.current) {
      setObservation(result.observation);
      setError(result.error);
      setCheckedAt(new Date().toLocaleTimeString("en-SG", { hour12: false }));
      setBusy(false);
    }
    inFlight.current = false;
  }

  return <div className="operation-inspection">
    <strong>Read-only operation inspection</strong>
    <p>Queries the approved operation only. It cannot retry execution, release the approval lock, or resolve the incident.</p>
    <button type="button" onClick={() => void inspect()} disabled={busy || !url || Boolean(operatorAccessError)}>
      {busy ? "Inspecting operation…" : "Inspect operation marker"}
    </button>
    {!url && <p>Connect the incident backend to inspect this operation.</p>}
    {operatorAccessError && <p role="status">{operatorAccessError}</p>}
    <div role="status" aria-live="polite">
      {error && <p className="inspection-error">{error}</p>}
      {observation && <>
        <p>{operationLabels[observation.status]}</p>
        <p>Source: {observation.source.replaceAll("_", " ")} · checked {checkedAt}</p>
        {observation.observedAt && <p>Provider observation: {new Date(observation.observedAt).toLocaleString("en-SG")}</p>}
        <p>Latest marker/cache only. Absence does not prove non-execution; a match does not prove rollout health. Incident status is unchanged.</p>
      </>}
    </div>
    <InspectionHistoryPanel url={url} incidentId={incidentId} transport={transport} readAccessError={readAccessError} />
  </div>;
}
