import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";
import { IncidentStreamDecoder, IncidentStreamProtocolError, streamConsoleIncident } from "../app/incident-stream.ts";
import * as consoleAuth from "../app/console-auth.ts";
import * as approvals from "../app/approval-recovery.ts";
import * as location from "../app/incident-location.ts";
import * as citations from "../app/evidence-citations.ts";
import * as preflight from "../app/preflight-target-review.ts";
import * as recovery from "../app/recovery-verification.ts";

const backend = "http://127.0.0.1:8000", question = "为什么 payment-service 今天延迟突然升高？";
const read = "local-reader-" + "r".repeat(32), operator = "local-operator-" + "o".repeat(32);
const credentials = { mode: "protected", trustedEndpoint: backend, readToken: read, operatorToken: operator, operatorActor: "local-operator" };
const instant = "2026-10-08T01:00:00+00:00";
function state(count = 0, status = "investigating") {
  return { incident_id: "INC-STREAM01", trace_id: "tr_stream01", question, environment: "prod-sg", status,
    current_step: count ? "query_telemetry" : "classify_incident", incident_type: null, severity: null,
    services: count ? ["payment-service"] : [], evidence: [], hypotheses: [], remediation: null,
    anomalous_window: null, created_at: instant, updated_at: instant,
    trace: Array.from({ length: count }, (_, index) => ({ sequence: index + 1, step: "query_telemetry", event: "tool_result",
      occurred_at: instant, duration_ms: 2, payload: { tool: "query_metrics", note: "checkpoint 已持久化" } })) };
}
function frame(snapshot, event = "incident", cursor = snapshot.trace.length) {
  return `id: ${cursor}\nevent: ${event}\ndata: ${JSON.stringify(snapshot)}\n\n`;
}
const failure = (code = "tool_unavailable", id = "INC-STREAM01") => `event: error\ndata: ${JSON.stringify({ code,
  message: "private-provider-text-and-token-should-not-be-shown", ...(id ? { incident_id: id } : {}) })}\n\n`;
function response(parts, { broken = false } = {}) {
  const encoder = new TextEncoder();
  return new Response(new ReadableStream({ start(controller) {
    for (const part of parts) controller.enqueue(typeof part === "string" ? encoder.encode(part) : part);
    if (broken) controller.error(new Error("private-transport-token")); else controller.close();
  } }), { headers: { "Content-Type": "text/event-stream; charset=utf-8" } });
}
async function run(body, options = {}) {
  const calls = [], snapshots = [], controller = options.controller ?? new AbortController();
  const transport = consoleAuth.createConsoleTransport(backend, options.credentials ?? credentials, async (url, init) => {
    calls.push({ url, init }); return body;
  });
  const result = await streamConsoleIncident(backend, question, transport, controller.signal, item => snapshots.push(item));
  return { calls, snapshots, result };
}

test("SSE parser accepts CRLF, comments, multline JSON and exact duplicate cursors once", () => {
  const decoder = new IncidentStreamDecoder(question), initial = state(), final = state(1, "diagnosed");
  const pretty = JSON.stringify(initial, null, 2).split("\n").map(line => `data: ${line}`).join("\n");
  const wire = `: keep-alive\n\nid: 0\nevent: incident\n${pretty}\n\n` + frame(initial)
    + frame(final) + frame(final, "complete") + frame(final, "complete") + ": keep-alive\n\n";
  const events = [];
  for (const character of wire.replaceAll("\n", "\r\n")) events.push(...decoder.push(character));
  decoder.finish();
  assert.deepEqual(events.map(item => [item.event, item.cursor]), [["incident", 0], ["incident", 1], ["complete", 1]]);
  assert.equal(decoder.lastIncident.status, "diagnosed");
});

