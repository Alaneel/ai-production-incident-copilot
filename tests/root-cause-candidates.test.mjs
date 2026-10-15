import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";
import * as candidates from "../app/root-cause-candidates.ts";
import * as citations from "../app/evidence-citations.ts";

const now = Date.now();
const iso = seconds => new Date(now + seconds * 1000).toISOString();
function metric(id, service, query, values = [0.95, 0.96]) {
  const unit = { request_latency_p95: "milliseconds", request_error_rate: "ratio", cpu_utilization: "ratio", db_pool_active_ratio: "ratio",
    memory_working_set: "bytes", kafka_consumer_lag: "messages", retry_rate: "retries_per_second", cache_miss_rate: "misses_per_second" }[query];
  return { id, service, kind: "metric", query, score: 0.9, observation: "Current retained signal", observed_at: iso(-10),
    attributes: { unit, series: values.map((value, index) => ({ timestamp: iso(-30 + index * 20), value })) } };
}
function topology(service, source, target, observed = false) {
  return { service, source: "service-catalog-via-telemetry-gateway", topology_kind: "static_fixture_catalog", catalog_status: "available",
    edges: observed ? [] : [{ source, target }], observed: observed ? {
      status: "available", source: "tempo_client_server_parentage", start: iso(-120), end: iso(0),
      coverage: "sampled_not_exhaustive", traces_read: 1, traces_failed: 0, calls_omitted: 0, calls_discarded: 0,
      calls: [{ source, target, trace_id: (source === "payment-service" ? "a" : "d").repeat(32),
        client_span_id: (source === "payment-service" ? "b" : "e").repeat(16), server_span_id: (source === "payment-service" ? "c" : "f").repeat(16), observed_at: iso(-20) }],
    } : { status: "not_supported" } };
}
function candidate(service = "risk-service", type = "cpu_saturation", path = ["payment-service", "risk-service"], basis = "catalog") {
  return { rank: 1, service, incident_type: type, local_evidence_ids: ["metric/risk-cpu"],
    symptom_evidence_ids: ["metric/payment-latency"], propagation_path: path, path_basis: basis,
    temporal_overlap: true, causal_direction_verified: false, validation_notes: ["private-provider-note <script>secret</script>"] };
}
function incident(basis = "catalog", deep = false) {
  return { services: deep ? ["payment-service", "ledger-service", "risk-service"] : ["payment-service", "risk-service"],
    complaint_service: "payment-service", diagnosis_service: "risk-service",
    service_topologies: deep ? [topology("payment-service", "payment-service", "ledger-service", basis === "observed_trace"),
      topology("ledger-service", "ledger-service", "risk-service", basis !== "catalog")]
      : [topology("payment-service", "payment-service", "risk-service", basis === "observed_trace")],
    evidence: [metric("metric/risk-cpu", "risk-service", "cpu_utilization"),
      metric("metric/payment-latency", "payment-service", "request_latency_p95", [2000, 2200])],
    root_cause_candidates: [candidate("risk-service", "cpu_saturation", deep
      ? ["payment-service", "ledger-service", "risk-service"] : ["payment-service", "risk-service"], basis)] };
}

test("catalog candidate separates complaint/source and never projects provider notes or confirmation", () => {
  const view = candidates.rootCauseCandidatesView(incident(), now);
  assert.equal(view.available, true);
  assert.equal(view.complaintService, "payment-service");
  assert.equal(view.diagnosisService, "risk-service");
  assert.equal(view.candidates[0].pathBasis, "catalog");
  assert.match(view.candidates[0].pathLabel, /catalog calls/);
  assert.match(view.candidates[0].temporalLabel, /correlation only/);
  assert.doesNotMatch(JSON.stringify(view), /private-provider|<script>|validation_notes/);
});

