import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";
import * as preflight from "../app/preflight-target-review.ts";
import * as citations from "../app/evidence-citations.ts";
import * as location from "../app/incident-location.ts";
import * as recovery from "../app/approval-recovery.ts";
import * as recoveryVerification from "../app/recovery-verification.ts";
import * as consoleAuth from "../app/console-auth.ts";
import * as incidentStream from "../app/incident-stream.ts";

function target(tool = "restart_deployment") {
  return { tool, service: "payment-service", namespace: "prod-sg", cluster_context: "a".repeat(64),
    deployment_uid: "deployment-owned-123", resource_version: "8765", deployment_template_sha256: "b".repeat(64),
    revision: tool === "rollback_release" ? "141" : null,
    replicaset_uid: tool === "rollback_release" ? "replicaset-owned-141" : null,
    rollback_template_sha256: tool === "rollback_release" ? "c".repeat(64) : null };
}
function incident(tool = "restart_deployment", mode = "kubernetes-server-dry-run") {
  const context = mode === "simulation" ? null : target(tool);
  return { incident_id: "inc-preflight", trace_id: "trace-1", question: "Why is payment slow?",
    status: "waiting_approval", current_step: "human_approval", incident_type: "cpu_saturation",
    severity: "SEV-2", created_at: "2026-10-07T14:00:00Z", anomalous_window: null,
    services: ["payment-service"], evidence: [], hypotheses: [],
    remediation: { tool, title: "Review action", command_preview: "preview only", expected_result: "Expected, not confirmed",
      requires_approval: true, dry_run_passed: true, preflight_mode: mode,
      arguments: { service: "payment-service", namespace: "prod-sg", target_context: context,
        ...(tool === "rollback_release" ? { revision: "141" } : {}) } },
    trace: [{ sequence: 10, step: "propose_remediation", event: "tool_result", duration_ms: null,
      payload: { tool, dry_run: true, accepted: true, preflight_mode: mode, target_context: structuredClone(context) } }] };
}

test("server dry-run projection exposes exact retained restart metadata, not authorization", () => {
  const review = preflight.preflightTargetReview(incident());
  assert.equal(review.known, true);
  assert.equal(review.mode, "kubernetes-server-dry-run");
  assert.deepEqual(review.target, target());
  assert.equal(review.simulationScope, null);
  assert.match(review.label, /reported/);
  assert.match(review.detail, /did not persist/);
  assert.match(review.detail, /efficacy/);
});

test("rollback projection requires the exact revision, ReplicaSet UID and template", () => {
  const state = incident("rollback_release");
  const review = preflight.preflightTargetReview(state);
  assert.equal(review.known, true);
  assert.equal(review.rollback, true);
  assert.equal(review.target.replicaset_uid, "replicaset-owned-141");
  for (const field of ["revision", "replicaset_uid", "rollback_template_sha256"]) {
    const missing = structuredClone(state);
    missing.remediation.arguments.target_context[field] = null;
    missing.trace[0].payload.target_context[field] = null;
    assert.equal(preflight.preflightTargetReview(missing).known, false, field);
  }
});

test("explicit simulation stays distinct and never synthesizes Kubernetes identities", () => {
  for (const tool of ["restart_deployment", "rollback_release"]) {
    const review = preflight.preflightTargetReview(incident(tool, "simulation"));
    assert.equal(review.known, true);
    assert.equal(review.mode, "simulation");
    assert.equal(review.target, null);
    assert.equal(review.simulationScope.namespace, "prod-sg");
    assert.match(review.detail, /No Kubernetes admission check/);
    assert.doesNotMatch(JSON.stringify(review), /deployment-owned|replicaset-owned|a{64}/);
  }
});

