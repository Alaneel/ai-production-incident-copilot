import assert from "node:assert/strict";
import test from "node:test";
import { inspectOperation } from "../app/operation-inspection.ts";

const valid = (status = "marker_matches", source = "kubernetes_deployment_marker") => ({
  incident_id: "inc-pending", incident_status: "remediation_pending", tool: "rollback_release", read_only: true,
  observation: { status, source, read_only: true, rollout_health_verified: false, observed_at: "2026-10-02T15:00:00Z" },
});

test("inspection makes exactly one GET without target, credential, or action payload", async () => {
  const calls = [];
  const result = await inspectOperation("http://backend", "inc-pending", "rollback_release", async (url, options) => {
    calls.push({ url, options });
    return Response.json({ ...valid(), arbitrary_secret: "private", observation: { ...valid().observation, note: "private" } });
  });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://backend/v1/incidents/inc-pending/operation-status");
  assert.equal(calls[0].options.method, "GET");
  assert.equal(calls[0].options.body, undefined);
  assert.equal(calls[0].options.headers, undefined);
  assert.equal(calls[0].options.cache, "no-store");
  assert.equal(result.observation.status, "marker_matches");
  assert.equal(result.observation.incidentStatus, "remediation_pending");
  assert.doesNotMatch(JSON.stringify(result), /private/);
});

for (const status of ["marker_matches", "not_observed", "conflicting_marker", "unverified_marker"]) {
  test(`${status} remains an observation, not an incident status update`, async () => {
    const result = await inspectOperation("http://backend", "inc-pending", "rollback_release", async () => Response.json(valid(status)));
    assert.equal(result.observation.status, status);
    assert.equal(result.observation.incidentStatus, "remediation_pending");
  });
}

for (const status of [401, 403, 409, 502]) {
  test(`HTTP ${status} is redacted and never triggers recovery or execution calls`, async () => {
    let calls = 0;
    const result = await inspectOperation("http://backend", "inc-pending", "rollback_release", async () => {
      calls++;
      return Response.json({ detail: "private-provider-diagnostic" }, { status });
    });
    assert.equal(calls, 1);
    assert.equal(result.observation, null);
    assert.ok(result.error);
    assert.doesNotMatch(result.error, /private/);
  });
}

test("wrong incident/tool, unsafe flags and source/status mismatch fail closed", async () => {
  const changes = [
    { incident_id: "another-incident" }, { tool: "restart_deployment" }, { incident_status: "waiting_approval" },
    { read_only: false }, { observation: { ...valid().observation, read_only: 1 } },
    { observation: { ...valid().observation, rollout_health_verified: true } },
    { observation: { ...valid().observation, source: "fixture" } },
    { observation: { ...valid().observation, status: "resolved" } },
    { observation: { ...valid().observation, observed_at: "bad date" } },
  ];
  for (const change of changes) {
    const result = await inspectOperation("http://backend", "inc-pending", "rollback_release", async () => Response.json({ ...valid(), ...change }));
    assert.equal(result.observation, null);
    assert.ok(result.error);
  }
});

test("transport failure is not retried and cannot retain stale success", async () => {
  let calls = 0;
  const result = await inspectOperation("http://backend", "inc-pending", "rollback_release", async () => {
    calls++;
    throw new Error("private network data");
  });
  assert.equal(calls, 1);
  assert.equal(result.observation, null);
  assert.doesNotMatch(result.error, /private/);
});
