import { actionReviewView } from "./action-review";
import { evidenceAnchor } from "./evidence-citations";

type Input = Parameters<typeof actionReviewView>[0];

export function ActionEvidenceReview({ incident }: { incident: Input }) {
  const review = actionReviewView(incident);
  return <section className="action-evidence-review" aria-label="Action evidence review">
    <h3>Review before changing production</h3>
    <p>{review.signalLabel}</p>
    <p>{review.familyLabel}</p>
    <p><strong>Semantic entailment, release causation and mitigation efficacy are not verified.</strong> Citation integrity, a valid revision and a successful dry run do not establish that this action will fix the incident.</p>
    {review.evidenceIds.length > 0 && <p>Retained primary-service signal citations: {review.evidenceIds.map(id =>
      <a key={id} href={`#${evidenceAnchor(id)}`}>{id}{" "}</a>)}</p>}
    <p>This panel summarizes a retained proposal check, not current execution authorization. The backend rechecks evidence and approval at dispatch. Review the full hypothesis and runbook; reject the action if its rationale remains unresolved.</p>
  </section>;
}