test("legacy, malformed, unsafe or mismatched metadata stays unknown and does not leak target fields", () => {
  const mutations = [
    s => delete s.remediation.preflight_mode,
    s => s.remediation.preflight_mode = "dry-run",
    s => s.remediation.dry_run_passed = "true",
    s => delete s.remediation.arguments,
    s => delete s.remediation.arguments.target_context,
    s => s.remediation.arguments.target_context = null,
    s => s.remediation.arguments.target_context = [],
    s => s.remediation.arguments.namespace = "different-ns",
    s => s.remediation.arguments.service = "other-service",
    s => s.remediation.arguments.target_context.tool = "rollback_release",
    s => s.remediation.arguments.target_context.namespace = "Namespace With Spaces",
    s => s.remediation.arguments.target_context.cluster_context = "A".repeat(64),
    s => s.remediation.arguments.target_context.deployment_template_sha256 = "bad",
    s => s.remediation.arguments.target_context.deployment_uid = "private target uid",
    s => s.remediation.arguments.target_context.resource_version = 8765,
    s => s.remediation.arguments.namespace = "prod-sg\n",
    s => s.remediation.arguments.target_context.cluster_context = "a".repeat(64) + "\n",
    s => s.remediation.arguments.target_context.deployment_uid = "deployment-owned-123\n",
    s => s.remediation.arguments.target_context.resource_version = "8765\n",
    s => s.remediation.arguments.target_context.deployment_uid = "x".repeat(129),
    s => delete s.remediation.arguments.target_context.revision,
    s => s.remediation.arguments.target_context.extra = "private-provider-text",
    s => s.remediation.arguments.revision = "141",
    s => s.remediation.arguments.target_context.revision = "141",
    s => s.trace[0].payload.target_context.deployment_uid = "different-target",
    s => s.trace[0].payload.accepted = "true",
    s => s.trace[0].payload.dry_run = "true",
    s => s.trace[0].payload.preflight_mode = "simulation",
    s => delete s.trace[0].payload.target_context,
    s => s.trace[0].sequence = "10",
    s => s.trace[0].sequence = -1,
    s => s.trace = [],
    s => s.trace = null,
    s => s.trace[0].payload = [],
  ];
  for (const [index, mutate] of mutations.entries()) {
    const state = incident(); mutate(state);
    const review = preflight.preflightTargetReview(state);
    assert.equal(review.known, false, `mutation ${index}`);
    assert.equal(review.mode, "unknown", `mutation ${index}`);
    assert.equal(review.target, null);
    assert.equal(review.simulationScope, null);
    assert.doesNotMatch(JSON.stringify(review), /deployment-owned|private-provider-text|different-target/);
  }
  for (const value of [null, [], true, "private-provider-text", { remediation: [] }]) {
    assert.equal(preflight.preflightTargetReview(value).known, false);
  }
});

test("latest failed preflight or duplicate sequence cannot reuse earlier success", () => {
  const failed = incident();
  failed.trace.push({ ...structuredClone(failed.trace[0]), sequence: 11 });
  failed.trace[1].payload.accepted = false;
  assert.equal(preflight.preflightTargetReview(failed).known, false);
  const duplicate = incident();
  duplicate.trace.push(structuredClone(duplicate.trace[0]));
  assert.equal(preflight.preflightTargetReview(duplicate).known, false);
});

test("simulation rejects a live/malformed context and stale mode claims", () => {
  for (const mutate of [s => s.remediation.arguments.target_context = target(),
    s => s.trace[0].payload.target_context = {}, s => delete s.trace[0].payload.target_context,
    s => s.remediation.arguments.namespace = "bad namespace", s => s.remediation.arguments.revision = "1"]) {
    const state = incident("restart_deployment", "simulation"); mutate(state);
    assert.equal(preflight.preflightTargetReview(state).known, false);
  }
  const invalidRevision = incident("rollback_release", "simulation");
  invalidRevision.remediation.arguments.revision = "141\n";
  assert.equal(preflight.preflightTargetReview(invalidRevision).known, false);
});

const nativeRequire = createRequire(import.meta.url);
async function compileComponent(file, imports, runtimeProcess = { env: {} }) {
  const source = await readFile(new URL(file, import.meta.url), "utf8");
  const compiled = ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX,
  } }).outputText;
  const exports = {};
  new Function("require", "exports", "process", compiled)(name => Object.hasOwn(imports, name) ? imports[name] : nativeRequire(name), exports, runtimeProcess);
  return exports;
}
const components = await compileComponent("../app/PreflightTargetReview.tsx", { "./preflight-target-review": preflight });
const render = state => renderToStaticMarkup(React.createElement(components.PreflightTargetReview, { incident: state }));

test("actual target component renders exact identities with configuration and efficacy limitations", () => {
  const html = render(incident("rollback_release"));
  assert.match(html, /aria-label="Preflight and approval target review"/);
  assert.match(html, /Namespace: <code>prod-sg<\/code> · Deployment: <code>payment-service/);
  assert.match(html, /Deployment UID: <code>deployment-owned-123/);
  assert.match(html, /Observed resource version: <code>8765/);
  assert.match(html, /controllers may update it/);
  assert.match(html, /This is not a Kubernetes cluster UID/);
  assert.match(html, /Target revision: <code>141/);
  assert.match(html, /ReplicaSet UID: <code>replicaset-owned-141/);
  assert.match(html, /container images only, not a full release or PodTemplate undo/);
  assert.match(html, /does not authorize execution or confirm recovery/);
  assert.doesNotMatch(html, /<button/);
});

