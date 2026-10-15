import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { ConsoleAuthError, consoleApprovalProblem, consoleAuthorizationProblem, consoleBackendEndpoint,
  createConsoleIncident, createConsoleTransport, isLocalDemoBackend,
  validConsoleApiToken, validConsoleOperatorActor } from "../app/console-auth.ts";
import { refreshApproval, sendApproval } from "../app/approval-recovery.ts";
import { inspectOperation } from "../app/operation-inspection.ts";
import { loadInspectionHistory } from "../app/inspection-history.ts";

const backend = "http://127.0.0.1:8000";
const id = "INC-AUTH_TEST";
const read = "local-test-reader-" + "r".repeat(32);
const operator = "local-test-operator-" + "o".repeat(32);
const actor = "local-token-bound-operator";
const auth = () => ({ mode: "protected", trustedEndpoint: backend, readToken: read, operatorToken: operator, operatorActor: actor });
const approval = { decision: "approve", actor, idempotency_key: "one-stable-operation", reason: "local protocol test" };
const snapshot = status => Response.json({ incident_id: id, status, services: ["payment-service"], evidence: [], hypotheses: [], trace: [] });
function recording(responses) {
  const calls = [];
  const transport = async (url, options) => {
    calls.push({ url, options });
    const response = responses.shift();
    if (response instanceof Error) throw response;
    assert.ok(response, "Unexpected request or unsafe replay");
    return response;
  };
  return { calls, transport };
}

test("backend trust rejects public plaintext, userinfo, queries, fragments and ambiguous paths", () => {
  for (const url of ["http://example.com", "http://localhost.evil", "http://127.0.0.1.evil",
    "https://user:secret@example.test", "https://@example.test", "https://example.test?", "https://example.test#",
    "https://example.test?token=private", "https://example.test#private", "https://example.test/api/%2fother",
    "https://example.test/api/../other", "https://example.test\\other", "ftp://localhost", " https://example.test"]) {
    assert.equal(consoleBackendEndpoint(url), null, url);
  }
  assert.equal(consoleBackendEndpoint("https://trusted.test/api/"), "https://trusted.test/api");
  for (const url of ["http://localhost:8000", "http://127.0.0.1:8000", "http://[::1]:8000"]) assert.ok(isLocalDemoBackend(url));
  assert.equal(isLocalDemoBackend("https://localhost"), false);
  assert.equal(isLocalDemoBackend("https://example.test"), false);
});

test("credential syntax mirrors the API's bounded non-space printable ASCII contract", () => {
  for (const value of ["", "a".repeat(31), "a".repeat(4097), " " + read, read + "\n", read + "é", null]) assert.equal(validConsoleApiToken(value), false);
  for (const value of [read, operator, "a".repeat(32), "a".repeat(4096), "test:" + "!".repeat(32)]) assert.ok(validConsoleApiToken(value));
  for (const value of ["", "a", " " + actor, actor + "\n", "a".repeat(121)]) assert.equal(validConsoleOperatorActor(value), false);
  assert.ok(validConsoleOperatorActor(actor));
});

test("reader and operator routes receive only their selected header and no ambient browser credentials", async () => {
  const fixture = recording(Array.from({ length: 6 }, () => new Response("{}")));
  const request = createConsoleTransport(backend, auth(), fixture.transport);
  for (const path of ["", "/history", "/events", "/operation-inspections"]) {
    await request(`${backend}/v1/incidents/${id}${path}`, { method: "GET" });
  }
  await request(`${backend}/v1/incidents`, { method: "POST", body: JSON.stringify({ question: "Investigate payment" }) });
  await request(`${backend}/v1/incidents/${id}/operation-status`, { method: "GET", headers: { Authorization: "Bearer wrong" } });
  for (const call of fixture.calls.slice(0, 5)) assert.equal(call.options.headers.get("Authorization"), `Bearer ${read}`);
  assert.equal(fixture.calls[5].options.headers.get("Authorization"), `Bearer ${operator}`);
  for (const call of fixture.calls) {
    assert.equal(call.options.redirect, "error"); assert.equal(call.options.credentials, "omit");
    assert.equal(call.options.cache, "no-store"); assert.equal(call.options.referrerPolicy, "no-referrer");
    assert.doesNotMatch(call.url + (call.options.body ?? ""), new RegExp(`${read}|${operator}`));
  }
});

test("tokens are never sent to another origin, path, query, redirecting auth endpoint or unsupported method", async () => {
  const fixture = recording([]), request = createConsoleTransport(backend, auth(), fixture.transport);
  for (const [url, method] of [
    ["https://other.test/v1/incidents/INC-X", "GET"], [`${backend}/v1/incidents/${id}?token=private`, "GET"],
    [`${backend}/v1/incidents/${id}#private`, "GET"], [`${backend}/other`, "GET"],
    [`${backend}/v1/incidents/${id}/approval`, "GET"], [`${backend}/v1/incidents/${id}`, "PATCH"],
    [`${backend}/v1/incidents/..%2Fapproval`, "GET"], [`${backend}/v1/incidents/${id}/approval/extra`, "POST"],
  ]) await assert.rejects(request(url, { method }), ConsoleAuthError);
  assert.equal(fixture.calls.length, 0);
});

