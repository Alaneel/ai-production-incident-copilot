import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";
import * as recovery from "../app/recovery-verification.ts";
import * as citations from "../app/evidence-citations.ts";
import { rootCauseCandidatesView } from "../app/root-cause-candidates.ts";

const ack = "2026-10-07T14:00:00+00:00", checked = "2026-10-07T14:01:00+00:00";
function metric(query, id, unit) {
  return { id, kind: "metric", service: "payment-service", source: "prometheus-via-telemetry-gateway",
    title: `${query} samples`, observation: "Bounded post-execution observation", observed_at: "2026-10-07T14:00:45+00:00",
    query, attributes: { unit, data_origin: "prometheus_api", discarded_samples: 0,
      series: ["2026-10-07T14:00:10+00:00", "2026-10-07T14:00:25+00:00", "2026-10-07T14:00:45+00:00"]
        .map(timestamp => ({ timestamp, value: query === "request_latency_p95" ? 200 : 0.01 })) } };
}
function incident(status = "pending", simulation = false) {
  return { incident_id: "inc-recovery", trace_id: "trace-1", question: "Why is payment slow?",
    status: status === "verified" ? "resolved" : "remediation_pending", current_step: "wait_for_recovery",
    incident_type: "cpu_saturation", severity: "SEV-2", created_at: "2026-10-07T13:50:00+00:00",
    anomalous_window: null, services: ["payment-service"], hypotheses: [],
    remediation: { title: "Restart payment", tool: "restart_deployment", command_preview: "Preview only",
      expected_result: "Recovery must be verified", requires_approval: true, dry_run_passed: true,
      preflight_mode: simulation ? "simulation" : "kubernetes-server-dry-run",
      arguments: { service: "payment-service", namespace: "prod-sg", target_context: simulation ? null : {
        tool: "restart_deployment", service: "payment-service", namespace: "prod-sg", cluster_context: "b".repeat(64),
        deployment_uid: "deployment-owned", resource_version: "1234", deployment_template_sha256: "c".repeat(64),
        revision: null, replicaset_uid: null, rollback_template_sha256: null,
      } } },
    evidence: [metric("request_latency_p95", "metric/latency", "milliseconds"), metric("request_error_rate", "metric-error", "ratio")],
    trace: [{ sequence: 30, step: "verify_recovery", event: "recovery_verification", duration_ms: 4,
      payload: { read_only: true } }],
    recovery: { status, simulation, checked_at: checked,
      reason_codes: [status === "verified" ? "rollout_and_sli_recovery_observed_not_causal_proof"
        : simulation ? "simulation_or_unknown_backend" : "deployment_rollout_incomplete"],
      metric_evidence_ids: status === "verified" ? ["metric/latency", "metric-error"] : [],
      observation_window: status === "verified" ? ["2026-10-07T14:00:10+00:00", "2026-10-07T14:00:45+00:00"] : null,
      execution_acknowledged_at: simulation ? null : ack,
      applied_generation: simulation ? null : 3, applied_template_sha256: simulation ? null : "a".repeat(64),
      deployment_observation: status === "verified" ? { source: "kubernetes_deployment_health", read_only: true, marker_status: "marker_matches",
        observed_at: "2026-10-07T14:00:45+00:00", service: "payment-service", namespace: "prod-sg", cluster_context: "b".repeat(64),
        deployment_uid: "deployment-owned", generation: 3, template_sha256: "a".repeat(64), observed_generation: 3,
        desired_replicas: 2, total_replicas: 2, updated_replicas: 2, ready_replicas: 2, available_replicas: 2, unavailable_replicas: 0 } : {} } };
}

test("pending real recovery requires an acknowledgement and applied identity before recheck", () => {
  const view = recovery.recoveryVerificationView(incident());
  assert.equal(view.mode, "pending");
  assert.equal(view.canCheck, true);
  assert.match(view.label, /recovery verification pending/);
  for (const field of ["execution_acknowledged_at", "applied_generation", "applied_template_sha256"]) {
    const state = incident(); state.recovery[field] = null;
    const missing = recovery.recoveryVerificationView(state);
    assert.equal(missing.mode, "pending");
    assert.equal(missing.canCheck, false);
  }
});

