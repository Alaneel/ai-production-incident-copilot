"use client";

import { useEffect, useRef, useState } from "react";
import { loadInspectionHistory, type InspectionAudit } from "./inspection-history";
import { operationLabels } from "./operation-inspection";

export function InspectionHistoryPanel({ url, incidentId, transport = fetch, readAccessError }: {
  url: string | undefined; incidentId: string; transport?: typeof fetch; readAccessError?: string | null;
}) {
  const [records, setRecords] = useState<InspectionAudit[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const active = useRef(true);
  const inFlight = useRef(false);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);

  async function refresh() {
    if (!url || inFlight.current || readAccessError) return;
    inFlight.current = true;
    setBusy(true); setRecords(null); setError(null);
    const result = await loadInspectionHistory(url, incidentId, transport);
    if (active.current) { setRecords(result.records); setError(result.error); setBusy(false); }
    inFlight.current = false;
  }

  return <section className="inspection-history" aria-label="Retained operation inspection history">
    <strong>Inspection audit history</strong>
    <p>Latest 100 records, separate from the incident trace. Retention follows backend storage configuration; memory mode is process-local.</p>
    <button type="button" disabled={busy || !url || Boolean(readAccessError)} onClick={() => void refresh()}>
      {busy ? "Loading inspection history…" : "Refresh inspection history"}
    </button>
    {readAccessError && <p role="status">{readAccessError}</p>}
    <div role="status" aria-live="polite">
      {error && <p className="inspection-error">{error}</p>}
      {records?.length === 0 && <p>No inspection records retained. This does not prove the operation was never executed.</p>}
    </div>
    {records && records.length > 0 && <ol>
      {records.map((record) => <li key={record.id}>
        <strong>{record.state === "completed" ? "Query completed — not incident resolution"
          : record.state === "unavailable" ? "Query unavailable — operation outcome unchanged"
          : "Audit unfinished — query completion unconfirmed"}</strong>
        <p>{record.observation ? operationLabels[record.observation.status]
          : "Do not infer execution success or failure from this audit record; no retry is authorized."}</p>
        <details><summary>{record.id} · {new Date(record.startedAt).toLocaleString("en-SG")}</summary>
          <p>Requester: {record.requester} · approval actor: {record.approvedActor}</p>
          <p>{record.authMode === "disabled" ? "Demo authorization disabled — not verified human identity" : "Token-bound operator identity — not human SSO"}</p>
          <p>{record.tool} · {record.namespace}/{record.service}{record.revision ? ` · revision ${record.revision}` : ""}</p>
          <p>Approved operation key: <code>{record.operationKey}</code></p>
          {record.completedAt && <p>Query completed at {new Date(record.completedAt).toLocaleString("en-SG")}</p>}
          {record.observation && <p>Source: {record.observation.source.replaceAll("_", " ")} · rollout health unverified</p>}
        </details>
      </li>)}
    </ol>}
  </section>;
}