test("fetch decoding preserves split UTF-8 bytes and sends one reader POST with no token in URL/body", async () => {
  const encoder = new TextEncoder(), final = state(1, "diagnosed");
  const wire = encoder.encode(frame(state()) + frame(final) + frame(final, "complete"));
  const result = await run(response([...wire].map(byte => Uint8Array.of(byte))));
  assert.equal(result.result.complete, true); assert.equal(result.result.uncertain, false);
  assert.equal(result.result.incidentId, "INC-STREAM01"); assert.equal(result.snapshots.length, 3);
  assert.equal(result.calls.length, 1); assert.equal(result.calls[0].url, `${backend}/v1/incidents/stream`);
  assert.equal(result.calls[0].init.method, "POST");
  assert.equal(result.calls[0].init.headers.get("Authorization"), `Bearer ${read}`);
  assert.equal(result.calls[0].init.headers.get("Accept"), "text/event-stream");
  assert.equal(result.calls[0].init.credentials, "omit"); assert.equal(result.calls[0].init.redirect, "error");
  assert.equal(result.calls[0].init.referrerPolicy, "no-referrer");
  assert.deepEqual(JSON.parse(result.calls[0].init.body), { question, environment: "prod-sg" });
  assert.doesNotMatch(result.calls[0].url + result.calls[0].init.body, new RegExp(`${read}|${operator}`));
});

test("bounded empty broad-log query metadata is valid but wrong query types remain rejected", () => {
  const actualShape = state(1);
  actualShape.evidence.push({ id: "ev_broad_logs", kind: "log", source: "fixture", title: "Broad service log search",
    observation: "Retained observations, not causal proof", observed_at: instant, service: "payment-service", score: 0.94,
    query: "", attributes: { matching_events: 184 } });
  const decoder = new IncidentStreamDecoder(question);
  assert.equal(decoder.push(frame(actualShape))[0].incident.evidence[0].query, "");
  for (const query of [false, {}, [], 100, "q".repeat(4097)]) {
    const bad = structuredClone(actualShape); bad.evidence[0].query = query;
    assert.throws(() => new IncidentStreamDecoder(question).push(frame(bad)), IncidentStreamProtocolError);
  }
});

for (const [label, mutate] of [
  ["foreign incident", item => item.incident_id = "INC-FOREIGN"], ["trace identity", item => item.trace_id = "tr_foreign"],
  ["trailing ID control", item => item.incident_id += "\n"], ["trailing service control", item => item.services = ["payment-service\n"]],
  ["question", item => item.question = "another request"], ["namespace", item => item.environment = "other-ns"],
  ["creation identity", item => item.created_at = "2026-10-08T01:00:01Z"],
  ["missing trace", item => delete item.trace], ["nonarray evidence", item => item.evidence = {}],
  ["bad current step", item => item.current_step = "private arbitrary step"], ["truthy status", item => item.status = true],
  ["duplicate services", item => item.services = ["payment-service", "payment-service"]],
  ["invalid calendar date", item => item.updated_at = "2026-02-31T01:00:00Z"],
  ["duration bool", item => item.trace[0].duration_ms = true],
  ["missing timestamp", item => delete item.trace[0].occurred_at],
  ["changed previous evidence", item => item.trace[0].payload.note = "rewritten history"],
  ["gapped trace sequence", item => item.trace[1].sequence = 4],
  ["malformed topology array", item => item.service_topologies = ["foreign provider"]],
  ["noniterable impact citations", item => item.service_impacts = [{ service: "ledger-service", status: "observed_anomaly", evidence_ids: {}, overlaps_primary_window: true }]],
  ["malformed hypotheses", item => item.hypotheses = [{ cause: "private" }]],
  ["malformed remediation", item => item.remediation = { requires_approval: "true" }],
  ["prototype payload", item => item.trace[1].payload = JSON.parse('{"__proto__":{"polluted":true}}')],
]) {
  test(`snapshot rejects ${label} without overwriting the last valid checkpoint`, () => {
    const decoder = new IncidentStreamDecoder(question), previous = state(1), next = state(2);
    decoder.push(frame(previous)); mutate(next);
    assert.throws(() => decoder.push(frame(next, "incident", 2)), IncidentStreamProtocolError);
    assert.deepEqual(decoder.lastIncident, previous);
  });
}

test("cursor regression, wrong lengths, leading zeros, conflicting duplicate and duplicate SSE fields fail closed", () => {
  for (const bad of [frame(state(1), "incident", 0), frame(state(2), "incident", "02"),
    frame(state(2)).replace("id: 2", "id: 2\nid: 2"), frame(state(2)).replace("event: incident", "event: incident\nevent: incident"),
    frame({ ...state(2), severity: "SEV-1" })]) {
    const decoder = new IncidentStreamDecoder(question); decoder.push(frame(state(2)));
    assert.throws(() => decoder.push(bad), IncidentStreamProtocolError);
  }
});