test("valid sampled two-hop and mixed paths stay distinct from catalog paths", () => {
  for (const basis of ["catalog", "observed_trace", "mixed"]) {
    const view = candidates.rootCauseCandidatesView(incident(basis, true), now);
    assert.equal(view.available, true, basis);
    assert.equal(view.candidates[0].pathBasis, basis);
    assert.deepEqual(view.candidates[0].path, ["payment-service", "ledger-service", "risk-service"]);
  }
});

test("primary cause retains complaint-local signals without inventing a cross-service path", () => {
  const state = incident();
  state.diagnosis_service = "payment-service";
  state.evidence[0].service = "payment-service";
  state.root_cause_candidates = [{ ...candidate("payment-service", "cpu_saturation", ["payment-service"], "primary"), temporal_overlap: null }];
  state.service_topologies = [];
  const view = candidates.rootCauseCandidatesView(state, now);
  assert.equal(view.available, true);
  assert.match(view.candidates[0].pathLabel, /no cross-service call path/);
  assert.match(view.candidates[0].temporalLabel, /not established/);
});

test("primary timeout is a symptom when a unique specific downstream candidate exists", () => {
  const state = incident();
  state.root_cause_candidates.push({ ...candidate("payment-service", "downstream_timeout", ["payment-service"], "primary"),
    rank: 2, local_evidence_ids: ["log/payment-timeout"] });
  state.evidence.push({ id: "log/payment-timeout", kind: "log", service: "payment-service", score: 0.9,
    observation: "Retained timeout log", observed_at: iso(-10), attributes: { events: [{ timestamp: iso(-20), message: "downstream request timed out" }] } });
  assert.equal(candidates.rootCauseCandidatesView(state, now).available, true);
});

test("competing concrete families on one service cannot justify a unique diagnosis selection", () => {
  const state = incident();
  state.root_cause_candidates.push({ ...candidate("risk-service", "database_connection_pool_exhaustion"), rank: 2,
    local_evidence_ids: ["metric/risk-pool"] });
  state.evidence.push(metric("metric/risk-pool", "risk-service", "db_pool_active_ratio"));
  assert.equal(candidates.rootCauseCandidatesView(state, now).available, false);
  state.diagnosis_service = null;
  const view = candidates.rootCauseCandidatesView(state, now);
  assert.equal(view.available, true);
  assert.equal(view.candidates.length, 2);
  assert.equal(view.diagnosisService, null);
});

test("split metric evidence retains contributors instead of requiring two points in every ID", () => {
  const state = incident();
  state.evidence[1].attributes.series = [{ timestamp: iso(-30), value: 2000 }];
  state.evidence.push({ ...structuredClone(state.evidence[1]), id: "metric/payment-latency-part2",
    attributes: { series: [{ timestamp: iso(-10), value: 2200 }] } });
  state.root_cause_candidates[0].symptom_evidence_ids.push("metric/payment-latency-part2");
  assert.equal(candidates.rootCauseCandidatesView(state, now).available, true);
});

