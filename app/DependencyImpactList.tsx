import { dependencyImpactRows, topologyProjection, type ServiceImpact } from "./dependency-impacts";
import { evidenceAnchor, resolveCitations } from "./evidence-citations";
import { observedDirection, observedTopologyProjection } from "./observed-topology";

export function DependencyImpactList({ services, impacts, evidence, topology }: {
  services: string[];
  impacts: ServiceImpact[];
  evidence: Array<{ id: string }>;
  topology?: unknown;
}) {
  const rows = dependencyImpactRows(services, impacts, topology);
  const projected = topologyProjection(services, topology);
  const observed = observedTopologyProjection(services, topology);
  return (
    <div className="dependency-impact-list" aria-label="Observed dependency impacts">
      <p>Selected service: <strong>{services[0] ?? "Unavailable"}</strong>. Topology and time overlap do not establish causal direction.</p>
      <p className="topology-provenance">{projected.provenance}<br />{projected.source} · {projected.availability}. Arrows describe catalog calls, not incident propagation.</p>
      <div className="observed-topology-summary" aria-label="Trace topology observation scope">
        <strong>{observed.label}</strong>
        {observed.window && <p>Retained observation window (UTC): {observed.window}</p>}
        {observed.counts && <p>{observed.counts}</p>}
        <p>CLIENT → SERVER parentage shows a sampled call, not failure propagation or root cause.</p>
        {observed.status === "available" && observed.calls.length === 0 && <p>No qualifying call pairs in the retained sample. This does not prove no dependencies.</p>}
      </div>
      {rows.length === 0 && <p>No related service names retained. This does not prove no dependencies.</p>}
      {rows.map((row) => {
        const citations = resolveCitations(evidence, row.evidenceIds);
        return <article key={row.service} className={row.anomalous ? "dependency-impact anomalous" : "dependency-impact"}>
          <div><strong>{row.service}</strong><span>{row.label}</span></div>
          {observed.status === "available" && <p className="observed-direction">{observedDirection(services[0], row.service, observed.calls)}</p>}
          {observed.calls.filter(call => call.source === row.service || call.target === row.service).map(call => <details className="observed-call" key={`${call.trace_id}:${call.client_span_id}:${call.server_span_id}`}>
            <summary>{call.source} → {call.target} · trace {call.trace_id.slice(0, 8)}…</summary>
            <dl>
              <dt>Trace ID</dt><dd>{call.trace_id}</dd>
              <dt>Client span</dt><dd>{call.client_span_id}</dd>
              <dt>Server span</dt><dd>{call.server_span_id}</dd>
              <dt>Observed (UTC)</dt><dd>{call.observed_at}</dd>
            </dl>
          </details>)}
          <p className="dependency-direction">{row.direction}</p>
          {row.paths.map((path) => <p className="dependency-path" key={path}>{path}</p>)}
          <p>{row.overlap}</p>
          <div className="dependency-citations">
            {citations.cited.map((item) => <a key={item.id} href={`#${evidenceAnchor(item.id)}`}>{item.id}</a>)}
            {(citations.missing.length > 0 || citations.ambiguous.length > 0) && <span>Evidence reference unavailable or ambiguous</span>}
          </div>
        </article>;
      })}
    </div>
  );
}
