import assert from "node:assert/strict";
import test from "node:test";
import { loadInspectionHistory, normalizeInspectionHistory } from "../app/inspection-history.ts";

function record(outcome = { status: "completed", completed_at: "2026-10-03T00:00:01Z", observation: {
  status: "marker_matches", source: "kubernetes_deployment_marker", read_only: true, rollout_health_verified: false,
} }) {
  return { audit_id: "inspection_" + "a".repeat(32), incident_id: "inc-pending", started_at: "2026-10-03T00:00:00Z",
           request: { incident_id: "inc-pending", tool: "rollback_release", read_only: true, auth_mode: "disabled",
                      arguments: { service: "payment-service", namespace: "prod-sg", revision: "141" },
                      request_principal: "unauthenticated-demo", approval_actor: "operator", idempotency_key: "approved-operation-key" }, outcome };
}

test("audit history is exactly one GET, without querying operation status or approval", async () => {
  const calls = [];
  const result = await loadInspectionHistory("http://backend", "inc-pending", async (url, options) => {
    calls.push({ url, options }); return Response.json([record()]);
  });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://backend/v1/incidents/inc-pending/operation-inspections");
  assert.equal(calls[0].options.method, "GET");
  assert.equal(calls[0].options.body, undefined);
  assert.equal(calls[0].options.headers, undefined);
  assert.equal(calls[0].options.cache, "no-store");
  assert.equal(result.records[0].state, "completed");
  assert.equal(result.records[0].observation.status, "marker_matches");
});

test("unfinished audit and unavailable query are distinct and carry no execution claim", () => {
  const unfinished = normalizeInspectionHistory([record(null)], "inc-pending")[0];
  const failed = normalizeInspectionHistory([record({ status: "unavailable", completed_at: "2026-10-03T00:00:01Z",
                                                    error_type: "private diagnostic" })], "inc-pending")[0];
  assert.equal(unfinished.state, "unfinished"); assert.equal(unfinished.completedAt, null);
  assert.equal(failed.state, "unavailable"); assert.equal(failed.observation, null);
  assert.doesNotMatch(JSON.stringify(failed), /private/);
  assert.equal(unfinished.requester, "unauthenticated-demo");
  assert.equal(unfinished.authMode, "disabled");
});

test("projection drops raw credentials, provider notes and unrelated fields", () => {
  const value = record();
  value.request.approval_receipt = "private receipt";
  value.request.arguments.authorization = "private token";
  value.outcome.observation.note = "private provider message";
  const result = normalizeInspectionHistory([value], "inc-pending");
  assert.doesNotMatch(JSON.stringify(result), /private/);
  assert.equal(result[0].operationKey, "approved-operation-key");
});

test("cross-incident, duplicate, malformed, oversized and unsafe records reject the whole response", () => {
  const valid = record();
  const invalid = [
    {}, [null], [{ ...valid, incident_id: "other" }],
    [{ ...valid, request: { ...valid.request, incident_id: "other" } }], [valid, valid],
    Array(101).fill(valid), [{ ...valid, audit_id: "untrusted-id" }],
    [{ ...valid, started_at: "not-a-date" }],
    [{ ...valid, request: { ...valid.request, read_only: false } }],
    [{ ...valid, request: { ...valid.request, auth_mode: "SSO-verified" } }],
    [{ ...valid, request: { ...valid.request, approval_actor: "a".repeat(121) } }],
    [{ ...valid, request: { ...valid.request, arguments: { ...valid.request.arguments, revision: null } } }],
    [record({ ...valid.outcome, status: "resolved" })],
    [record({ ...valid.outcome, observation: { ...valid.outcome.observation, rollout_health_verified: true } })],
  ];
  for (const value of invalid) assert.throws(() => normalizeInspectionHistory(value, "inc-pending"));
});

test("empty history is empty evidence, not an execution outcome", () => {
  assert.deepEqual(normalizeInspectionHistory([], "inc-pending"), []);
});

for (const status of [401, 403, 404, 503]) {
  test(`history HTTP ${status} is redacted and never retried`, async () => {
    let calls = 0;
    const result = await loadInspectionHistory("http://backend", "inc-pending", async () => {
      calls++; return Response.json({ detail: "private server diagnostic" }, { status });
    });
    assert.equal(calls, 1); assert.equal(result.records, null);
    assert.ok(result.error); assert.doesNotMatch(result.error, /private/);
  });
}

test("history network failure clears data and does not trigger a fallback call", async () => {
  let calls = 0;
  const result = await loadInspectionHistory("http://backend", "inc-pending", async () => {
    calls++; throw new Error("private network context");
  });
  assert.equal(calls, 1); assert.equal(result.records, null);
  assert.doesNotMatch(result.error, /private/);
});
