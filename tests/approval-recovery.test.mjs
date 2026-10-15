import assert from "node:assert/strict";
import test from "node:test";
import { refreshApproval, sendApproval } from "../app/approval-recovery.ts";

const id = "INC-RECOVERY";
const body = { decision: "approve", idempotency_key: "stable-operation-key" };
const json = (status) => Response.json({ incident_id: id, status });

function fixture(responses) {
  const calls = [];
  const transport = async (url, options) => {
    calls.push({ url, ...options });
    const response = responses.shift();
    if (response instanceof Error) throw response;
    assert.ok(response, "Unexpected request, possibly unsafe replay");
    return response;
  };
  return { calls, transport };
}

test("successful approval makes exactly one POST", async () => {
  const { calls, transport } = fixture([json("resolved")]);
  const result = await sendApproval("http://local", id, body, transport);
  assert.equal(result.uncertain, false);
  assert.equal(result.incident.status, "resolved");
  assert.deepEqual(calls.map(c => c.method), ["POST"]);
  assert.equal(JSON.parse(calls[0].body).idempotency_key, body.idempotency_key);
});

for (const status of ["resolved", "rejected", "remediation_failed", "remediation_pending"]) {
  test(`lost approval response recovers ${status} using GET, never replaying POST`, async () => {
    const { calls, transport } = fixture([new TypeError("Failed to fetch"), json(status)]);
    const result = await sendApproval("http://local", id, body, transport);
    assert.equal(result.incident.status, status);
    assert.equal(result.uncertain, false);
    assert.deepEqual(calls.map(c => c.method), ["POST", "GET"]);
    assert.equal(calls[1].cache, "no-store");
  });
}

test("conflicting approval reflects retained rejection, not attempted approval", async () => {
  const { calls, transport } = fixture([new Response(null, { status: 409 }), json("rejected")]);
  const result = await sendApproval("http://local", id, body, transport);
  assert.equal(result.incident.status, "rejected");
  assert.equal(result.uncertain, false);
  assert.equal(calls.length, 2);
});

test("stale waiting state after response loss remains locked", async () => {
  const { calls, transport } = fixture([new Error("disconnected"), json("waiting_approval")]);
  const result = await sendApproval("http://local", id, body, transport);
  assert.equal(result.uncertain, true);
  assert.match(result.error, /Refresh status/);
  assert.deepEqual(calls.map(c => c.method), ["POST", "GET"]);
});

test("failed read-only recovery preserves uncertainty", async () => {
  const { calls, transport } = fixture([new Error("disconnected"), new Response(null, { status: 503 })]);
  const result = await sendApproval("http://local", id, body, transport);
  assert.equal(result.uncertain, true);
  assert.equal(result.incident, null);
  assert.deepEqual(calls.map(c => c.method), ["POST", "GET"]);
});

test("wrong-incident and unknown-status responses cannot unlock mutations", async () => {
  for (const snapshot of [{ incident_id: "INC-OTHER", status: "resolved" }, { incident_id: id, status: "unknown" }, null]) {
    const { calls, transport } = fixture([Response.json(snapshot)]);
    const result = await refreshApproval("http://local", id, transport);
    assert.equal(result.uncertain, true);
    assert.equal(result.incident, null);
    assert.deepEqual(calls.map(c => c.method), ["GET"]);
  }
});

test("manual refresh is read-only and can recover an eventual terminal state", async () => {
  const { calls, transport } = fixture([json("mitigating"), json("resolved")]);
  assert.equal((await refreshApproval("http://local", id, transport)).uncertain, true);
  assert.equal((await refreshApproval("http://local", id, transport)).uncertain, false);
  assert.deepEqual(calls.map(c => c.method), ["GET", "GET"]);
});
