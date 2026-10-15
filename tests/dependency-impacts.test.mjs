import assert from "node:assert/strict";
import test from "node:test";
import { dependencyImpactRows, topologyProjection } from "../app/dependency-impacts.ts";

test("dependency view contains actual deduplicated services, no invented placeholders", () => {
  assert.deepEqual(dependencyImpactRows(["payment-service"], []), []);
  const rows = dependencyImpactRows(["payment-service", "checkout-api", "payment-service", "checkout-api", "other"], []);
  assert.deepEqual(rows.map((row) => row.service), ["checkout-api", "other"]);
  assert.ok(rows.every((row) => row.label === "Not assessed"));
});

test("observations, failures and unknown data remain distinct and never imply health or causality", () => {
  const rows = dependencyImpactRows(["primary", "overlap", "outside", "missing", "failed", "empty"], [
    { service: "overlap", status: "observed_anomaly", overlaps_primary_window: true, evidence_ids: ["ev-1"] },
    { service: "outside", status: "observed_anomaly", overlaps_primary_window: false, evidence_ids: ["ev-2"] },
    { service: "missing", status: "observed_anomaly", overlaps_primary_window: null, evidence_ids: [] },
    { service: "failed", status: "query_failed", overlaps_primary_window: null, evidence_ids: [] },
    { service: "empty", status: "unconfirmed", overlaps_primary_window: null, evidence_ids: [] },
  ]);
  assert.deepEqual(rows.map((row) => row.overlap), ["Overlaps primary anomaly window", "Outside primary anomaly window", "Window overlap unknown", "No verified impact window", "No verified impact window"]);
  assert.equal(rows[3].label, "Query failed");
  assert.equal(rows[4].label, "Impact unconfirmed");
  assert.deepEqual(rows[0].evidenceIds, ["ev-1"]);
});

test("duplicate assessments and unknown statuses cannot become a verified anomaly", () => {
  const impact = { service: "dependency", status: "observed_anomaly", overlaps_primary_window: true, evidence_ids: [] };
  assert.equal(dependencyImpactRows(["primary", "dependency"], [impact, impact])[0].label, "Not assessed");
  assert.equal(dependencyImpactRows(["primary", "dependency"], [{ ...impact, status: "healthy" }])[0].anomalous, false);
});

const services = ["payment-service", "checkout-api", "ledger-service", "risk-engine"];
const topology = {
  service: "payment-service", source: "service-catalog-via-telemetry-gateway", topology_kind: "static_fixture_catalog",
  edges: [{ source: "checkout-api", target: "payment-service" },
    { source: "payment-service", target: "ledger-service" },
    { source: "payment-service", target: "risk-engine" },
    { source: "risk-engine", target: "payment-service" }],
};

test("catalog arrows distinguish upstream, downstream and bidirectional without implying impact", () => {
  const rows = dependencyImpactRows(services, [], topology);
  assert.deepEqual(rows.map(row => row.direction), ["Upstream caller (catalog)", "Downstream dependency (catalog)", "Bidirectional catalog relation"]);
  assert.deepEqual(rows[0].paths, ["checkout-api → payment-service"]);
  assert.equal(rows[2].paths.length, 2);
  assert.ok(rows.every(row => row.label === "Not assessed" && !row.anomalous));
  assert.equal(topologyProjection(services, topology).provenance, "Static demo catalog · not live trace discovery");
});

test("old name-only incidents never gain arrows from assessment relation claims", () => {
  const rows = dependencyImpactRows(services, [{ service: "checkout-api", relation: "upstream_catalog_dependency", status: "unconfirmed", evidence_ids: [] }]);
  assert.ok(rows.every(row => row.direction === "Direction unknown" && row.paths.length === 0));
  assert.equal(topologyProjection(services, undefined).provenance, "Topology source unknown");
});

test("cross-incident and oversized topology fail closed", () => {
  for (const raw of [null, "bad", { ...topology, service: "other-service" }, { ...topology, edges: {} }, { ...topology, edges: Array(65).fill(topology.edges[0]) }]) {
    assert.deepEqual(topologyProjection(services, raw).edges, []);
    assert.ok(dependencyImpactRows(services, [], raw).every(row => row.direction === "Direction unknown"));
  }
});

test("unsafe, unrelated, duplicate and self edges are discarded without retaining arbitrary fields", () => {
  const raw = { ...topology, edges: [
    { ...topology.edges[0], private_note: "secret", p95_ms: 9999 }, topology.edges[0], null,
    { source: "checkout-api", target: "ledger-service" },
    { source: "payment-service", target: "unknown-service" },
    { source: "payment-service", target: "payment-service" },
    { source: "payment-service", target: "<unsafe>" },
  ] };
  assert.deepEqual(topologyProjection(services, raw).edges, [topology.edges[0]]);
});

test("unrecognized provenance is never upgraded to live discovery or rendered verbatim", () => {
  const projected = topologyProjection(services, { ...topology, source: "private origin", topology_kind: "live_verified" });
  assert.equal(projected.source, "Source not supplied");
  assert.equal(projected.provenance, "Topology source unknown");
  assert.equal(topologyProjection(services, { ...topology, source: "legacy_adapter", edges: [], topology_kind: "unknown" }).source, "Name-only adapter");
});

test("observed time overlap does not change the meaning of catalog direction", () => {
  const row = dependencyImpactRows(services, [{ service: "checkout-api", status: "observed_anomaly", overlaps_primary_window: true, evidence_ids: ["ev-1"], causal_direction_verified: true }], topology)[0];
  assert.equal(row.direction, "Upstream caller (catalog)");
  assert.equal(row.overlap, "Overlaps primary anomaly window");
  assert.deepEqual(row.evidenceIds, ["ev-1"]);
});
