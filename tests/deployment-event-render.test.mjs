import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";
import * as events from "../app/deployment-events.ts";
import * as citations from "../app/evidence-citations.ts";

// Render the actual TSX component, not a hand-written HTML representation.
const source = await readFile(new URL("../app/DeploymentEventTimeline.tsx", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: {
  module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX,
} }).outputText;
const nativeRequire = createRequire(import.meta.url);
const exports = {};
new Function("require", "exports", compiled)(name => {
  if (name === "./deployment-events") return events;
  if (name === "./evidence-citations") return citations;
  return nativeRequire(name);
}, exports);
const render = props => renderToStaticMarkup(React.createElement(exports.DeploymentEventTimeline, props));
const snapshot = { id: "deploy-1", kind: "deployment", service: "payment-service", attributes: {
  data_origin: "kubernetes_api", namespace: "prod-sg", deployment_uid: "payment-owned", event_timeline: { status: "available" },
} };
const event = { id: "event/1", kind: "deployment_event", service: "payment-service", attributes: {
  data_origin: "kubernetes_api", namespace: "prod-sg", service: "payment-service", deployment_uid: "payment-owned",
  event_uid: "event-1", reason: "ScalingReplicaSet", timestamp_semantics: "event_observation_not_rollout_time",
  first_observed_at: "2026-10-03T12:02:00Z", last_observed_at: "2026-10-03T12:03:00Z",
  message: "<script>private-message</script>",
} };
const props = { services: ["payment-service"], evidence: [snapshot, event],
  window: ["2026-10-03T12:00:00Z", "2026-10-03T12:10:00Z"] };

test("actual event component renders accessible table, evidence anchor and noncausal limitations", () => {
  const html = render(props);
  assert.match(html, /aria-label="Deployment event observations"/);
  assert.match(html, /<caption>/);
  assert.match(html, /scope="col"/);
  assert.match(html, /scope="row"/);
  assert.match(html, /data-label="First \/ latest observation"/);
  assert.match(html, /data-label="Temporal relationship"/);
  assert.match(html, /href="#evidence-event%2F1"/);
  assert.match(html, /2026-10-03T12:02:00.000Z/);
  assert.match(html, /Interval overlaps sampled anomaly window/);
  assert.match(html, /does not prove an individual occurrence/);
  assert.match(html, /does not authorize remediation/);
  assert.doesNotMatch(html, /private-message|<script>|<button/);
});

test("actual component preserves unknown event time and unavailable query without inventing chronology", () => {
  const incomplete = structuredClone(event);
  incomplete.attributes.first_observed_at = null;
  const html = render({ ...props, evidence: [snapshot, incomplete] });
  assert.match(html, /First: Unknown/);
  assert.match(html, /Event interval incomplete/);
  const unavailable = structuredClone(snapshot);
  unavailable.attributes.event_timeline.status = "unavailable";
  const failedHtml = render({ ...props, evidence: [unavailable] });
  assert.match(failedHtml, /Event query unavailable/);
  assert.match(failedHtml, /does not prove that no deployment occurred/);
  assert.doesNotMatch(failedHtml, /<table|ScalingReplicaSet/);
});

test("incident overview integrates the event component only with actual incident state", async () => {
  const consoleSource = await readFile(new URL("../app/IncidentConsole.tsx", import.meta.url), "utf8");
  assert.match(consoleSource, /incident && <DeploymentEventTimeline services=\{incident.services\} evidence=\{incident.evidence\} window=\{incident.anomalous_window\}/);
});