test("verified recovery projects only scoped rollout/SLI observation, never causal proof", () => {
  const state = incident("verified");
  state.recovery.provider_note = "private-provider-diagnostic";
  state.recovery.deployment_observation.provider_note = "private-provider-diagnostic";
  const view = recovery.recoveryVerificationView(state);
  assert.equal(view.mode, "verified");
  assert.equal(view.canCheck, false);
  assert.match(view.label, /Applied rollout and sustained SLI recovery observed/);
  assert.doesNotMatch(JSON.stringify(view), /private-provider/);
});

test("simulation and legacy resolved cannot masquerade as verified live recovery", () => {
  const simulated = recovery.recoveryVerificationView(incident("verified", true));
  assert.equal(simulated.mode, "simulation");
  assert.equal(simulated.canCheck, false);
  assert.match(simulated.label, /live recovery is not verified/);
  const legacy = incident("verified"); delete legacy.recovery;
  assert.equal(recovery.recoveryVerificationView(legacy).mode, "unknown");
  const wrongBackend = incident("verified"); wrongBackend.remediation.preflight_mode = "simulation";
  assert.equal(recovery.recoveryVerificationView(wrongBackend).mode, "unknown");
});

test("malformed, cross-scope or insufficient recovery data never becomes verified", () => {
  const mutations = [
    s => s.recovery = [], s => s.recovery.status = "resolved", s => s.recovery.status = ["verified"], s => s.status = "remediation_pending",
    s => s.recovery.simulation = "false", s => delete s.recovery.simulation,
    s => s.recovery.checked_at = "2026-10-07 14:01:00", s => s.recovery.checked_at = "2026-10-07T15:01:00+01:00",
    s => s.recovery.checked_at = "2026-02-30T14:01:00Z", s => s.recovery.checked_at += "\n",
    s => s.recovery.execution_acknowledged_at = null, s => s.recovery.execution_acknowledged_at = "2026-10-07T15:00:00Z",
    s => s.recovery.applied_generation = true, s => s.recovery.applied_generation = 0,
    s => s.recovery.applied_generation = Number.MAX_SAFE_INTEGER + 1,
    s => s.recovery.applied_template_sha256 = "A".repeat(64), s => s.recovery.applied_template_sha256 += "\n",
    s => s.recovery.reason_codes = ["private_provider_diagnostic"], s => s.recovery.reason_codes = ["metric_threshold_failed"],
    s => s.recovery.reason_codes = [], s => s.recovery.metric_evidence_ids = [],
    s => s.recovery.metric_evidence_ids.push("metric-error"), s => s.recovery.metric_evidence_ids = ["missing", "metric-error"],
    s => s.evidence.push(structuredClone(s.evidence[0])), s => s.evidence[0].kind = "log",
    s => s.evidence[0].service = "ledger-service", s => s.evidence[0].source = "fixture",
    s => s.evidence[0].attributes.data_origin = "synthetic", s => s.evidence[0].attributes.unit = "seconds",
    s => s.evidence[1].query = "request_latency_p95", s => s.evidence[1].attributes.unit = "milliseconds",
    s => s.evidence[1].query = ["request_error_rate"], s => s.remediation.tool = ["restart_deployment"],
    s => s.evidence[0].attributes.series = [], s => s.evidence[0].attributes.series = null,
    s => s.evidence[0].attributes.series[0] = null, s => s.evidence[0].attributes.series[0].value = true,
    s => s.evidence[0].attributes.series[0].value = 1001,
    s => s.evidence[1].attributes.series[0].value = 0.051,
    s => s.evidence[0].attributes.series[0].timestamp = "2026-10-07T14:00:46Z",
    s => s.evidence[0].attributes.series[0].timestamp = "2026-10-07T14:01:01Z",
    s => s.evidence[0].attributes.series[0].timestamp = "invalid",
    s => s.evidence[0].attributes.discarded_samples = 1,
    s => s.evidence[0].observed_at = "2026-10-07T14:00:01Z",
    s => delete s.recovery.deployment_observation,
    s => s.recovery.deployment_observation.source = "fixture",
    s => s.recovery.deployment_observation.read_only = "true",
    s => s.recovery.deployment_observation.marker_status = "not_observed",
    s => s.recovery.deployment_observation.deployment_uid = "different-deployment",
    s => s.recovery.deployment_observation.cluster_context = "c".repeat(64),
    s => s.recovery.deployment_observation.generation = 4,
    s => s.recovery.deployment_observation.template_sha256 = "d".repeat(64),
    s => s.recovery.deployment_observation.observed_generation = 2,
    s => s.recovery.deployment_observation.desired_replicas = 0,
    s => s.recovery.deployment_observation.total_replicas = 3,
    s => s.recovery.deployment_observation.updated_replicas = 1,
    s => s.recovery.deployment_observation.ready_replicas = 1,
    s => s.recovery.deployment_observation.available_replicas = 1,
    s => s.recovery.deployment_observation.unavailable_replicas = 1,
    s => s.recovery.deployment_observation.observed_at = ack,
    s => s.recovery.deployment_observation.observed_at = "2026-10-07T14:00:20Z",
    s => s.recovery.deployment_observation.observed_at = "2026-10-07T14:01:01Z",
    s => s.recovery.observation_window = null,
    s => s.recovery.observation_window.reverse(),
    s => s.recovery.observation_window[0] = "2026-10-07T13:59:00Z",
    s => s.recovery.observation_window[1] = "2026-10-07T14:02:00Z",
    s => s.recovery.observation_window[1] = "2026-10-07T14:00:30Z",
    s => s.remediation.arguments.service = "other-service", s => s.remediation.preflight_mode = undefined,
  ];
  for (const [index, mutate] of mutations.entries()) {
    const state = incident("verified"); mutate(state);
    const view = recovery.recoveryVerificationView(state);
    assert.equal(view.mode, "unknown", `mutation ${index}`);
    assert.equal(view.canCheck, false);
    assert.equal(view.observation, null);
    assert.doesNotMatch(JSON.stringify(view), /private_provider/);
  }
  for (const state of [null, true, "provider-note", [], {}]) assert.equal(recovery.recoveryVerificationView(state).mode, "unknown");
});