test("legacy, malformed, unscoped, oversized or unsupported candidates fail the entire review closed", () => {
  const mutations = [s => delete s.complaint_service, s => s.complaint_service = "risk-service", s => s.services = [],
    s => s.services.push("payment-service"), s => s.services[0] += "\n", s => delete s.diagnosis_service,
    s => s.diagnosis_service = "payment-service", s => s.root_cause_candidates = null,
    s => s.root_cause_candidates.push(structuredClone(s.root_cause_candidates[0])),
    s => s.root_cause_candidates = Array.from({ length: 9 }, () => structuredClone(s.root_cause_candidates[0])),
    s => s.root_cause_candidates[0].rank = "1", s => s.root_cause_candidates[0].rank = true,
    s => s.root_cause_candidates[0].rank = 2, s => s.root_cause_candidates[0].service = "other-service",
    s => s.root_cause_candidates[0].incident_type = "invented_cause", s => s.root_cause_candidates[0].incident_type = ["cpu_saturation"],
    s => s.root_cause_candidates[0].local_evidence_ids = [], s => s.root_cause_candidates[0].local_evidence_ids.push("metric/risk-cpu"),
    s => s.root_cause_candidates[0].local_evidence_ids = ["missing"], s => s.root_cause_candidates[0].symptom_evidence_ids = [],
    s => s.root_cause_candidates[0].symptom_evidence_ids = ["metric/risk-cpu"], s => s.root_cause_candidates[0].propagation_path.reverse(),
    s => s.root_cause_candidates[0].propagation_path = ["payment-service", "payment-service", "risk-service"],
    s => s.root_cause_candidates[0].propagation_path = ["payment-service", "unknown-service", "risk-service"],
    s => s.root_cause_candidates[0].propagation_path = ["payment-service", "risk-service", "ledger-service", "risk-service"],
    s => s.root_cause_candidates[0].path_basis = "primary", s => s.root_cause_candidates[0].path_basis = "observed_trace",
    s => s.root_cause_candidates[0].temporal_overlap = null, s => s.root_cause_candidates[0].temporal_overlap = "true",
    s => s.root_cause_candidates[0].causal_direction_verified = true, s => s.root_cause_candidates[0].causal_direction_verified = 0,
    s => s.root_cause_candidates[0].validation_notes = "private-provider-text",
    s => s.evidence.push(structuredClone(s.evidence[0])), s => s.evidence[0].service = "payment-service",
    s => s.evidence[0].kind = "technical_doc", s => s.evidence[0].score = 0, s => s.evidence[0].score = "0.9",
    s => s.evidence[0].attributes.series = [], s => s.evidence[0].attributes.series[0].value = true,
    s => s.evidence[0].attributes.series[0].value = 1.2, s => s.evidence[0].observed_at = iso(-400),
    s => s.evidence[0].attributes.series[0].timestamp = iso(1), s => s.evidence[0].attributes.series[0].timestamp = "2026-02-30T12:00:00Z",
    s => s.evidence[1].attributes.series[0].value = 500, s => s.evidence[1].query = "cpu_utilization",
    s => s.diagnostic_excluded_evidence_ids = ["metric/risk-cpu"],
    s => s.diagnostic_excluded_evidence_ids = ["metric/payment-latency"],
    s => s.diagnostic_excluded_evidence_ids = "private-provider",
    s => s.service_topologies = [], s => s.service_topologies = null,
    s => s.service_topologies.push(structuredClone(s.service_topologies[0])),
    s => s.service_topologies[0].source = "private-provider-source", s => s.service_topologies[0].catalog_status = "unavailable",
    s => s.service_topologies[0].edges[0] = { source: "risk-service", target: "payment-service" }];
  for (const [index, mutate] of mutations.entries()) {
    const state = incident(); mutate(state);
    const view = candidates.rootCauseCandidatesView(state, now);
    assert.equal(view.available, false, `mutation ${index}`);
    assert.deepEqual(view.candidates, []);
    assert.equal(view.diagnosisService, null);
    assert.doesNotMatch(JSON.stringify(view), /private-provider|script/);
  }
  for (const state of [null, [], true, {}, "private"]) assert.equal(candidates.rootCauseCandidatesView(state, now).available, false);
});

test("stale, malformed, zero-ID or conflicting-direction trace calls cannot earn observed labels", () => {
  const changes = [s => s.service_topologies[0].observed.calls[0].observed_at = iso(-400),
    s => s.service_topologies[0].observed.calls[0].trace_id = "0".repeat(32),
    s => s.service_topologies[0].observed.calls[0].source = "risk-service",
    s => s.service_topologies[0].observed.calls[0].target = "payment-service",
    s => s.service_topologies[0].observed.calls[0].client_span_id = "c".repeat(16),
    s => s.service_topologies[0].observed.calls.push(structuredClone(s.service_topologies[0].observed.calls[0])),
    s => s.service_topologies[0].observed.source = "unknown",
    s => s.service_topologies[0].observed.traces_read = "1"];
  for (const mutate of changes) {
    const state = incident("observed_trace"); mutate(state);
    assert.equal(candidates.rootCauseCandidatesView(state, now).available, false);
  }
});

