"use client";

import { useEffect, useId, useRef, useState } from "react";
import { evidenceAnchor } from "./evidence-citations";
import { checkIncidentRecovery, recoveryCheckEndpoint, recoveryReasonLabels,
  recoveryVerificationView, validRecoveryOperatorToken } from "./recovery-verification";

export function RecoveryVerificationPanel<T extends { incident_id: string; status: string }>({ url, incident, operatorToken, transport = fetch, onUpdate }: {
  url: string | undefined; incident: T; operatorToken?: string; transport?: typeof fetch; onUpdate: (incident: T) => void;
}) {
  const review = recoveryVerificationView(incident);
  const [localToken, setLocalToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const active = useRef(true), inFlight = useRef(false);
  const tokenInputId = useId();
  const token = operatorToken ?? localToken;
  const endpoint = recoveryCheckEndpoint(url, incident.incident_id);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);

  async function check() {
    if (!url || inFlight.current || !review.canCheck || !validRecoveryOperatorToken(token)) return;
    inFlight.current = true;
    setBusy(true);
    setError(null);
    const result = await checkIncidentRecovery(url, incident, token, transport);
    if (active.current) {
      setError(result.error);
      if (result.incident) { setLocalToken(""); onUpdate(result.incident); }
      setBusy(false);
    }
    inFlight.current = false;
  }

  return <section className="action-evidence-review" aria-label="Execution and recovery verification">
    <h3>Execution and recovery verification</h3>
    <p><strong>{review.label}</strong></p>
    <p>An execution acknowledgement is not recovery. Verification separately checks the applied Deployment rollout and sustained post-execution latency/error SLIs. Observed recovery does not prove that the action caused it.</p>
    {review.observation && <>
      <p>{review.observation.execution_acknowledged_at
        ? `Execution request acknowledged: ${review.observation.execution_acknowledged_at}.`
        : "Execution acknowledgement unavailable."} Checked: {review.observation.checked_at}.</p>
      {review.observation.applied_generation !== null && <p>Applied generation: <code>{review.observation.applied_generation}</code></p>}
      {review.observation.applied_template_sha256 && <p>Applied template SHA-256: <code>{review.observation.applied_template_sha256}</code></p>}
      <p>{review.observation.observation_window
        ? `Shared observation window (UTC): ${review.observation.observation_window[0]} — ${review.observation.observation_window[1]}.`
        : "No sustained shared recovery window has been verified."}</p>
      {review.observation.reason_codes.length > 0 && <ul>{review.observation.reason_codes.map(code =>
        <li key={code}><code>{code}</code> — {recoveryReasonLabels[code]}</li>)}</ul>}
      {review.observation.metric_evidence_ids.length > 0 && <p>Retained metric observations: {review.observation.metric_evidence_ids.map(id =>
        <a key={id} href={`#${evidenceAnchor(id)}`}>{id}{" "}</a>)}</p>}
    </>}
    {review.mode === "simulation" && <p>Simulation cannot confirm a live rollout or production SLI recovery. Recovery rechecking is disabled.</p>}
    {review.mode === "unknown" && <p>Legacy resolved status, missing acknowledgement or malformed recovery data is not recovery evidence. Review the trace; no action will be resubmitted.</p>}
    {review.mode === "pending" && <>
      <p>Recheck only on your explicit request. This queries read-only tools and saves an observation; it never repeats restart or rollback. There is no automatic polling.</p>
      {operatorToken === undefined && <>
        <label htmlFor={tokenInputId}>Operator token for recovery verification</label>
        <input id={tokenInputId} type="password" autoComplete="off" value={localToken} disabled={busy}
          onChange={event => setLocalToken(event.target.value)} aria-describedby={`${tokenInputId}-scope`} />
        <p id={`${tokenInputId}-scope`}>Token stays in this panel&apos;s memory, is not saved in browser storage, URL or trace, and is cleared after a successful check. It is sent only to the configured incident backend{endpoint ? <>: <code>{endpoint}</code></> : ". Connect a valid backend first."}</p>
      </>}
      {!review.canCheck && <p>Applied generation, template identity and execution acknowledgement are required before rechecking.</p>}
      <button type="button" disabled={busy || !endpoint || !review.canCheck || !validRecoveryOperatorToken(token)} onClick={() => void check()}>
        {busy ? "Verifying recovery…" : "Recheck recovery (read-only tools)"}
      </button>
    </>}
    <div role="status" aria-live="polite">{error && <p>{error}</p>}</div>
  </section>;
}
