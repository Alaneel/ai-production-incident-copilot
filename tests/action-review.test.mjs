import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";
import * as reviewModule from "../app/action-review.ts";
import * as citations from "../app/evidence-citations.ts";

function incident() {
  return { services: ["payment-service"], incident_type: "database_connection_pool_exhaustion",
    remediation: { tool: "rollback_release" },
    hypotheses: [{ id: "hyp-1", rank: 1, cause: "Pool exhaustion", evidence_ids: ["metric/1", "log-1"] }],
    evidence: [{ id: "metric/1", kind: "metric", service: "payment-service" },
      { id: "log-1", kind: "log", service: "payment-service" }],
    trace: [{ sequence: 30, event: "action_signal_applicability_checked", payload: {
      allowed: true, tool: "rollback_release", profile: "database_connection_pool_exhaustion",
      primary_service: "payment-service", leading_hypothesis_id: "hyp-1", leading_cause: "Pool exhaustion",
      leading_evidence_ids: ["metric/1", "log-1"], cited_primary_signal_ids: ["metric/1", "log-1"],
      cause_family_check: { status: "recognized_family_match" },
      reason: "private-provider-text", semantic_entailment_verified: true, causation_verified: true,
    } }] };
}

test("scoped retained assessment projects citations but never certifies semantics or efficacy", () => {
  const result = reviewModule.actionReviewView(incident());
  assert.equal(result.scoped, true);
  assert.deepEqual(result.evidenceIds, ["metric/1", "log-1"]);
  assert.match(result.signalLabel, /efficacy not established/);
  assert.match(result.familyLabel, /not semantic validation/);
  assert.doesNotMatch(JSON.stringify(result), /private-provider-text/);
});

test("all known cause-family unresolved states remain distinct", () => {
  const labels = new Set();
  for (const status of ["recognized_family_match", "explicit_family_mismatch", "unrecognized", "unresolved_negation", "multiple_families_unresolved"]) {
    const state = incident();
    state.trace[0].payload.cause_family_check.status = status;
    labels.add(reviewModule.actionReviewView(state).familyLabel);
  }
  assert.equal(labels.size, 5);
});

test("changed hypothesis, profile, primary service, action or citation scope invalidates stale assessment", () => {
  const mutations = [s => s.hypotheses[0].cause = "CPU saturation", s => s.hypotheses[0].id = "different-hyp",
    s => s.hypotheses[0].evidence_ids.push("extra"), s => s.services[0] = "other-service",
    s => s.incident_type = "cpu_saturation", s => s.remediation.tool = "restart_deployment",
    s => s.trace[0].payload.cited_primary_signal_ids = ["uncited-id"]];
  for (const mutate of mutations) {
    const state = incident(); mutate(state);
    assert.equal(reviewModule.actionReviewView(state).scoped, false);
  }
});

test("missing, duplicate, foreign or wrong-kind signal citations cannot produce a passed review", () => {
  const mutations = [s => s.evidence.pop(), s => s.evidence.push(s.evidence[0]),
    s => s.evidence[0].service = "ledger-db", s => s.evidence[0].kind = "deployment_event",
    s => s.trace[0].payload.cited_primary_signal_ids = []];
  for (const mutate of mutations) {
    const state = incident(); mutate(state);
    assert.equal(reviewModule.actionReviewView(state).scoped, false);
  }
});

test("latest assessment is used; ambiguous, legacy, malformed and truthy non-boolean results are unknown", () => {
  const old = incident();
  old.trace.push({ ...structuredClone(old.trace[0]), sequence: 31 });
  old.trace[1].payload.allowed = false;
  old.remediation.tool = "none";
  assert.match(reviewModule.actionReviewView(old).signalLabel, /blocked/);
  const mutations = [s => s.trace = [], s => s.trace.push(structuredClone(s.trace[0])),
    s => s.trace[0].payload = null, s => s.trace[0].payload.allowed = "true",
    s => delete s.trace[0].payload.leading_hypothesis_id];
  for (const mutate of mutations) {
    const state = incident(); mutate(state);
    assert.equal(reviewModule.actionReviewView(state).scoped, false);
  }
});

test("explicit demo summary compatibility does not become a production verification label", () => {
  const state = incident();
  state.trace[0].payload.simulation_only_summary_compatibility = true;
  assert.match(reviewModule.actionReviewView(state).signalLabel, /not production verification/);
});

const source = await readFile(new URL("../app/ActionEvidenceReview.tsx", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText;
const nativeRequire = createRequire(import.meta.url), exports = {};
new Function("require", "exports", compiled)(name => name === "./action-review" ? reviewModule
  : name === "./evidence-citations" ? citations : nativeRequire(name), exports);

test("actual React review component renders caveats and evidence anchors without execution controls", () => {
  const html = renderToStaticMarkup(React.createElement(exports.ActionEvidenceReview, { incident: incident() }));
  assert.match(html, /aria-label="Action evidence review"/);
  assert.match(html, /Semantic entailment, release causation and mitigation efficacy are not verified/);
  assert.match(html, /href="#evidence-metric%2F1"/);
  assert.match(html, /not current execution authorization/);
  assert.doesNotMatch(html, /<button|private-provider-text/);
});

test("actual review component explicitly warns on unrecognized and missing legacy assessments", () => {
  const state = incident();
  state.trace[0].payload.cause_family_check.status = "unrecognized";
  assert.match(renderToStaticMarkup(React.createElement(exports.ActionEvidenceReview, { incident: state })), /wording unrecognized/);
  state.trace = [];
  assert.match(renderToStaticMarkup(React.createElement(exports.ActionEvidenceReview, { incident: state })), /No current, scoped signal assessment/);
});