test("only an explicit diagnostic final complete event establishes completion", async () => {
  for (const status of ["investigating", "mitigating", "resolved", "rejected", "remediation_pending"]) {
    const result = await run(response([frame(state(1, status), "complete")]));
    assert.equal(result.result.complete, false); assert.equal(result.result.uncertain, true);
  }
  for (const status of ["waiting_approval", "diagnosed", "insufficient_evidence", "remediation_failed"]) {
    const result = await run(response([frame(state(1, status)), frame(state(1, status), "complete")]));
    assert.equal(result.result.complete, true, status);
  }
  const pending = await run(response([frame(state(1, "waiting_approval"))]));
  assert.equal(pending.result.complete, false); assert.equal(pending.result.uncertain, true);
  assert.equal(pending.result.incident.status, "waiting_approval");
  assert.equal(pending.calls.length, 1); assert.match(pending.result.error, /read-only/);
});

test("error frames are allowlisted, deduplicated, scoped and provider messages are never rendered", async () => {
  const decoder = new IncidentStreamDecoder(question);
  assert.equal(decoder.push(failure()).length, 1); assert.deepEqual(decoder.push(failure()), []); decoder.finish();
  const result = await run(response([frame(state(1)), failure(), failure()]));
  assert.equal(result.result.complete, false); assert.equal(result.result.uncertain, true);
  assert.match(result.result.error, /diagnostic tool/);
  assert.doesNotMatch(result.result.error, /private-provider|token-should/);
  const idOnly = await run(response([failure()])); assert.equal(idOnly.result.incidentId, "INC-STREAM01");
  for (const wire of [failure("unknown_private_code"), failure().replace("event: error", "id: 1\nevent: error"),
    failure().replace('"message":', '"unexpected":true,"message":'), failure("tool_unavailable", "INC-FOREIGN")]) {
    const validator = new IncidentStreamDecoder(question); validator.push(frame(state(1)));
    assert.throws(() => validator.push(wire), IncidentStreamProtocolError);
  }
  const terminal = new IncidentStreamDecoder(question); terminal.push(frame(state(1, "diagnosed")) + frame(state(1, "diagnosed"), "complete"));
  assert.throws(() => terminal.push(failure()), IncidentStreamProtocolError);
});

test("frame, wire and record count budgets bound provider streams", () => {
  assert.throws(() => new IncidentStreamDecoder(question).push(`data: ${"x".repeat(8 * 1024 * 1024)}`), IncidentStreamProtocolError);
  const oversized = new IncidentStreamDecoder(question), comment = `: ${"x".repeat(1024 * 1024)}\n\n`;
  assert.throws(() => { for (let index = 0; index < 33; index++) oversized.push(comment); }, IncidentStreamProtocolError);
  const frames = new IncidentStreamDecoder(question);
  assert.throws(() => { for (let index = 0; index < 129; index++) frames.push(frame(state())); }, IncidentStreamProtocolError);
});

test("truncated SSE, invalid UTF-8 and unknown media types never trigger another POST or fallback", async () => {
  for (const body of [response([frame(state(1)).slice(0, -1)]), response([Uint8Array.of(0xff)]),
    Response.json(state(1, "diagnosed")), response(["event: other\ndata: {}\n\n"]),
    response([frame(state(1))], { broken: true })]) {
    const result = await run(body);
    assert.equal(result.result.complete, false); assert.equal(result.result.uncertain, true);
    assert.equal(result.calls.length, 1);
    assert.doesNotMatch(result.result.error, /private-transport/);
  }
});

for (const status of [400, 401, 403, 422]) {
  test(`HTTP ${status} is explicit refusal, not stream completion or a synchronous fallback`, async () => {
    const result = await run(Response.json({ detail: `${operator} private-provider-text` }, { status }));
    assert.equal(result.result.complete, false); assert.equal(result.result.uncertain, false);
    assert.equal(result.result.incident, null); assert.equal(result.calls.length, 1);
    assert.doesNotMatch(result.result.error, /private-provider|local-operator/);
  });
}

test("local authorization refusal dispatches no request; explicit local demo sends no Bearer", async () => {
  const denied = await run(response([]), { credentials: { ...credentials, trustedEndpoint: null } });
  assert.equal(denied.calls.length, 0); assert.equal(denied.result.uncertain, false);
  const demo = await run(response([frame(state(1, "diagnosed")), frame(state(1, "diagnosed"), "complete")]), { credentials: {
    ...credentials, mode: "local-demo", readToken: "", operatorToken: "" } });
  assert.equal(demo.result.complete, true); assert.equal(demo.calls[0].init.headers.has("Authorization"), false);
});

