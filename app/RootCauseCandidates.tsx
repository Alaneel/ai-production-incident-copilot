import { rootCauseCandidatesView } from "./root-cause-candidates";
import { evidenceAnchor } from "./evidence-citations";

export function RootCauseCandidates({ incident, now }: { incident: unknown; now?: number }) {
  const review = rootCauseCandidatesView(incident, now);
  return <section className="panel" aria-label="Candidate cause services">
    <div className="panel-title"><div><span className="eyebrow">CROSS-SERVICE INVESTIGATION</span><h2>Candidate cause services</h2></div></div>
    <p>A candidate is an investigation hypothesis, not a confirmed root cause. A single candidate, time overlap or a directed call does not establish causal failure propagation. This panel does not authorize remediation.</p>
    {!review.available ? <p>Candidate review unavailable or inconsistent. Legacy or stale data is not current causal confirmation; no service is automatically promoted to confirmed root cause.</p> : <>
      <p>Complaint service: <strong>{review.complaintService}</strong>.</p>
      <p>{review.diagnosisService ? <>Selected candidate source: <strong>{review.diagnosisService}</strong> — not a confirmed root cause.</>
        : "No unique candidate source selected; do not infer a confirmed cause from ordering."}</p>
      {review.candidates.length === 0 && <p>No sufficiently supported candidate retained. This does not prove that dependencies are healthy or exclude a downstream cause.</p>}
      {review.candidates.map(candidate => <article className="action-evidence-review" key={`${candidate.service}:${candidate.incidentType}`}>
        <h3>Candidate {candidate.rank}: {candidate.service}</h3>
        <p>{candidate.familyLabel}</p>
        <p>Downstream call path (not verified failure propagation): <code>{candidate.path.join(" → ")}</code></p>
        <p>{candidate.pathLabel}</p>
        <p>{candidate.temporalLabel}</p>
        <p>Candidate-service signals: {candidate.localEvidenceIds.map(id => <a key={id} href={`#${evidenceAnchor(id)}`}>{id}{" "}</a>)}</p>
        {candidate.symptomEvidenceIds.length > 0 && <p>Complaint-service symptom signals: {candidate.symptomEvidenceIds.map(id =>
          <a key={id} href={`#${evidenceAnchor(id)}`}>{id}{" "}</a>)}</p>}
        <p>Retained references support investigation scope only. Semantic entailment, release causation and mitigation efficacy are not verified; review the evidence rather than treating this candidate as an execution target.</p>
      </article>)}
    </>}
  </section>;
}
