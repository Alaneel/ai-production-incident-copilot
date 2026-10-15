import assert from "node:assert/strict";
import test from "node:test";
import { observedTopologyProjection, observedDirection } from "../app/observed-topology.ts";
import { topologyProjection } from "../app/dependency-impacts.ts";

const services = ["payment-service", "checkout-api", "ledger-service"];
function fixture() {
  return { service: "payment-service", edges: [], source: "service-catalog-via-telemetry-gateway", catalog_status: "unavailable",
    observed: { status: "available", source: "tempo_client_server_parentage", coverage: "sampled_not_exhaustive",
      start: "2026-10-01T10:00:00Z", end: "2026-10-01T10:30:00Z", traces_read: 1, traces_failed: 0, calls_omitted: 0, calls_discarded: 0,
      calls: [{ source: "checkout-api", target: "payment-service", trace_id: "1".repeat(32), client_span_id: "2".repeat(16), server_span_id: "3".repeat(16), observed_at: "2026-10-01T10:15:00Z", private_note: "do not expose" }],
    } };
}

test("retained sample exposes safe trace/span references and historical UTC window", () => {
  const view = observedTopologyProjection(services, fixture());
  assert.equal(view.status, "available");
  assert.equal(view.calls[0].trace_id, "1".repeat(32));
  assert.match(view.window, /2026-10-01T10:00:00.000Z/);
  assert.match(view.label, /not exhaustive/);
  assert.doesNotMatch(JSON.stringify(view), /private_note|do not expose/);
});

test("actual upstream/downstream and bidirectional directions remain sampled calls", () => {
  const call = observedTopologyProjection(services, fixture()).calls[0];
  assert.equal(observedDirection("payment-service", "checkout-api", [call]), "Observed upstream caller (sample)");
  const reverse = { ...call, source: "payment-service", target: "checkout-api" };
  assert.equal(observedDirection("payment-service", "checkout-api", [reverse]), "Observed downstream dependency (sample)");
  assert.equal(observedDirection("payment-service", "checkout-api", [call, reverse]), "Observed bidirectional calls (sample)");
  assert.match(observedDirection("payment-service", "ledger-service", [call]), /sample is not exhaustive/);
});

test("partial failed reads and omitted/discarded counters remain visible", () => {
  const raw = fixture();
  Object.assign(raw.observed, { coverage: "partial_sample_not_exhaustive", traces_failed: 2, calls_omitted: 5, calls_discarded: 3 });
  const view = observedTopologyProjection(services, raw);
  assert.match(view.label, /Partial/);
  assert.match(view.counts, /2 failed.*5 call pairs omitted.*3 discarded/);
});

test("empty samples are not unavailable, exhaustive or proof of no dependencies", () => {
  const raw = fixture();
  Object.assign(raw.observed, { calls: [], traces_read: 0 });
  const view = observedTopologyProjection(services, raw);
  assert.equal(view.status, "available");
  assert.deepEqual(view.calls, []);
  assert.match(view.label, /not exhaustive/);
});

test("unsupported, failed and old incidents cannot gain provider-supplied call claims", () => {
  assert.equal(observedTopologyProjection(services, null).status, "not_requested");
  assert.equal(observedTopologyProjection(services, { service: services[0] }).status, "not_requested");
  for (const status of ["not_supported", "unavailable", "not_requested", "unrecognized"]) {
    const raw = fixture(); raw.observed.status = status;
    const view = observedTopologyProjection(services, raw);
    assert.deepEqual(view.calls, []);
    assert.equal(view.window, null);
  }
});

test("wrong primary/source/coverage, malformed counts and oversized data fail closed", () => {
  for (const mutate of [
    raw => raw.service = "other-service", raw => raw.observed.source = "private provider origin",
    raw => raw.observed.coverage = "exhaustive", raw => raw.observed.traces_read = true,
    raw => raw.observed.traces_failed = 8, raw => raw.observed.calls_omitted = -1,
    raw => raw.observed.calls = Array(65).fill(raw.observed.calls[0]),
    raw => raw.observed.end = "2026-10-01T11:00:00Z", raw => raw.observed.start = "2026-10-01T10:00:00",
  ]) {
    const raw = fixture(); mutate(raw);
    assert.equal(observedTopologyProjection(services, raw).status, "unavailable");
  }
});

test("invalid/unknown/self/unrelated calls and unsafe identifiers are removed", () => {
  for (const mutate of [
    call => call.trace_id = "https://private.example", call => call.client_span_id = "0".repeat(16),
    call => call.server_span_id = call.client_span_id, call => call.target = call.source,
    call => call.source = "unknown-service", call => call.target = "ledger-service",
    call => call.observed_at = "2026-10-01T12:00:00Z",
  ]) {
    const raw = fixture(); mutate(raw.observed.calls[0]);
    const view = observedTopologyProjection(services, raw);
    assert.deepEqual(view.calls, []);
    assert.match(view.counts, /1 discarded/);
  }
});

test("duplicate conflicting identities never produce first-winner arrows", () => {
  const raw = fixture();
  raw.observed.calls.push({ ...raw.observed.calls[0], source: "payment-service", target: "ledger-service" });
  assert.deepEqual(observedTopologyProjection(services, raw).calls, []);
  raw.observed.calls = [raw.observed.calls[0], { ...raw.observed.calls[0], trace_id: "4".repeat(32) }];
  assert.equal(observedTopologyProjection(services, raw).status, "unavailable");
});

test("catalog failure does not discard observed calls or upgrade catalog provenance", () => {
  const raw = fixture();
  assert.equal(topologyProjection(services, raw).availability, "Catalog query unavailable");
  assert.equal(topologyProjection(services, raw).provenance, "Topology source unknown");
  assert.equal(observedTopologyProjection(services, raw).calls.length, 1);
});