test("complete cannot invent a first snapshot or advance/change the prior checkpoint", () => {
  assert.throws(() => new IncidentStreamDecoder(question).push(frame(state(1, "diagnosed"), "complete")), IncidentStreamProtocolError);
  for (const final of [state(2, "diagnosed"), { ...state(1, "diagnosed"), updated_at: "2026-10-08T01:00:01Z" }]) {
    const decoder = new IncidentStreamDecoder(question); decoder.push(frame(state(1, "diagnosed")));
    assert.throws(() => decoder.push(frame(final, "complete")), IncidentStreamProtocolError);
  }
});

test("abort before dispatch makes zero requests and abort during pending read cancels the body", async () => {
  const early = new AbortController(); early.abort();
  const result = await run(response([]), { controller: early }); assert.equal(result.calls.length, 0);
  let cancelled = 0, started;
  const connected = new Promise(resolve => started = resolve), controller = new AbortController();
  const pending = new Response(new ReadableStream({ start() { started(); }, cancel() { cancelled++; } }), {
    headers: { "Content-Type": "text/event-stream" } });
  const task = run(pending, { controller }); await connected; await new Promise(resolve => setImmediate(resolve));
  controller.abort(); const stopped = await task;
  assert.equal(stopped.result.complete, false); assert.equal(stopped.calls.length, 1); assert.equal(cancelled, 1);
});

test("EOF without complete preserves the reference for one explicit reader GET, never a POST retry", async () => {
  const interrupted = await run(response([frame(state(1))]));
  assert.equal(interrupted.result.incidentId, "INC-STREAM01"); assert.equal(interrupted.result.complete, false);
  const gets = [], transport = consoleAuth.createConsoleTransport(backend, credentials, async (url, init) => {
    gets.push({ url, init }); return Response.json(state(2, "diagnosed"));
  });
  assert.equal((await approvals.refreshApproval(backend, interrupted.result.incidentId, transport)).incident.status, "diagnosed");
  assert.equal(gets.length, 1); assert.equal(gets[0].init.method, "GET");
  assert.equal(gets[0].init.headers.get("Authorization"), `Bearer ${read}`);
});

