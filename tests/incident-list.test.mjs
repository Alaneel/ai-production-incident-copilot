import assert from "node:assert/strict";
import test from "node:test";
import { createConsoleTransport } from "../app/console-auth.ts";
import { attentionCount, loadIncidentList, relativeTime } from "../app/incident-list.ts";

const backend = "http://127.0.0.1:8000";
const read = "local-test-reader-" + "r".repeat(32);
const operator = "local-test-operator-" + "o".repeat(32);
const credentials = { mode: "protected", trustedEndpoint: backend, readToken: read, operatorToken: operator, operatorActor: "local-operator" };
const row = (overrides = {}) => ({ incident_id: "INC-4F2A91", status: "waiting_approval", environment: "prod-sg", service: "checkout",
  severity: "SEV-2", question: "Alert HighLatency is firing for checkout", leading_cause: "Pool exhausted after v142",
  proposed_action: "Rollback checkout to v141", created_at: "2026-11-25T09:00:00+00:00", updated_at: "2026-11-25T09:05:00+00:00", ...overrides });
function recording(response) {
  const calls = [];
  return { calls, transport: async (url, options) => { calls.push({ url, options }); if (response instanceof Error) throw response; return response; } };
}

test("the incident list is one reader-scoped GET with no query string or ambient credentials", async () => {
  const fixture = recording(Response.json({ incidents: [row(), row({ incident_id: "INC-000002", status: "diagnosed", service: null, leading_cause: null, proposed_action: null })], count: 2 }));
  const result = await loadIncidentList(backend, createConsoleTransport(backend, credentials, fixture.transport));
  assert.equal(result.error, null);
  assert.deepEqual(result.incidents.map(item => item.incident_id), ["INC-4F2A91", "INC-000002"]);
  assert.equal(attentionCount(result.incidents), 1);
  assert.equal(fixture.calls.length, 1);
  const [{ url, options }] = fixture.calls;
  assert.equal(url, `${backend}/v1/incidents`);
  assert.equal(options.method, "GET");
  assert.equal(options.headers.get("Authorization"), `Bearer ${read}`);
  assert.equal(options.credentials, "omit");
  assert.equal(options.redirect, "error");
  // Unlisted fields from the backend are not carried into the console.
  assert.equal("created_at" in result.incidents[0], false);
});

test("the trusted transport still refuses list filters and writes outside the allow-list", async () => {
  const fixture = recording(Response.json({ incidents: [] }));
  const request = createConsoleTransport(backend, credentials, fixture.transport);
  await assert.rejects(request(`${backend}/v1/incidents?status=waiting_approval`, { method: "GET" }), /does not match the trusted backend/);
  await assert.rejects(request(`${backend}/v1/incidents`, { method: "DELETE" }), /Unsupported incident request/);
  assert.equal(fixture.calls.length, 0);
});

test("malformed, oversized or failed list responses yield no rows and no partial rendering", async () => {
  const bad = [
    { incidents: [row({ incident_id: "<script>" })] },
    { incidents: [row({ status: "approved" })] },
    { incidents: [row({ leading_cause: "x".repeat(241) })] },
    { incidents: [row({ updated_at: "yesterday" })] },
    { incidents: [row(), "not-a-row"] },
    { incidents: Array.from({ length: 101 }, () => row()) },
    { incidents: "none" },
  ];
  for (const body of bad) {
    const result = await loadIncidentList(backend, recording(Response.json(body)).transport);
    assert.deepEqual(result, { incidents: [], error: "Could not load the incident list. Nothing was changed." });
  }
  const denied = await loadIncidentList(backend, recording(new Response("{}", { status: 401 })).transport);
  assert.equal(denied.incidents.length, 0);
  assert.match(denied.error, /credential|token|access/i);
  const offline = await loadIncidentList(backend, recording(new TypeError("fetch failed")).transport);
  assert.match(offline.error, /Could not load/);
  const unconfigured = recording(Response.json({ incidents: [] }));
  assert.match((await loadIncidentList(undefined, unconfigured.transport)).error, /No request was sent/);
  assert.equal(unconfigured.calls.length, 0);
});

test("relative times are stable and never negative", () => {
  const now = Date.parse("2026-11-25T10:00:00Z");
  assert.equal(relativeTime("2026-11-25T09:59:40Z", now), "just now");
  assert.equal(relativeTime("2026-11-25T09:48:00Z", now), "12m ago");
  assert.equal(relativeTime("2026-11-25T07:00:00Z", now), "3h ago");
  assert.equal(relativeTime("2026-11-22T10:00:00Z", now), "3d ago");
  assert.equal(relativeTime("2026-11-25T10:05:00Z", now), "just now");
});