test("HTTPS is authorized only after the exact full backend is trusted; configuration changes invalidate credentials", async () => {
  const fixture = recording([]);
  for (const endpoint of ["https://trusted.test/api", "https://trusted.test/api2", "http://127.0.0.1:8001"]) {
    const request = createConsoleTransport(endpoint, auth(), fixture.transport);
    await assert.rejects(request(`${endpoint}/v1/incidents/${id}`), /Confirm the exact trusted backend/);
  }
  const trusted = { ...auth(), trustedEndpoint: "https://trusted.test/api" };
  const allowed = recording([new Response("{}")]);
  await createConsoleTransport("https://trusted.test/api", trusted, allowed.transport)(`https://trusted.test/api/v1/incidents/${id}`);
  assert.equal(allowed.calls[0].options.headers.get("Authorization"), `Bearer ${read}`);
});

test("missing credentials and duplicate role credentials block locally without attempting anonymous requests", async () => {
  for (const credentials of [{ ...auth(), readToken: "" }, { ...auth(), trustedEndpoint: null }]) {
    const fixture = recording([]), request = createConsoleTransport(backend, credentials, fixture.transport);
    const result = await refreshApproval(backend, id, request);
    assert.equal(result.incident, null); assert.equal(result.uncertain, true); assert.ok(result.error);
    assert.equal(fixture.calls.length, 0);
  }
  for (const credentials of [{ ...auth(), operatorToken: "" }, { ...auth(), operatorToken: read }, { ...auth(), operatorActor: "" }]) {
    const fixture = recording([]);
    const result = await sendApproval(backend, id, approval, createConsoleTransport(backend, credentials, fixture.transport));
    assert.equal(result.incident, null); assert.equal(result.uncertain, false); assert.ok(result.error);
    assert.equal(fixture.calls.length, 0);
  }
});

test("explicit loopback demo has no Bearer and never requires made-up keys", async () => {
  const credentials = { ...auth(), mode: "local-demo", readToken: "", operatorToken: "" };
  assert.equal(consoleAuthorizationProblem(backend, credentials, "read"), null);
  assert.equal(consoleApprovalProblem(backend, credentials), null);
  const fixture = recording([snapshot("waiting_approval"), snapshot("rejected")]);
  const request = createConsoleTransport(backend, credentials, fixture.transport);
  await refreshApproval(backend, id, request);
  await sendApproval(backend, id, { ...approval, decision: "reject" }, request);
  assert.deepEqual(fixture.calls.map(call => call.options.method), ["GET", "POST"]);
  assert.ok(fixture.calls.every(call => !call.options.headers.has("Authorization")));
  for (const changed of [{ ...credentials, trustedEndpoint: "https://trusted.test" }, { ...credentials, readToken: read }]) {
    const target = changed.trustedEndpoint;
    await assert.rejects(createConsoleTransport(target, changed, fixture.transport)(`${target}/v1/incidents/${id}`), ConsoleAuthError);
  }
});

test("approval sends the operator token with an explicit actor; mismatched actor blocks before dispatch", async () => {
  const fixture = recording([snapshot("rejected")]);
  const request = createConsoleTransport(backend, auth(), fixture.transport);
  const result = await sendApproval(backend, id, { ...approval, decision: "reject" }, request);
  assert.equal(result.incident.status, "rejected");
  assert.equal(fixture.calls.length, 1);
  assert.equal(fixture.calls[0].options.headers.get("Authorization"), `Bearer ${operator}`);
  assert.deepEqual(JSON.parse(fixture.calls[0].options.body), { ...approval, decision: "reject" });
  const wrong = await sendApproval(backend, id, { ...approval, actor: "another-actor" }, request);
  assert.equal(wrong.incident, null); assert.equal(wrong.uncertain, false);
  assert.match(wrong.error, /actor/); assert.equal(fixture.calls.length, 1);
});

for (const status of [401, 403]) {
  test(`approval HTTP ${status} is explicit rejection, not successful fallback or another credential retry`, async () => {
    const fixture = recording([Response.json({ detail: `${read} private-provider-diagnostic` }, { status })]);
    const result = await sendApproval(backend, id, approval, createConsoleTransport(backend, auth(), fixture.transport));
    assert.equal(fixture.calls.length, 1); assert.equal(result.incident, null); assert.equal(result.uncertain, false);
    assert.match(result.error, /No approval decision was accepted/);
    assert.doesNotMatch(JSON.stringify(result), /private-provider|local-test-reader|local-test-operator/);
  });
  test(`reader HTTP ${status} is explicit denial without operator or demo fallback`, async () => {
    const fixture = recording([Response.json({ detail: operator }, { status })]);
    const result = await refreshApproval(backend, id, createConsoleTransport(backend, auth(), fixture.transport));
    assert.equal(result.incident, null); assert.equal(result.uncertain, true);
    assert.equal(fixture.calls.length, 1); assert.ok(result.error); assert.doesNotMatch(result.error, /local-test/);
  });
}

