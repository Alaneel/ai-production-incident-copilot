export type ServiceImpact = {
  service: string;
  status: string;
  evidence_ids: string[];
  overlaps_primary_window: boolean | null;
};

export type ServiceTopology = {
  service: string;
  edges: Array<{ source: string; target: string }>;
  source: string;
  topology_kind: string;
  catalog_status?: string;
  observed?: unknown;
};

export function topologyProjection(services: string[], raw: unknown) {
  const unavailable = { edges: [] as Array<{ source: string; target: string }>, provenance: "Topology source unknown", source: "Source not supplied", availability: "Catalog availability unknown" };
  if (!raw || typeof raw !== "object") return unavailable;
  const topology = raw as Partial<ServiceTopology>;
  if (topology.service !== services[0] || !Array.isArray(topology.edges) || topology.edges.length > 64) return unavailable;
  const validService = (name: unknown): name is string => typeof name === "string" && /^[a-z0-9][a-z0-9-]{1,79}$/.test(name);
  const seen = new Set<string>();
  const edges = topology.edges.filter((edge) => {
    if (!edge || typeof edge !== "object" || !validService(edge.source) || !validService(edge.target)) return false;
    if (edge.source === edge.target || ![edge.source, edge.target].includes(services[0])) return false;
    if (!services.includes(edge.source) || !services.includes(edge.target)) return false;
    const key = `${edge.source}:${edge.target}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  }).map(({ source, target }) => ({ source, target }));
  const source = topology.source === "service-catalog-via-telemetry-gateway" ? "Catalog via telemetry gateway"
    : topology.source === "service-catalog" ? "Service catalog"
    : topology.source === "legacy_adapter" ? "Name-only adapter" : "Source not supplied";
  return { edges, source, availability: topology.catalog_status === "unavailable" ? "Catalog query unavailable"
    : topology.catalog_status === "available" ? "Catalog data retained" : "Catalog availability unknown", provenance: topology.topology_kind === "static_fixture_catalog"
    ? "Static demo catalog · not live trace discovery" : "Topology source unknown" };
}

export function dependencyImpactRows(services: string[], impacts: ServiceImpact[], topology?: unknown) {
  const primary = services[0];
  const projected = topologyProjection(services, topology);
  return [...new Set(services.slice(1))].filter((service) => service !== primary).map((service) => {
    const matches = impacts.filter((impact) => impact.service === service);
    const impact = matches.length === 1 ? matches[0] : undefined;
    const status = impact?.status;
    const label = status === "observed_anomaly" ? "Observed anomaly"
      : status === "query_failed" ? "Query failed"
      : status === "unconfirmed" ? "Impact unconfirmed" : "Not assessed";
    const overlap = status === "observed_anomaly"
      ? impact?.overlaps_primary_window === true ? "Overlaps primary anomaly window"
        : impact?.overlaps_primary_window === false ? "Outside primary anomaly window" : "Window overlap unknown"
      : "No verified impact window";
    const upstream = projected.edges.some((edge) => edge.source === service && edge.target === primary);
    const downstream = projected.edges.some((edge) => edge.source === primary && edge.target === service);
    const direction = upstream && downstream ? "Bidirectional catalog relation"
      : upstream ? "Upstream caller (catalog)" : downstream ? "Downstream dependency (catalog)" : "Direction unknown";
    const paths = projected.edges.filter((edge) => edge.source === service || edge.target === service)
      .map((edge) => `${edge.source} → ${edge.target}`);
    return { service, label, overlap, direction, paths, anomalous: status === "observed_anomaly", evidenceIds: impact?.evidence_ids ?? [] };
  });
}
