import assert from "node:assert/strict";
import test from "node:test";
import { resolveCitations, sourceLink, evidenceAnchor } from "../app/evidence-citations.ts";

test("citations follow hypothesis IDs, not arrival order or a three-item cutoff", () => {
  const evidence = ["noise", "log", "runbook", "metric", "deploy", "trace"].map(id => ({ id }));
  const resolved = resolveCitations(evidence, ["deploy", "metric", "log", "runbook"]);
  assert.deepEqual(resolved.cited.map(item => item.id), ["deploy", "metric", "log", "runbook"]);
  assert.deepEqual(resolved.missing, []);
  assert.deepEqual(resolved.ambiguous, []);
});

test("uncited retained evidence cannot masquerade as root-cause citations", () => {
  assert.deepEqual(resolveCitations([{ id: "context" }], []).cited, []);
});

test("duplicate citation requests count once", () => {
  const result = resolveCitations([{ id: "metric" }], ["metric", "metric"]);
  assert.equal(result.cited.length, 1);
});

test("missing and conflicting duplicate evidence IDs are explicit integrity failures", () => {
  const result = resolveCitations([{ id: "dup", value: 1 }, { id: "dup", value: 2 }, { id: "valid" }], ["dup", "missing", "valid"]);
  assert.deepEqual(result.cited.map(item => item.id), ["valid"]);
  assert.deepEqual(result.ambiguous, ["dup"]);
  assert.deepEqual(result.missing, ["missing"]);
});

test("only credential-free http and https sources become external links", () => {
  for (const source of ["javascript:alert(1)", "data:text/html,test", "file:///tmp/private", "runbook://payments/pool", "incident://INC-1", "http://user:password@example.com", "/relative", "not a uri"]) {
    assert.equal(sourceLink(source), null);
  }
  assert.equal(sourceLink("https://example.com/runbooks/pool"), "https://example.com/runbooks/pool");
});

test("evidence IDs cannot break their fragment anchors", () => {
  assert.equal(evidenceAnchor("ev_1"), "evidence-ev_1");
  assert.equal(evidenceAnchor('ev/#"<tag>'), "evidence-ev%2F%23%22%3Ctag%3E");
});