test("a simple selected path inside a feedback cycle cannot be presented as a downstream candidate", () => {
  const state = incident();
  state.service_topologies[0].edges.push({ source: "risk-service", target: "payment-service" });
  assert.equal(candidates.rootCauseCandidatesView(state, now).available, false);
});

test("a true overlap flag cannot replace actual overlapping current fault and SLI windows", () => {
  const state = incident();
  state.evidence[0].attributes.series.forEach((point, index) => point.timestamp = iso(-250 + index * 20));
  assert.equal(candidates.rootCauseCandidatesView(state, now).available, false);
  // Closed-window contact is allowed, but one microsecond of separation is not.
  const boundary = incident(), endpoint = iso(-30).replace(/\.\d{3}Z$/, ".123001Z");
  boundary.evidence[0].attributes.series = [{ timestamp: iso(-50), value: 0.95 }, { timestamp: endpoint, value: 0.96 }];
  boundary.evidence[1].attributes.series = [{ timestamp: endpoint, value: 2000 }, { timestamp: iso(-10), value: 2200 }];
  assert.equal(candidates.rootCauseCandidatesView(boundary, now).available, true);
  boundary.evidence[1].attributes.series[0].timestamp = endpoint.replace("123001Z", "123002Z");
  assert.equal(candidates.rootCauseCandidatesView(boundary, now).available, false);
});

test("local metric citations must support the exact family with truthful units and sustained thresholds", () => {
  const cases = [["cpu_saturation", "cpu_utilization", [0.8, 0.9], [0.81, 0.9]],
    ["database_connection_pool_exhaustion", "db_pool_active_ratio", [0.9, 0.95], [0.91, 0.95]],
    ["kafka_consumer_lag", "kafka_consumer_lag", [100, 150], [101, 150]],
    ["retry_storm", "retry_rate", [1, 2], [1.1, 2]], ["cache_penetration", "cache_miss_rate", [1, 2], [1.1, 2]]];
  for (const [family, query, healthy, faulty] of cases) {
    const state = incident(); state.root_cause_candidates[0].incident_type = family;
    state.evidence[0] = metric("metric/risk-cpu", "risk-service", query, faulty);
    assert.equal(candidates.rootCauseCandidatesView(state, now).available, true, family);
    state.evidence[0].attributes.unit = "arbitrary-units";
    assert.equal(candidates.rootCauseCandidatesView(state, now).available, false, `${family} units`);
    state.evidence[0] = metric("metric/risk-cpu", "risk-service", query, healthy);
    assert.equal(candidates.rootCauseCandidatesView(state, now).available, false, `${family} threshold`);
  }
  const cache = incident(); cache.root_cause_candidates[0].incident_type = "cache_penetration";
  cache.evidence[0] = metric("metric/risk-cpu", "risk-service", "cache_miss_rate", [5, 6]);
  cache.evidence[0].attributes.unit = "ratio";
  assert.equal(candidates.rootCauseCandidatesView(cache, now).available, false);
  const unrelated = incident(); unrelated.evidence[0] = metric("metric/risk-cpu", "risk-service", "request_latency_p95", [2000, 2200]);
  assert.equal(candidates.rootCauseCandidatesView(unrelated, now).available, false);
  const symptomUnits = incident(); symptomUnits.evidence[1].attributes.unit = "seconds";
  assert.equal(candidates.rootCauseCandidatesView(symptomUnits, now).available, false);
});

test("memory candidates require material sustained growth, not a high or healthy working set", () => {
  for (const [values, expected] of [[[10e6, 12e6, 14e6], true], [[10e6, 10e6, 14e6], false],
    [[10e6, 11e6, 10e6], false], [[10e6, 10.1e6, 10.2e6], false], [[100e6, 102e6, 104e6], false]]) {
    const state = incident(); state.root_cause_candidates[0].incident_type = "memory_leak";
    state.evidence[0] = metric("metric/risk-cpu", "risk-service", "memory_working_set", values);
    state.evidence[0].attributes.series.forEach((point, index) => point.timestamp = iso(-40 + index * 15));
    assert.equal(candidates.rootCauseCandidatesView(state, now).available, expected, String(values));
  }
});

