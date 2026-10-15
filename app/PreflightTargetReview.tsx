import { preflightTargetReview } from "./preflight-target-review";

export function PreflightTargetReview({ incident }: { incident: unknown }) {
  const review = preflightTargetReview(incident);
  return <section className="action-evidence-review" aria-label="Preflight and approval target review">
    <h3>Preflight and approval target</h3>
    <p><strong>{review.label}</strong></p>
    <p>{review.detail}</p>
    {review.simulationScope && <p>Simulated scope: <code>{review.simulationScope.namespace}/{review.simulationScope.service}</code>
      {review.simulationScope.revision && <> · revision <code>{review.simulationScope.revision}</code></>}. No live Deployment UID is available.</p>}
    {review.target && <>
      <p>Namespace: <code>{review.target.namespace}</code> · Deployment: <code>{review.target.service}</code></p>
      <p>Deployment UID: <code>{review.target.deployment_uid}</code></p>
      <p>Observed resource version: <code>{review.target.resource_version}</code> — controllers may update it after preflight.</p>
      <p>API origin / CA configuration fingerprint: <code>{review.target.cluster_context}</code>. This is not a Kubernetes cluster UID.</p>
      <p>Deployment template SHA-256: <code>{review.target.deployment_template_sha256}</code></p>
      {review.rollback && <>
        <p>Target revision: <code>{review.target.revision}</code> · ReplicaSet UID: <code>{review.target.replicaset_uid}</code></p>
        <p>Target ReplicaSet template SHA-256: <code>{review.target.rollback_template_sha256}</code></p>
      </>}
    </>}
    {review.rollback && <p>Rollback scope is container images only, not a full release or PodTemplate undo.</p>}
    <p>This display does not authorize execution or confirm recovery. The backend rechecks the approval and target at dispatch.</p>
  </section>;
}
