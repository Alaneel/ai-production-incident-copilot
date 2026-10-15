import { evidenceAnchor, sourceLink } from "./evidence-citations";

type Item = {
  id: string; kind: string; source: string; title: string; observation: string;
  observed_at: string; service?: string; query: string | null;
  attributes: Record<string, unknown>;
};

export function EvidenceExplorer({ evidence, citedIds }: { evidence: Item[]; citedIds: string[] }) {
  const cited = new Set(citedIds);
  return (
    <section className="panel evidence-explorer" aria-label="Retained evidence explorer">
      <div className="panel-title">
        <div><span className="eyebrow">AUDITABLE EVIDENCE</span><h2>All retained observations</h2></div>
        <span className="small-status">{evidence.length} items</span>
      </div>
      <p>Cited items support the leading hypothesis. Context-only items were collected but are not its citations.</p>
      {evidence.map((item, index) => {
        const link = sourceLink(item.source);
        return (
          <details key={`${item.id}-${index}`} id={evidence.filter(entry => entry.id === item.id).length === 1 ? evidenceAnchor(item.id) : `${evidenceAnchor(item.id)}-${index}`}>
            <summary><code>{item.id}</code><b>{cited.has(item.id) ? "CITED" : "CONTEXT ONLY"}</b><strong>{item.title}</strong></summary>
            <dl>
              <dt>Kind</dt><dd>{item.kind}</dd>
              <dt>Source</dt><dd>{link ? <a href={link} target="_blank" rel="noopener noreferrer">{item.source}</a> : item.source}</dd>
              <dt>Service</dt><dd>{item.service ?? "Not recorded"}</dd>
              <dt>Observed at</dt><dd><time>{item.observed_at}</time></dd>
              <dt>Query</dt><dd>{item.query ?? "Not recorded"}</dd>
            </dl>
            <p>{item.observation}</p>
            <pre aria-label={`Retained attributes for ${item.id}`}>{JSON.stringify(item.attributes, null, 2)}</pre>
          </details>
        );
      })}
      {evidence.length === 0 && <p>No evidence was retained.</p>}
    </section>
  );
}