test("endpoint and authentication validation never put credentials in query or URL", () => {
  assert.equal(recovery.recoveryCheckEndpoint("https://backend/", "inc/a"), "https://backend/v1/incidents/inc%2Fa/recovery-check");
  for (const url of ["javascript:alert(1)", "http://user:secret@backend", "http://backend?token=private", "http://backend#private", "not-a-url"]) {
    assert.equal(recovery.recoveryCheckEndpoint(url, "inc-a"), null);
  }
  for (const token of ["", "Bearer token", "token\n", "token\r\nheader:private", "x".repeat(8193)]) assert.equal(recovery.validRecoveryOperatorToken(token), false);
});

test("recheck makes one authenticated POST with no body/query, retries, or redirect following", async () => {
  const calls = [];
  const response = incident("verified");
  response.arbitrary_private = "private-provider-note";
  response.recovery.provider_note = "private-provider-note";
  response.recovery.deployment_observation.provider_note = "private-provider-note";
  const result = await recovery.checkIncidentRecovery("http://backend", incident(), "operator-token", async (url, options) => {
    calls.push({ url, options }); return Response.json(response);
  });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://backend/v1/incidents/inc-recovery/recovery-check");
  assert.equal(calls[0].options.method, "POST");
  assert.deepEqual(calls[0].options.headers, { Authorization: "Bearer operator-token" });
  assert.equal(calls[0].options.body, undefined);
  assert.equal(calls[0].options.redirect, "error");
  assert.equal(calls[0].options.cache, "no-store");
  assert.equal(result.error, null);
  assert.equal(result.incident.status, "resolved");
  assert.equal(recovery.recoveryVerificationView(result.incident).mode, "verified");
  assert.equal(result.incident.recovery.deployment_observation.total_replicas, 2);
  assert.doesNotMatch(JSON.stringify(result), /operator-token|private-provider-note/);
});

test("pending observations update retained state without implying recovery or dispatch", async () => {
  const result = await recovery.checkIncidentRecovery("http://backend", incident(), "token", async () => Response.json(incident()));
  assert.equal(result.error, null);
  assert.equal(result.incident.status, "remediation_pending");
  assert.equal(recovery.recoveryVerificationView(result.incident).mode, "pending");
});

