import assert from "node:assert/strict";
import test from "node:test";
import { incidentLocationUrl, readIncidentLocation, restoredApprovalUncertain } from "../app/incident-location.ts";

test("fresh console has no saved incident", () => {
  assert.equal(readIncidentLocation("http://local/"), null);
});

test("incident URL preserves unrelated navigation and survives reload", () => {
  const url = incidentLocationUrl("http://local/?view=trace#evidence", "INC-123ABC", false);
  assert.deepEqual(readIncidentLocation(url), { incidentId: "INC-123ABC", approvalPending: false });
  assert.equal(new URL(url).searchParams.get("view"), "trace");
  assert.equal(new URL(url).hash, "#evidence");
});

test("ambiguity marker survives URL restoration without carrying execution data", () => {
  const url = incidentLocationUrl("http://local/", "INC-123ABC", true);
  assert.deepEqual([...new URL(url).searchParams.keys()], ["incident", "approval_pending"]);
  assert.equal(readIncidentLocation(url).approvalPending, true);
  const resolved = incidentLocationUrl(url, "INC-123ABC", false);
  assert.equal(readIncidentLocation(resolved).approvalPending, false);
});

test("any supplied pending marker is treated conservatively", () => {
  assert.equal(readIncidentLocation("http://local/?incident=INC-123&approval_pending=0").approvalPending, true);
});

test("invalid incident references are rejected", () => {
  for (const id of ["", "../../approval", "https://other", "INC-<script>", "INC-" + "a".repeat(101)]) {
    assert.throws(() => readIncidentLocation(`http://local/?incident=${encodeURIComponent(id)}`));
  }
});

test("normal paused incident may be approved after readonly restoration", () => {
  assert.equal(restoredApprovalUncertain("waiting_approval", false), false);
});

test("paused snapshot cannot prove a pending approval was never executed", () => {
  assert.equal(restoredApprovalUncertain("waiting_approval", true), true);
  assert.equal(restoredApprovalUncertain("mitigating", false), true);
  assert.equal(restoredApprovalUncertain(null, false), true);
  assert.equal(restoredApprovalUncertain("unknown", false), true);
});

test("terminal states resolve browser ambiguity but never imply replay", () => {
  for (const status of ["resolved", "rejected", "diagnosed", "insufficient_evidence", "remediation_failed", "remediation_pending"]) {
    assert.equal(restoredApprovalUncertain(status, true), false);
  }
});