const consoleSource = await readFile(new URL("../app/IncidentConsole.tsx", import.meta.url), "utf8");
const compiled = ts.transpileModule(consoleSource, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText;
const nativeRequire = createRequire(import.meta.url);
function harness() {
  const values = [], refs = [], tasks = [], cleanups = [], updates = [], posted = [], gets = [];
  let restoredState = null;
  let hook = 0, ref = 0, first = true;
  const fakeReact = { ...React, useMemo: callback => callback(), useEffect: effect => { if (first) cleanups.push(effect()); },
    useState(initial) {
      const index = hook++;
      if (!(index in values)) values[index] = ({ 11: backend, 12: read, 13: operator, 14: "local-operator" })[index] ?? initial;
      return [values[index], value => { values[index] = typeof value === "function" ? value(values[index]) : value; updates.push({ index, value: values[index] }); }];
    },
    useRef(initial) { const index = ref++; return refs[index] ??= { current: initial }; },
  };
  const fakeLocation = { href: "http://localhost:3128/" };
  const browser = { location: fakeLocation, history: { replaceState(_a, _b, url) { fakeLocation.href = url; } } };
  const stubPanel = () => null;
  const imports = { react: fakeReact, "./console-auth": consoleAuth, "./approval-recovery": { ...approvals,
    sendApproval: async (...args) => { posted.push(args); return { incident: state(1, "rejected"), uncertain: false, error: null }; },
    refreshApproval: async (...args) => { gets.push(args); return { incident: restoredState, uncertain: true, error: null }; } },
    "./incident-location": location, "./evidence-citations": citations, "./preflight-target-review": preflight,
    "./recovery-verification": recovery, "./incident-stream": { streamConsoleIncident(url, requestedQuestion, transport, signal, callback) {
      let resolve; const promise = new Promise(done => resolve = done);
      tasks.push({ url, requestedQuestion, transport, signal, callback, resolve }); return promise;
    } } };
  for (const name of ["EvidenceExplorer", "ActionEvidenceReview", "PreflightTargetReview", "DeploymentEventTimeline", "DependencyImpactList",
    "OperationInspectionPanel", "RecoveryVerificationPanel", "RootCauseCandidates"]) imports[`./${name}`] = { [name]: stubPanel };
  const exports = {};
  new Function("require", "exports", "process", "window", compiled)(name => imports[name] ?? nativeRequire(name), exports,
    { env: { NEXT_PUBLIC_ORCHESTRATOR_URL: backend } }, browser);
  function render() { hook = 0; ref = 0; const element = exports.IncidentConsole(); first = false; return element; }
  function find(element, predicate) {
    if (!element || typeof element !== "object") return null;
    if (predicate(element)) return element;
    return React.Children.toArray(element.props?.children).map(item => find(item, predicate)).find(Boolean) ?? null;
  }
  return { values, tasks, updates, posted, gets, render, find, browser,
    restoreWith(snapshot) { restoredState = snapshot; }, unmount() { cleanups.forEach(fn => fn?.()); } };
}
function finalResult(snapshot = state(1, "diagnosed")) {
  return { incident: snapshot, incidentId: snapshot.incident_id, complete: true, uncertain: false, error: null };
}

test("actual console keeps intermediate waiting-approval snapshots locked until complete", async () => {
  const ui = harness(), form = ui.find(ui.render(), item => item.type === "form" && item.props.onSubmit);
  const submitting = form.props.onSubmit({ preventDefault() {} });
  const waiting = state(1, "waiting_approval");
  waiting.remediation = { id: "rem_test", title: "Advisory preview", tool: "restart_deployment", arguments: {},
    command_preview: "preview", expected_result: "Unconfirmed", risk: "high", requires_approval: true, dry_run_passed: true, preflight_mode: "simulation" };
  ui.tasks[0].callback(waiting);
  assert.equal(ui.values[3].incident_id, waiting.incident_id);
  assert.equal(ui.find(ui.render(), item => item.props?.className === "approve-button"), null);
  assert.equal(ui.posted.length, 0);
  ui.tasks[0].resolve(finalResult(waiting)); await submitting;
  assert.equal(ui.values[15], "complete"); assert.equal(ui.values[16], false);
  assert.equal(ui.tasks.length, 1);
});

test("actual rendered classification progress cannot present its initial type hint as a final root cause", async () => {
  const ui = harness(), form = ui.find(ui.render(), item => item.type === "form");
  const submitting = form.props.onSubmit({ preventDefault() {} });
  const classified = state(1);
  classified.incident_type = "database_connection_pool_exhaustion";
  ui.tasks[0].callback(classified);
  const running = renderToStaticMarkup(ui.render());
  assert.match(running, /<h1>payment-service diagnostic progress<\/h1>/);
  assert.doesNotMatch(running, /<h1>[^<]*(?:database|pool|exhaustion)/);
  assert.match(running, /Awaiting retained diagnostic evidence and hypotheses/);
  assert.doesNotMatch(running, /Insufficient evidence to identify a root cause/);
  ui.tasks[0].resolve({ incident: classified, incidentId: classified.incident_id, complete: false, uncertain: true, error: "Interrupted" });
  await submitting;
  const interrupted = renderToStaticMarkup(ui.render());
  assert.match(interrupted, /<h1>payment-service diagnosis unconfirmed<\/h1>/);
  assert.doesNotMatch(interrupted, /<h1>[^<]*(?:database|pool|exhaustion)/);
});

test("actual rendered complete result retains its final incident-family title", async () => {
  const ui = harness(), submitting = ui.find(ui.render(), item => item.type === "form").props.onSubmit({ preventDefault() {} });
  const final = state(1, "diagnosed"); final.incident_type = "database_connection_pool_exhaustion";
  ui.tasks[0].callback(final); ui.tasks[0].resolve(finalResult(final)); await submitting;
  const html = renderToStaticMarkup(ui.render());
  assert.match(html, /<h1>payment-service database connection pool exhaustion<\/h1>/);
  assert.match(html, /Insufficient evidence to identify a root cause/);
});

for (const change of ["clear", "question", "mode", "reader", "operator", "actor", "trust", "unmount"]) {
  test(`actual console ${change} aborts a prior stream and ignores late snapshots/completion`, async () => {
    const ui = harness(), first = ui.render(), form = ui.find(first, item => item.type === "form" && item.props.onSubmit);
    const submitting = form.props.onSubmit({ preventDefault() {} });
    assert.equal(ui.tasks.length, 1);
    if (change === "clear") ui.find(first, item => item.type === "button" && item.props.onClick?.name === "clearCredentials").props.onClick();
    if (change === "question") ui.find(first, item => item.props?.id === "incident-question").props.onChange({ target: { value: "Why is checkout failing now?" } });
    if (change === "mode") ui.find(first, item => item.props?.id === "console-auth-mode").props.onChange({ target: { value: "local-demo" } });
    if (["reader", "operator", "actor"].includes(change)) ui.find(first, item => item.props?.id === ({ reader: "console-read-token",
      operator: "console-operator-token", actor: "console-operator-actor" })[change]).props.onChange({ target: { value: "changed-in-memory" } });
    if (change === "trust") ui.find(first, item => item.type === "input" && item.props.type === "checkbox").props.onChange({ target: { checked: false } });
    if (change === "unmount") ui.unmount();
    assert.equal(ui.tasks[0].signal.aborted, true);
    const before = ui.updates.length;
    ui.tasks[0].callback(state(1, "waiting_approval")); ui.tasks[0].resolve(finalResult()); await submitting;
    assert.equal(ui.updates.length, before, "stale callbacks/finally cannot overwrite the current view");
    assert.equal(ui.values[3], null); assert.equal(ui.posted.length, 0);
  });
}

test("changing the question permits an explicit new diagnosis while old replies cannot overwrite it", async () => {
  const ui = harness();
  const first = ui.find(ui.render(), item => item.type === "form").props.onSubmit({ preventDefault() {} });
  ui.find(ui.render(), item => item.props?.id === "incident-question").props.onChange({ target: { value: "Why is risk-service failing?" } });
  const second = ui.find(ui.render(), item => item.type === "form").props.onSubmit({ preventDefault() {} });
  assert.equal(ui.tasks.length, 2); assert.equal(ui.tasks[0].signal.aborted, true);
  const current = { ...state(1, "diagnosed"), incident_id: "INC-NEW", question: "Why is risk-service failing?", services: ["risk-service"] };
  ui.tasks[1].callback(current); ui.tasks[1].resolve(finalResult(current)); await second;
  const before = ui.updates.length; ui.tasks[0].callback(state(2)); ui.tasks[0].resolve(finalResult()); await first;
  assert.equal(ui.updates.length, before); assert.equal(ui.values[3].incident_id, "INC-NEW");
  assert.equal(ui.tasks[1].requestedQuestion, current.question);
});

test("read-only GET after a cut stream preserves the unconfirmed gate even for waiting_approval", async () => {
  const ui = harness(), form = ui.find(ui.render(), item => item.type === "form");
  const submitting = form.props.onSubmit({ preventDefault() {} });
  const waiting = state(1, "waiting_approval");
  ui.tasks[0].callback(waiting); ui.tasks[0].resolve({ incident: waiting, incidentId: waiting.incident_id,
    complete: false, uncertain: true, error: "Stream interrupted" }); await submitting;
  assert.match(ui.browser.location.href, /diagnosis_pending=1/);
  ui.restoreWith(waiting);
  ui.find(ui.render(), item => item.type === "button" && item.props.children === "Restore / refresh incident (read-only)").props.onClick();
  await Promise.resolve(); await Promise.resolve();
  assert.equal(ui.gets.length, 1); assert.equal(ui.values[15], "interrupted"); assert.equal(ui.values[16], true);
  assert.equal(ui.find(ui.render(), item => item.props?.className === "approve-button"), null);
  assert.match(ui.browser.location.href, /diagnosis_pending=1/); assert.equal(ui.posted.length, 0);
});

test("console uses fetch SSE only, keeps sync helper compatible, and never uses EventSource/token URLs", async () => {
  const authSource = await readFile(new URL("../app/console-auth.ts", import.meta.url), "utf8");
  assert.match(consoleSource, /streamConsoleIncident<ApiIncident>/);
  assert.match(consoleSource, /diagnosisRequiresRefresh/);
  assert.match(consoleSource, /epoch !== diagnosisEpoch.current \|\| controller.signal.aborted/);
  assert.match(authSource, /export async function createConsoleIncident/);
  assert.doesNotMatch(consoleSource, /new EventSource|setInterval|createConsoleIncident<ApiIncident>/);
  assert.doesNotMatch(consoleSource, /retainLocation\([^\n]*(?:Token|Credential)/);
});
