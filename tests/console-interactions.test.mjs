import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";
import * as consoleAuth from "../app/console-auth.ts";
import * as incidentList from "../app/incident-list.ts";
import * as approvals from "../app/approval-recovery.ts";
import * as location from "../app/incident-location.ts";
import * as citations from "../app/evidence-citations.ts";
import * as preflight from "../app/preflight-target-review.ts";
import * as recovery from "../app/recovery-verification.ts";
import * as incidentStream from "../app/incident-stream.ts";

// Offline demo interactions of the actual console (no orchestrator configured).
// Hooks are replaced so the parent can be driven directly; timers are captured
// so the demo replay can be stepped deterministically.
const source = await readFile(new URL("../app/IncidentConsole.tsx", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText;
const nativeRequire = createRequire(import.meta.url);
const stubPanel = () => null;

function load(react, env = {}) {
  const imports = { react, "./console-auth": consoleAuth, "./incident-list": incidentList, "./approval-recovery": approvals, "./incident-location": location,
    "./evidence-citations": citations, "./preflight-target-review": preflight, "./recovery-verification": recovery,
    "./incident-stream": incidentStream };
  for (const name of ["EvidenceExplorer", "ActionEvidenceReview", "PreflightTargetReview", "DeploymentEventTimeline", "DependencyImpactList",
    "OperationInspectionPanel", "RecoveryVerificationPanel", "RootCauseCandidates"]) imports[`./${name}`] = { [name]: stubPanel };
  const timers = [];
  const exports = {};
  new Function("require", "exports", "process", "setTimeout", "clearTimeout", compiled)(
    name => imports[name] ?? nativeRequire(name), exports, { env },
    (fn, ms) => { timers.push({ fn, ms }); return timers.length; }, () => {});
  return { exports, timers };
}

function harness(env = {}, { mount = false } = {}) {
  const values = [], refs = [];
  let hook = 0, ref = 0, mounted = false;
  // With `mount`, the console's single mount effect runs once, as after hydration.
  const fakeReact = { ...React, useMemo: callback => callback(),
    useEffect(effect) { if (mount && !mounted) { mounted = true; effect(); } },
    useState(initial) {
      const index = hook++;
      if (!(index in values)) values[index] = initial;
      return [values[index], value => { values[index] = typeof value === "function" ? value(values[index]) : value; }];
    },
    useRef(initial) { const index = ref++; return refs[index] ??= { current: initial }; },
  };
  const { exports, timers } = load(fakeReact, env);
  const render = () => { hook = 0; ref = 0; return exports.IncidentConsole(); };
  const find = (element, predicate) => {
    if (!element || typeof element !== "object") return null;
    if (predicate(element)) return element;
    return React.Children.toArray(element.props?.children).map(item => find(item, predicate)).find(Boolean) ?? null;
  };
  const text = element => typeof element === "string" || typeof element === "number" ? String(element)
    : React.Children.toArray(element?.props?.children).map(text).join("");
  const steps = tree => React.Children.toArray(find(tree, item => item.props?.className === "workflow-list").props.children)
    .map(item => item.props.className).join(" ");
  async function flush() { while (timers.length) { const next = timers.shift(); next.fn(); } }
  return { render, find, text, steps, timers, flush };
}

test("demo snapshot renders working controls instead of inert decoration", () => {
  const { exports } = load(React);
  const html = renderToStaticMarkup(React.createElement(exports.IncidentConsole));
  assert.match(html, /<button type="button" class="icon-button" aria-label="Open command palette" aria-haspopup="dialog">/);
  assert.doesNotMatch(html, /<button[^>]*class="(?:env-button|avatar)"/);
  assert.match(html, /<span class="env-button"/);
  assert.match(html, /aria-label="Copy remediation command"/);
  assert.match(html, /class="metric-chart warning inspectable" role="img" aria-label="P95 latency trend\. Use arrow keys to read samples\." tabindex="0"/);
  // The command is tokenised so flags never split, but its text is unchanged.
  assert.match(html, /<span>--to-revision=141<\/span>/);
  const command = html.match(/<span>\$<\/span><code>(.*?)<\/code>/s)[1].replace(/<[^>]+>/g, "");
  assert.equal(command, "kubectl rollout undo deployment/payment-service --to-revision=141 -n payments");
});

test("Analyze replays the demo workflow locally, step by step, without contacting a backend", async () => {
  const ui = harness();
  let tree = ui.render();
  assert.equal(ui.steps(tree), "complete complete complete complete running queued");
  ui.find(tree, item => item.type === "form").props.onSubmit({ preventDefault() {} });

  tree = ui.render();
  assert.equal(ui.steps(tree), "running queued queued queued queued queued");
  assert.match(ui.text(ui.find(tree, item => item.props?.className === "api-notice running")), /No backend, model or cluster is contacted/);
  assert.equal(ui.text(ui.find(tree, item => item.type === "button" && item.props.type === "submit")), "Replaying…");
  assert.ok(ui.find(tree, item => item.props?.className?.includes("trace-panel")), "replay opens the agent trace");
  ui.find(tree, item => item.type === "button" && ui.text(item) === "Incident overview").props.onClick();
  tree = ui.render();
  // Hypothesis and remediation are held back (and inert) until their step completes.
  assert.equal(ui.find(tree, item => item.type === "section" && item.props.className?.includes("remediation-panel")).props.inert, true);

  // A decision cannot be recorded while the replay is still running.
  ui.find(tree, item => item.props?.className === "reject-button")?.props.onClick();
  tree = ui.render();
  assert.equal(ui.find(tree, item => item.props?.className?.startsWith("decision")), null);

  ui.timers.shift().fn();
  assert.equal(ui.steps(ui.render()), "complete running queued queued queued queued");
  await ui.flush();
  tree = ui.render();
  ui.find(tree, item => item.type === "button" && ui.text(item) === "Incident overview").props.onClick();
  tree = ui.render();
  assert.equal(ui.steps(tree), "complete complete complete complete complete complete");
  assert.match(ui.text(ui.find(tree, item => item.props?.className === "api-notice connected")), /rollback is proposed and held for human approval/);
  assert.match(ui.text(ui.find(tree, item => item.props?.className === "investigating")), /Awaiting approval/);
  assert.equal(ui.find(tree, item => item.type === "section" && item.props.className?.includes("remediation-panel")).props.inert, undefined);
  // Approval stays gated by the unknown demo preflight; rejection stays available.
  assert.equal(ui.find(tree, item => item.props?.className === "approve-button").props.disabled, true);
  assert.notEqual(ui.find(tree, item => item.props?.className === "reject-button").props.disabled, true);
});

test("command palette exposes navigation, replay and copy commands for the demo", () => {
  const ui = harness();
  const tree = ui.render();
  const palette = ui.find(tree, item => Array.isArray(item.props?.commands));
  const ids = palette.props.commands.map(command => command.id);
  assert.deepEqual(ids, ["ask", "replay", "overview", "map", "hypothesis", "remediation", "trace", "inbox", "theme", "copy-command", "copy-trace"]);
  palette.props.commands.find(command => command.id === "trace").run();
  assert.ok(ui.find(ui.render(), item => item.props?.className?.includes("trace-panel")));
});

test("a same-origin build names no backend until the page that served it is known", () => {
  const sameOrigin = { NEXT_PUBLIC_ORCHESTRATOR_URL: "same-origin" };
  const html = renderToStaticMarkup(React.createElement(load(React, sameOrigin).exports.IncidentConsole));
  assert.match(html, /<main class="shell" data-backend="same-origin">/);
  assert.match(html, /<code>no backend configured<\/code>/);
  assert.match(html, /<span class="connection-state idle">Demo data<\/span>/);
  // It describes itself as what it is: served by its API, token based, not per-user sign-in.
  assert.match(html, /<strong>API connection — not SSO<\/strong>/);
  assert.match(html, /This page is served by the API it talks to and sends tokens nowhere else\./);
  assert.doesNotMatch(html, /Development integration only/);
  // A build bound to a fixed backend, or to none, is never marked same-origin.
  for (const env of [{}, { NEXT_PUBLIC_ORCHESTRATOR_URL: "https://opspilot-api.example.com" }]) {
    const other = renderToStaticMarkup(React.createElement(load(React, env).exports.IncidentConsole));
    assert.doesNotMatch(other, /data-backend/);
    assert.match(other, /<strong>Local API connection — not SSO<\/strong>/);
    assert.match(other, /Development integration only\./);
  }
});

test("after hydration a same-origin build uses only the origin that served it, and only when that is safe", async (t) => {
  t.after(() => { delete globalThis.window; });
  const backendOf = async (href) => {
    globalThis.window = { location: { href, origin: new URL(href).origin } };
    const ui = harness({ NEXT_PUBLIC_ORCHESTRATOR_URL: "same-origin" }, { mount: true });
    ui.render();
    await new Promise(resolve => queueMicrotask(resolve));
    const tree = ui.render();
    return { backend: ui.text(ui.find(tree, item => item.props?.className === "field-value")),
      state: ui.text(ui.find(tree, item => item.props?.className?.startsWith("connection-state"))),
      notice: ui.text(ui.find(tree, item => item.props?.className === "api-notice warn")) };
  };
  // A port-forward or a TLS ingress: the backend is the page's own origin, still waiting for explicit trust.
  for (const [href, origin] of [["http://localhost:8080/?incident=INC-1", "http://localhost:8080"], ["https://opspilot.example.com/", "https://opspilot.example.com"]]) {
    const view = await backendOf(href);
    assert.equal(view.backend, origin);
    assert.equal(view.state, "Setup required");
    assert.match(view.notice, /Confirm the exact trusted backend before sending credentials or requests|Enter reader credentials/);
  }
  // Served over plain HTTP on a network: no backend is accepted, so no credential can be sent in the clear.
  const insecure = await backendOf("http://opspilot.internal/");
  assert.equal(insecure.backend, "Unavailable or unsafe URL");
  assert.match(insecure.notice, /Configure a valid HTTPS or HTTP loopback incident backend\. No credential or request was sent\./);
});