test("simulation, unacknowledged, legacy or unauthenticated state makes zero requests", async () => {
  const invalid = [incident("pending", true), incident("verified")];
  const legacy = incident(); delete legacy.recovery; invalid.push(legacy);
  const noAck = incident(); noAck.recovery.execution_acknowledged_at = null; invalid.push(noAck);
  for (const state of invalid) {
    let calls = 0;
    const result = await recovery.checkIncidentRecovery("http://backend", state, "token", async () => { calls++; throw new Error("must not call"); });
    assert.equal(calls, 0);
    assert.equal(result.incident, null);
  }
  let calls = 0;
  await recovery.checkIncidentRecovery("http://backend", incident(), "", async () => { calls++; });
  assert.equal(calls, 0);
});

for (const status of [401, 403, 409, 502]) {
  test(`recovery HTTP ${status} preserves state and redacts provider errors without retry`, async () => {
    let calls = 0;
    const result = await recovery.checkIncidentRecovery("http://backend", incident(), "token", async () => {
      calls++; return Response.json({ detail: "private-provider-diagnostic" }, { status });
    });
    assert.equal(calls, 1);
    assert.equal(result.incident, null);
    assert.ok(result.error);
    assert.doesNotMatch(result.error, /private-provider/);
  });
}

test("cross-incident and malformed full responses cannot update the console", async () => {
  const mutations = [s => s.incident_id = "other-incident", s => delete s.recovery,
    s => s.recovery = [], s => s.recovery.simulation = true,
    s => s.evidence = [], s => s.trace = null, s => s.trace[0] = null,
    s => s.trace[0].payload = [], s => s.hypotheses = null,
    s => s.services = "payment-service", s => s.created_at = "yesterday",
    s => s.remediation = null, s => s.remediation.expected_result = null,
    s => s.service_impacts = [null], s => s.service_topology = [],
    s => s.evidence[0].attributes = [], s => s.evidence[0].observed_at = "not-a-date",
    s => s.recovery.reason_codes = ["private_provider_note"]];
  for (const [index, mutate] of mutations.entries()) {
    const response = incident("verified"); mutate(response);
    let calls = 0;
    const result = await recovery.checkIncidentRecovery("http://backend", incident(), "token", async () => {
      calls++; return Response.json(response);
    });
    assert.equal(calls, 1);
    assert.equal(result.incident, null, `mutation ${index}`);
    assert.ok(result.error);
    assert.doesNotMatch(result.error, /private_provider/);
  }
});

test("network and JSON failures do not retry or fall back to approval/execution", async () => {
  for (const transport of [async () => { throw new Error("private network data"); },
    async () => new Response("invalid-json"), async () => Response.json(null)]) {
    let calls = 0;
    const result = await recovery.checkIncidentRecovery("http://backend", incident(), "token", async (...args) => { calls++; return transport(...args); });
    assert.equal(calls, 1);
    assert.equal(result.incident, null);
    assert.doesNotMatch(result.error, /private|invalid-json/);
  }
});