test("log citations require a current family-specific signature and genuine overlapping event time", () => {
  for (const [family, signature] of [["database_connection_pool_exhaustion", "ACQUISITION TIMED OUT"],
    ["deployment_regression", "Invalid Ledger Response Shape"]]) {
    const state = incident(); state.root_cause_candidates[0].incident_type = family;
    state.evidence[0] = { id: "metric/risk-cpu", kind: "log", service: "risk-service", score: 0.9, observation: "Retained local log",
      observed_at: iso(-10), attributes: { events: [{ timestamp: iso(-20), message: signature }] } };
    assert.equal(candidates.rootCauseCandidatesView(state, now).available, true, family);
    state.evidence[0].attributes.events[0].message = "request completed";
    assert.equal(candidates.rootCauseCandidatesView(state, now).available, false);
    state.evidence[0].attributes.events[0] = { timestamp: iso(-200), message: signature };
    assert.equal(candidates.rootCauseCandidatesView(state, now).available, false);
  }
});

test("uncited newer green/conflicting points and unrelated context IDs cannot support a fault window", () => {
  for (const [time, value] of [[-5, 0.1], [-10, 0.1]]) {
    const state = incident(), extra = metric("metric/risk-cpu-context", "risk-service", "cpu_utilization", [value]);
    extra.attributes.series[0].timestamp = iso(time); state.evidence.push(extra);
    assert.equal(candidates.rootCauseCandidatesView(state, now).available, false);
  }
  const unrelated = incident(); unrelated.evidence.push(metric("healthy-pool", "risk-service", "db_pool_active_ratio", [0.1, 0.2]));
  unrelated.root_cause_candidates[0].local_evidence_ids.push("healthy-pool");
  assert.equal(candidates.rootCauseCandidatesView(unrelated, now).available, false);
  const gap = incident(); gap.evidence[0].attributes.series[0].timestamp = iso(-200);
  assert.equal(candidates.rootCauseCandidatesView(gap, now).available, false);
  const excluded = incident(); excluded.evidence.push(metric("excluded-green", "risk-service", "cpu_utilization", [0.1, 0.2]));
  excluded.diagnostic_excluded_evidence_ids = ["excluded-green"];
  assert.equal(candidates.rootCauseCandidatesView(excluded, now).available, true);
  excluded.diagnostic_excluded_evidence_ids.push("excluded-green");
  assert.equal(candidates.rootCauseCandidatesView(excluded, now).available, false);
});

test("every hop's observed support has priority and the retained path basis must match exactly", () => {
  const state = incident("observed_trace", true);
  state.service_topologies.forEach((item, index) => item.edges.push({ source: state.services[index], target: state.services[index + 1] }));
  for (const basis of ["catalog", "mixed", "observed_trace"]) {
    state.root_cause_candidates[0].path_basis = basis;
    assert.equal(candidates.rootCauseCandidatesView(state, now).available, basis === "observed_trace", basis);
  }
  const catalog = incident(); catalog.root_cause_candidates[0].path_basis = "observed_trace";
  assert.equal(candidates.rootCauseCandidatesView(catalog, now).available, false);
});

