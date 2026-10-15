import { deploymentEventView, eventStatusLabels } from "./deployment-events";
import { evidenceAnchor } from "./evidence-citations";

type Evidence = { id: string; kind: string; service?: string; attributes: Record<string, unknown> };

export function DeploymentEventTimeline({ services, evidence, window }: {
  services: string[]; evidence: Evidence[]; window: [string, string] | null;
}) {
  const view = deploymentEventView(services, evidence, window);
  return <section className="panel deployment-event-panel" aria-label="Deployment event observations">
    <div className="panel-title">
      <div><span className="eyebrow">TEMPORAL CONTEXT · NOT ROOT CAUSE</span><h2>Deployment event observations</h2></div>
      <span className="small-status">{view.rows.length} usable observations</span>
    </div>
    <p role="status">{eventStatusLabels[view.status]}</p>
    <p>{view.window ? <>Sampled anomaly window (UTC): <time>{view.window[0]}</time> — <time>{view.window[1]}</time>.</>
      : "No usable sampled anomaly window retained."}</p>
    <p>Event times are first/latest observations, not rollout completion. Interval overlap does not prove an individual occurrence inside the window, a release revision or causation. This context does not authorize remediation.</p>
    {view.rows.length > 0 && <div className="deployment-event-table"><table>
      <caption>Retained observations compared with sampled metrics · UTC</caption>
      <thead><tr><th scope="col">Event / evidence</th><th scope="col">First / latest observation</th><th scope="col">Temporal relationship</th></tr></thead>
      <tbody>{view.rows.map(row => <tr key={row.evidenceId}>
        <th scope="row"><strong>{row.reason}</strong><a href={`#${evidenceAnchor(row.evidenceId)}`}>{row.evidenceId}</a></th>
        <td data-label="First / latest observation"><span>First: {row.first ? <time>{row.first}</time> : "Unknown"}</span><span>Latest: {row.last ? <time>{row.last}</time> : "Unknown"}</span></td>
        <td data-label="Temporal relationship">{row.relation}</td>
      </tr>)}</tbody>
    </table></div>}
    {view.omitted > 0 && <p role="alert">{view.omitted} event evidence items cannot be uniquely validated for this view. Inspect retained evidence and trace; no temporal claim is made for these items.</p>}
    <p>Events may expire, be filtered or fail to load. Absence of retained events does not prove that no deployment occurred.</p>
  </section>;
}