const source = await readFile(new URL("../app/RecoveryVerificationPanel.tsx", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText;
const nativeRequire = createRequire(import.meta.url), exports = {};
new Function("require", "exports", compiled)(name => name === "./recovery-verification" ? recovery
  : name === "./evidence-citations" ? citations : nativeRequire(name), exports);
const render = (state, token = "operator-token") => renderToStaticMarkup(React.createElement(exports.RecoveryVerificationPanel, {
  url: "http://backend", incident: state, operatorToken: token, onUpdate: () => { throw new Error("render must not update"); },
}));

test("actual recovery component separates acknowledgement, observed rollout/SLIs and causal proof", () => {
  const html = render(incident("verified"));
  assert.match(html, /aria-label="Execution and recovery verification"/);
  assert.match(html, /Applied rollout and sustained SLI recovery observed/);
  assert.match(html, /An execution acknowledgement is not recovery/);
  assert.match(html, /does not prove that the action caused it/);
  assert.match(html, /Applied generation: <code>3/);
  assert.match(html, /Shared observation window \(UTC\)/);
  assert.match(html, /href="#evidence-metric%2Flatency"/);
  assert.match(html, /rollout_and_sli_recovery_observed_not_causal_proof/);
  assert.doesNotMatch(html, /<button|operator-token/);
});

test("actual component exposes one explicit pending check and no simulation/legacy false positive", () => {
  const pending = render(incident());
  assert.match(pending, /<button type="button">Recheck recovery \(read-only tools\)/);
  assert.match(pending, /never repeats restart or rollback/);
  assert.match(pending, /There is no automatic polling/);
  assert.doesNotMatch(pending, /operator-token/);
  const simulation = render(incident("pending", true));
  assert.match(simulation, /Simulation — live recovery is not verified/);
  assert.doesNotMatch(simulation, /<button|type="password"/);
  const legacy = incident("verified"); delete legacy.recovery;
  const unknown = render(legacy);
  assert.match(unknown, /Legacy resolved status/);
  assert.match(unknown, /Recovery not verified/);
  assert.doesNotMatch(unknown, /<button|sustained SLI recovery observed/);
});

test("actual component's local password entry is scoped, in-memory and required", () => {
  const html = render(incident(), undefined);
  // Omit prop explicitly; the render helper default above supplies a test token.
  const local = renderToStaticMarkup(React.createElement(exports.RecoveryVerificationPanel, {
    url: "http://backend", incident: incident(), onUpdate: () => {},
  }));
  assert.match(local, /type="password" autoComplete="off"/);
  assert.match(local, /not saved in browser storage, URL or trace/);
  assert.match(local, /cleared after a successful check/);
  assert.match(local, /http:\/\/backend\/v1\/incidents\/inc-recovery\/recovery-check/);
  assert.match(local, /<button type="button" disabled=""/);
  assert.doesNotMatch(html, /operator-token/);
  assert.doesNotMatch(source, /localStorage|sessionStorage|console\.|NEXT_PUBLIC|setInterval|setTimeout/);
});

test("unknown provider reasons and malicious evidence IDs do not become executable content", () => {
  const state = incident(); state.recovery.reason_codes = ["<script>private</script>"];
  assert.doesNotMatch(render(state), /<script>|private/);
  const escaped = incident("verified");
  escaped.evidence[0].id = "<script>id</script>";
  escaped.recovery.metric_evidence_ids[0] = escaped.evidence[0].id;
  const html = render(escaped);
  assert.match(html, /&lt;script&gt;id&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script>/);
});

test("incident console integrates recovery only for retained pending/resolved incidents", async () => {
  const console = await readFile(new URL("../app/IncidentConsole.tsx", import.meta.url), "utf8");
  assert.match(console, /incident && \["remediation_pending", "resolved"\]\.includes\(incident.status\)/);
  assert.match(console, /<RecoveryVerificationPanel key=\{`\$\{incident.incident_id\}:\$\{authMode\}:\$\{trustedEndpoint\}`\}/);
  assert.match(console, /operatorToken=\{authMode === "protected" && !operatorAccessError \? credentials.operatorToken : ""\}/);
  assert.match(console, /transport=\{apiTransport\} onUpdate=\{setIncident\}/);
  assert.match(console, /title: "Recovery verification pending"/);
  assert.match(console, /Simulation — live recovery unverified/);
});

async function interaction(state, response, failure = false) {
  const setters = [], updates = [], calls = [];
  let stateIndex = 0, task;
  const mockedReact = { ...React, useId: () => "token-field", useEffect: () => {}, useRef: value => ({ current: value }),
    useState(initial) { const index = stateIndex++; return [index === 0 ? "memory-token" : initial, value => setters.push({ index, value })]; } };
  const mockedRecovery = { ...recovery, checkIncidentRecovery(url, incident, token) {
    task = recovery.checkIncidentRecovery(url, incident, token, async (url, options) => {
      calls.push({ url, options });
      if (failure) throw new Error("private-provider-message");
      return Response.json(response);
    });
    return task;
  } };
  const interactiveExports = {};
  new Function("require", "exports", compiled)(name => name === "react" ? mockedReact
    : name === "./recovery-verification" ? mockedRecovery : name === "./evidence-citations" ? citations : nativeRequire(name), interactiveExports);
  const element = interactiveExports.RecoveryVerificationPanel({ url: "http://backend", incident: state, onUpdate: value => updates.push(value) });
  const findButton = element => {
    if (!element || typeof element !== "object") return null;
    if (element.type === "button") return element;
    const children = React.Children.toArray(element.props?.children);
    return children.map(findButton).find(Boolean) ?? null;
  };
  const button = findButton(element);
  assert.ok(button);
  assert.equal(button.props.disabled, false);
  button.props.onClick();
  await task;
  await Promise.resolve();
  return { setters, updates, calls };
}

test("actual check handler updates once and clears the memory token after valid success", async () => {
  const result = await interaction(incident(), incident("verified"));
  assert.equal(result.calls.length, 1);
  assert.equal(result.updates.length, 1);
  assert.equal(result.updates[0].incident_id, "inc-recovery");
  assert.equal(recovery.recoveryVerificationView(result.updates[0]).mode, "verified");
  assert.ok(result.setters.some(item => item.index === 0 && item.value === ""));
  assert.ok(result.setters.some(item => item.index === 1 && item.value === false));
});

test("actual handler preserves state on cross-id or transport errors and does not leak provider messages", async () => {
  for (const failure of [false, true]) {
    const response = incident("verified"); response.incident_id = "foreign-incident";
    const result = await interaction(incident(), response, failure);
    assert.equal(result.calls.length, 1);
    assert.equal(result.updates.length, 0);
    assert.equal(result.setters.filter(item => item.index === 0).length, 0);
    assert.doesNotMatch(JSON.stringify(result.setters), /memory-token|private-provider-message/);
    assert.ok(result.setters.some(item => item.index === 2 && typeof item.value === "string"));
  }
});

test("read-only recovery updates preserve valid RCA metadata but cannot promote invalid candidates", async () => {
  const current = Date.now(), time = delta => new Date(current + delta * 1000).toISOString();
  const response = incident();
  response.services.push("risk-service");
  response.complaint_service = "payment-service";
  response.diagnosis_service = "risk-service";
  response.diagnostic_excluded_evidence_ids = [];
  response.evidence[0].score = 0.9;
  response.evidence[0].observed_at = time(-10);
  response.evidence[0].attributes.series = [{ timestamp: time(-30), value: 2000 }, { timestamp: time(-10), value: 2200 }];
  response.evidence.push({ ...structuredClone(response.evidence[0]), id: "cpu-risk", service: "risk-service", query: "cpu_utilization",
    attributes: { unit: "ratio", series: [{ timestamp: time(-30), value: 0.95 }, { timestamp: time(-10), value: 0.96 }] } });
  response.service_topologies = [{ service: "payment-service", source: "service-catalog", topology_kind: "static_fixture_catalog",
    catalog_status: "available", edges: [{ source: "payment-service", target: "risk-service", provider_note: "private-provider-note" }], observed: { status: "not_supported" } }];
  response.root_cause_candidates = [{ rank: 1, service: "risk-service", incident_type: "cpu_saturation", local_evidence_ids: ["cpu-risk"],
    symptom_evidence_ids: ["metric/latency"], propagation_path: ["payment-service", "risk-service"], path_basis: "catalog", temporal_overlap: true,
    causal_direction_verified: false, validation_notes: ["private-provider-note"] }];
  for (const invalid of [false, "causal", "disjoint", "healthy", "unit"]) {
    const payload = structuredClone(response);
    if (invalid === "causal") payload.root_cause_candidates[0].causal_direction_verified = true;
    if (invalid === "disjoint") payload.evidence.at(-1).attributes.series.forEach((point, index) => point.timestamp = time(-250 + index * 20));
    if (invalid === "healthy") payload.evidence.at(-1).attributes.series.forEach(point => point.value = 0.1);
    if (invalid === "unit") payload.evidence.at(-1).attributes.unit = "seconds";
    const result = await recovery.checkIncidentRecovery("http://backend", incident(), "token", async () => Response.json(payload));
    assert.equal(result.error, null);
    assert.equal(recovery.recoveryVerificationView(result.incident).mode, "pending");
    assert.equal(rootCauseCandidatesView(result.incident, current).available, !invalid);
    assert.doesNotMatch(JSON.stringify(result), /private-provider-note|Bearer|operator-token/);
    if (!invalid) assert.equal(result.incident.diagnosis_service, "risk-service");
  }
});
