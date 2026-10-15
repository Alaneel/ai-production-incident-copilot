import assert from "node:assert/strict";
import test from "node:test";
import { deploymentEventView, eventStatusLabels } from "../app/deployment-events.ts";

const window = ["2026-10-03T12:00:00Z", "2026-10-03T12:10:00Z"];
const snapshot = { id: "deploy-1", kind: "deployment", service: "payment-service", attributes: {
  data_origin: "kubernetes_api", namespace: "prod-sg", deployment_uid: "payment-owned", event_timeline: { status: "available" },
} };
const event = { id: "event-evidence-1", kind: "deployment_event", service: "payment-service", attributes: {
  data_origin: "kubernetes_api", namespace: "prod-sg", service: "payment-service", deployment_uid: "payment-owned",
  event_uid: "event-1", reason: "ScalingReplicaSet", timestamp_semantics: "event_observation_not_rollout_time",
  first_observed_at: "2026-10-03T12:02:00Z", last_observed_at: "2026-10-03T12:03:00Z", message: "private-injected-message",
} };
const view = (items = [snapshot, event], range = window) => deploymentEventView(["payment-service"], items, range);

test("valid event view retains evidence link, UTC times and a noncausal interval relationship", () => {
  const result = view();
  assert.equal(result.status, "available");
  assert.equal(result.omitted, 0);
  assert.deepEqual(result.rows, [{ evidenceId: event.id, reason: "ScalingReplicaSet",
    first: "2026-10-03T12:02:00.000Z", last: "2026-10-03T12:03:00.000Z",
    relation: "Interval overlaps sampled anomaly window" }]);
  assert.doesNotMatch(JSON.stringify(result), /private-injected-message/);
});

test("before, after and boundary interval relationships never claim rollout timing", () => {
  for (const [first, last, expected] of [
    ["11:01", "11:02", "Before sampled anomaly window"],
    ["12:11", "12:12", "After sampled anomaly window"],
    ["11:59", "12:00", "Interval overlaps sampled anomaly window"],
    ["11:00", "13:00", "Interval overlaps sampled anomaly window"],
  ]) {
    const item = structuredClone(event);
    item.attributes.first_observed_at = `2026-10-03T${first}:00Z`;
    item.attributes.last_observed_at = `2026-10-03T${last}:00Z`;
    assert.equal(view([snapshot, item]).rows[0].relation, expected);
  }
});

test("missing event time remains unknown and no anomaly window implies no comparison", () => {
  for (const field of ["first_observed_at", "last_observed_at"]) {
    const item = structuredClone(event);
    item.attributes[field] = null;
    const result = view([snapshot, item]);
    assert.equal(result.rows[0][field === "first_observed_at" ? "first" : "last"], null);
    assert.equal(result.rows[0].relation, "Event interval incomplete");
    assert.equal(view([snapshot, item], null).rows[0].relation, "Anomaly window unavailable");
  }
});

test("empty, unavailable, not-requested and legacy unknown states remain distinct", () => {
  const labels = new Set();
  for (const status of ["empty", "unavailable", "not_requested", "unknown"]) {
    const context = structuredClone(snapshot);
    context.attributes.event_timeline.status = status;
    const result = view([context, event]);
    assert.equal(result.status, status);
    assert.deepEqual(result.rows, []);
    labels.add(eventStatusLabels[result.status]);
  }
  assert.equal(labels.size, 4);
  assert.equal(view([]).status, "unknown");
  assert.equal(deploymentEventView([], [snapshot, event], window).status, "unknown");
});

test("wrong origin, identity, scope, reason or naive timestamps cannot become temporal context", () => {
  for (const [field, value] of [["data_origin", "synthetic"], ["deployment_uid", "other"],
    ["namespace", "other-ns"], ["service", "other-service"], ["reason", "private-injection"],
    ["event_uid", "<invalid>"], ["timestamp_semantics", "rollout_completed"],
    ["first_observed_at", "2026-10-03T12:02:00"], ["last_observed_at", "broken"]]) {
    const item = structuredClone(event);
    item.attributes[field] = value;
    assert.deepEqual(view([snapshot, item]).rows, []);
    assert.equal(view([snapshot, item]).omitted, 1);
  }
});

test("reversed or wholly missing event intervals are omitted; invalid metric windows are unknown", () => {
  const item = structuredClone(event);
  item.attributes.first_observed_at = "2026-10-03T12:04:00Z";
  assert.deepEqual(view([snapshot, item]).rows, []);
  item.attributes.first_observed_at = item.attributes.last_observed_at = null;
  assert.deepEqual(view([snapshot, item]).rows, []);
  for (const range of [[window[1], window[0]], ["invalid", window[1]], ["2026-10-03T12:00:00", window[1]]]) {
    assert.equal(view(undefined, range).rows[0].relation, "Anomaly window unavailable");
  }
});

test("duplicate evidence IDs or event UIDs do not produce ambiguous citations", () => {
  assert.deepEqual(view([snapshot, event, structuredClone(event)]).rows, []);
  const duplicate = structuredClone(event);
  duplicate.id = "different-evidence";
  assert.deepEqual(view([snapshot, event, duplicate]).rows, []);
  assert.equal(view([snapshot, event, duplicate]).status, "unknown");
  assert.deepEqual(view([snapshot, structuredClone(snapshot), event]).rows, []);
});

test("foreign services and synthetic deployment snapshots cannot be upgraded to observed events", () => {
  const foreign = structuredClone(event);
  foreign.service = "other-service";
  assert.deepEqual(view([snapshot, foreign]).rows, []);
  const synthetic = structuredClone(snapshot);
  synthetic.attributes.data_origin = "synthetic";
  assert.deepEqual(view([synthetic, event]).rows, []);
  assert.equal(view([synthetic, event]).status, "unknown");
});

test("malformed retained attribute objects cannot crash or invent an event view", () => {
  for (const attributes of [null, [], "bad"]) {
    assert.equal(view([{ ...snapshot, attributes }, event]).status, "unknown");
    assert.deepEqual(view([snapshot, { ...event, attributes }]).rows, []);
  }
});

test("oversized event list is rejected rather than silently truncated to a timeline", () => {
  const events = Array.from({ length: 101 }, (_, i) => ({ ...event, id: `ev-${i}`, attributes: { ...event.attributes, event_uid: `uid-${i}` } }));
  const result = view([snapshot, ...events]);
  assert.deepEqual(result.rows, []);
  assert.equal(result.omitted, 101);
});