test("ambiguous approval uses only reader GET recovery and never repeats the operator POST", async () => {
  const fixture = recording([new TypeError("private transport metadata"), snapshot("remediation_pending")]);
  const result = await sendApproval(backend, id, approval, createConsoleTransport(backend, auth(), fixture.transport));
  assert.equal(result.incident.status, "remediation_pending"); assert.equal(result.uncertain, false);
  assert.deepEqual(fixture.calls.map(call => call.options.method), ["POST", "GET"]);
  assert.deepEqual(fixture.calls.map(call => call.options.headers.get("Authorization")), [`Bearer ${operator}`, `Bearer ${read}`]);
  assert.doesNotMatch(result.error, /private transport/);
});

test("operation marker and inspection history retain separate operator/read scopes and no action body", async () => {
  const fixture = recording([Response.json({ incident_id: id, incident_status: "remediation_pending", tool: "restart_deployment", read_only: true,
    observation: { status: "unknown", source: "process_cache", read_only: true, rollout_health_verified: false } }), Response.json([])]);
  const request = createConsoleTransport(backend, auth(), fixture.transport);
  assert.equal((await inspectOperation(backend, id, "restart_deployment", request)).observation.status, "unknown");
  assert.deepEqual((await loadInspectionHistory(backend, id, request)).records, []);
  assert.deepEqual(fixture.calls.map(call => call.options.headers.get("Authorization")), [`Bearer ${operator}`, `Bearer ${read}`]);
  assert.ok(fixture.calls.every(call => call.options.method === "GET" && call.options.body === undefined));
});

test("shared recovery transport is operator-only, bodyless and bound to the trusted endpoint", async () => {
  const fixture = recording([new Response("{}")]), request = createConsoleTransport(backend, auth(), fixture.transport);
  const endpoint = `${backend}/v1/incidents/${id}/recovery-check`;
  await request(endpoint, { method: "POST", headers: { Authorization: "Bearer unrelated" } });
  assert.equal(fixture.calls.length, 1);
  assert.equal(fixture.calls[0].options.headers.get("Authorization"), `Bearer ${operator}`);
  assert.equal(fixture.calls[0].options.body, undefined);
  for (const body of ["", "{}", "{\"target\":\"other\"}"]) {
    await assert.rejects(request(endpoint, { method: "POST", body }), /no request body/);
  }
  await assert.rejects(createConsoleTransport(backend, { ...auth(), operatorToken: "" }, fixture.transport)(endpoint, { method: "POST" }), ConsoleAuthError);
  assert.equal(fixture.calls.length, 1);
});

test("diagnosis POST uses reader scope and auth failures never become a static demo result", async () => {
  const fixture = recording([snapshot("diagnosed"), new Response("private provider", { status: 401 }), new TypeError(operator)]);
  const request = createConsoleTransport(backend, auth(), fixture.transport);
  assert.equal((await createConsoleIncident(backend, "Investigate payment-service", request)).incident.status, "diagnosed");
  const denied = await createConsoleIncident(backend, "Investigate payment-service", request);
  const failed = await createConsoleIncident(backend, "Investigate payment-service", request);
  assert.equal(denied.incident, null); assert.ok(denied.error);
  assert.equal(failed.incident, null); assert.ok(failed.error);
  assert.doesNotMatch(JSON.stringify([denied, failed]), /private provider|local-test-operator/);
  assert.ok(fixture.calls.every(call => call.options.headers.get("Authorization") === `Bearer ${read}`));
  assert.deepEqual(JSON.parse(fixture.calls[0].options.body), { question: "Investigate payment-service", environment: "prod-sg" });
});

test("console wiring keeps secrets memory-only, provides explicit trust/mode, and avoids fixed fake approval actor", async () => {
  const [source, helper, operation, history, recovery] = await Promise.all([
    readFile(new URL("../app/IncidentConsole.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/console-auth.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/OperationInspectionPanel.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/InspectionHistoryPanel.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/RecoveryVerificationPanel.tsx", import.meta.url), "utf8"),
  ]);
  assert.match(source, /type="password" autoComplete="off" value=\{readCredential\}/);
  assert.match(source, /type="password" autoComplete="off" value=\{operatorCredential\}/);
  assert.match(source, /actor: credentials\.operatorActor/);
  assert.match(source, /createConsoleTransport\(orchestratorUrl, credentials\)/);
  assert.match(source, /Unauthenticated local demo/); assert.match(source, /I trust this exact backend/);
  assert.match(source, /clearCredentials\(\); setAuthMode\(mode\)/);
  assert.match(operation, /inspectOperation\(url, incidentId, tool, transport\)/);
  assert.match(history, /loadInspectionHistory\(url, incidentId, transport\)/);
  assert.match(recovery, /checkIncidentRecovery\(url, incident, token, transport\)/);
  assert.doesNotMatch(source + helper, /localStorage|sessionStorage|process\.env\.[A-Z_]*(?:TOKEN|SECRET|ACTOR)|demo-oncall@/);
  assert.doesNotMatch(source, /retainLocation\([^\n]*(?:Credential|Token)/);
});