test("cross-topology identity conflicts remove sampled support independently of ordering, with catalog fallback", () => {
  for (const field of ["source", "target", "observed_at"]) for (const reverse of [false, true]) {
    const state = incident("observed_trace"), first = state.service_topologies[0];
    const other = structuredClone(first); other.service = "risk-service";
    if (field === "source" || field === "target") {
      other.observed.calls[0].source = "risk-service"; other.observed.calls[0].target = "payment-service";
    } else other.observed.calls[0].observed_at = iso(-19);
    state.service_topologies.push(other); if (reverse) state.service_topologies.reverse();
    assert.equal(candidates.rootCauseCandidatesView(state, now).available, false, `${field}:${reverse}`);
    first.edges.push({ source: "payment-service", target: "risk-service" }); state.root_cause_candidates[0].path_basis = "catalog";
    assert.equal(candidates.rootCauseCandidatesView(state, now).available, true, `fallback ${field}:${reverse}`);
  }
  const duplicate = incident("observed_trace"), second = structuredClone(duplicate.service_topologies[0]); second.service = "risk-service";
  second.observed.calls[0].observed_at = duplicate.service_topologies[0].observed.calls[0].observed_at.replace("Z", "+00:00");
  duplicate.service_topologies.push(second);
  assert.equal(candidates.rootCauseCandidatesView(duplicate, now).available, true);
  duplicate.service_topologies[0].observed.calls[0].observed_at = iso(-20).replace(/\.\d{3}Z$/, ".123001Z");
  second.observed.calls[0].observed_at = iso(-20).replace(/\.\d{3}Z$/, ".123002Z");
  assert.equal(candidates.rootCauseCandidatesView(duplicate, now).available, false);
});

test("explicit empty candidates remain no evidence, not proof of healthy dependencies", () => {
  const state = incident(); state.root_cause_candidates = []; state.diagnosis_service = null;
  const view = candidates.rootCauseCandidatesView(state, now);
  assert.equal(view.available, true);
  assert.equal(view.candidates.length, 0);
});

const source = await readFile(new URL("../app/RootCauseCandidates.tsx", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText;
const nativeRequire = createRequire(import.meta.url), exports = {};
new Function("require", "exports", compiled)(name => name === "./root-cause-candidates" ? candidates
  : name === "./evidence-citations" ? citations : nativeRequire(name), exports);
const render = state => renderToStaticMarkup(React.createElement(exports.RootCauseCandidates, { incident: state, now }));

test("actual React candidate component renders scoped references and explicit noncausal caveats without controls", () => {
  const html = render(incident("mixed", true));
  assert.match(html, /aria-label="Candidate cause services"/);
  assert.match(html, /Complaint service: <strong>payment-service/);
  assert.match(html, /Selected candidate source: <strong>risk-service/);
  assert.match(html, /not a confirmed root cause/);
  assert.match(html, /payment-service → ledger-service → risk-service/);
  assert.match(html, /Catalog and sampled-call support/);
  assert.match(html, /href="#evidence-metric%2Frisk-cpu"/);
  assert.match(html, /href="#evidence-metric%2Fpayment-latency"/);
  assert.match(html, /does not authorize remediation/);
  assert.doesNotMatch(html, /private-provider|secret|<script>|<button|<input|<form/);
});

test("actual component keeps old/unknown and empty reviews distinct without creating confirmation", () => {
  const legacy = render({ services: ["payment-service"] });
  assert.match(legacy, /Candidate review unavailable or inconsistent/);
  assert.doesNotMatch(legacy, /Selected candidate source|Candidate 1/);
  const state = incident(); state.root_cause_candidates = []; state.diagnosis_service = null;
  const empty = render(state);
  assert.match(empty, /No sufficiently supported candidate retained/);
  assert.match(empty, /does not prove that dependencies are healthy/);
});

test("actual component escapes evidence IDs while service names remain strictly scoped", () => {
  const state = incident(); state.evidence[0].id = "<script>id</script>";
  state.root_cause_candidates[0].local_evidence_ids[0] = state.evidence[0].id;
  const html = render(state);
  assert.match(html, /&lt;script&gt;id&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script>/);
});

test("incident console integrates candidates only from actual retained incident state", async () => {
  const console = await readFile(new URL("../app/IncidentConsole.tsx", import.meta.url), "utf8");
  assert.match(console, /incident && <RootCauseCandidates incident=\{incident\}/);
  assert.match(console, /complaint_service\?: string \| null/);
  assert.match(console, /diagnosis_service\?: string \| null/);
});