test("actual component displays simulation and unknown honestly, with HTML escaping", () => {
  const simulation = render(incident("rollback_release", "simulation"));
  assert.match(simulation, /Simulation preflight only/);
  assert.match(simulation, /Simulated scope: <code>prod-sg\/payment-service/);
  assert.match(simulation, /No live Deployment UID is available/);
  assert.doesNotMatch(simulation, /Deployment UID:|server-side dry-run reported/);
  const malformed = incident();
  malformed.remediation.arguments.target_context.extra = "<script>private-provider-text</script>";
  const unknown = render(malformed);
  assert.match(unknown, /Preflight unknown or inconsistent/);
  assert.match(unknown, /Run a new diagnosis or renew preflight/);
  assert.doesNotMatch(unknown, /private-provider-text|<script>|deployment-owned/);
  const escaped = incident();
  escaped.remediation.arguments.target_context.deployment_uid = "<script>escaped</script>";
  escaped.trace[0].payload.target_context.deployment_uid = escaped.remediation.arguments.target_context.deployment_uid;
  assert.match(render(escaped), /&lt;script&gt;escaped&lt;\/script&gt;/);
  assert.doesNotMatch(render(escaped), /<script>/);
});

// Seed retained state and explicitly trusted development credentials in the
// actual console. Display conditions and button attributes still come from TSX;
// no HTTP request is made, and child panels are isolated here.
async function renderConsole(state) {
  const endpoint = "http://127.0.0.1:8000";
  const authState = { 12: endpoint, 13: "test-reader-" + "r".repeat(32),
    14: "test-operator-" + "o".repeat(32), 15: "test-token-bound-operator" };
  let stateIndex = 0;
  const seededReact = { ...React, useState(initial) {
    stateIndex += 1;
    return [stateIndex === 4 ? state : authState[stateIndex] ?? initial, () => {}];
  } };
  const stubPanel = () => null;
  const console = await compileComponent("../app/IncidentConsole.tsx", {
    react: seededReact, "./preflight-target-review": preflight,
    "./PreflightTargetReview": components, "./approval-recovery": recovery,
    "./recovery-verification": recoveryVerification,
    "./incident-location": location, "./evidence-citations": citations,
    "./EvidenceExplorer": { EvidenceExplorer: stubPanel }, "./ActionEvidenceReview": { ActionEvidenceReview: stubPanel },
    "./DeploymentEventTimeline": { DeploymentEventTimeline: stubPanel }, "./DependencyImpactList": { DependencyImpactList: stubPanel },
    "./OperationInspectionPanel": { OperationInspectionPanel: stubPanel },
    "./RecoveryVerificationPanel": { RecoveryVerificationPanel: stubPanel },
    "./RootCauseCandidates": { RootCauseCandidates: stubPanel },
    "./console-auth": consoleAuth,
    "./incident-stream": incidentStream,
  }, { env: { NEXT_PUBLIC_ORCHESTRATOR_URL: endpoint } });
  return renderToStaticMarkup(React.createElement(console.IncidentConsole));
}

test("actual console disables unknown/legacy approval but leaves rejection available", async () => {
  const state = incident();
  delete state.remediation.preflight_mode;
  const html = await renderConsole(state);
  assert.match(html, /class="approve-button" disabled=""/);
  assert.match(html, /class="reject-button">Reject/);
  assert.match(html, /PREFLIGHT UNKNOWN/);
  assert.match(html, /Rejection remains available/);
});

test("actual console keeps explicit simulation review distinct from server dry-run", async () => {
  const simulation = await renderConsole(incident("restart_deployment", "simulation"));
  assert.match(simulation, /<b>SIMULATION<\/b>/);
  assert.match(simulation, /This is a simulated action. No Kubernetes operation will be submitted/);
  assert.match(simulation, /class="approve-button">Approve restart/);
  const live = await renderConsole(incident());
  assert.match(live, /<b>SERVER DRY RUN<\/b>/);
  assert.match(live, /class="approve-button">Approve restart/);
  assert.doesNotMatch(live, /recovery confirmed|mitigation verified/);
});

test("console source preserves selected-command fallback and blocks programmatic unknown approval", async () => {
  const source = await readFile(new URL("../app/IncidentConsole.tsx", import.meta.url), "utf8");
  assert.match(source, /if \(decision === "approve" && !preflightReview.known\) return/);
  assert.match(source, /No remediation selected; investigate an incident first\./);
});
