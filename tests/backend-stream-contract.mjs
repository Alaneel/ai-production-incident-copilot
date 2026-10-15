// Explicit cross-language smoke; intentionally not part of pure-frontend *.test.mjs.
// node --experimental-strip-types --test tests/backend-stream-contract.mjs
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { readFile, readdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { IncidentStreamDecoder, IncidentStreamProtocolError, streamConsoleIncident } from "../app/incident-stream.ts";
import { createConsoleTransport } from "../app/console-auth.ts";

const root = path.resolve(process.env.INCIDENT_STREAM_BACKEND_ROOT ?? fileURLToPath(new URL("../", import.meta.url)));
const python = path.join(root, ".venv/bin/python");
const question = "为什么 payment-service 今天延迟突然升高？";
const code = `
import json
from fastapi.testclient import TestClient
from incident_copilot.api import app
with TestClient(app) as client:
    modes = client.get("/health").json()
    assert modes["checkpoint"] == "memory" and modes["task_state"] == "memory"
    assert modes["tools"] == "fixture" and modes["reasoning"] == "deterministic"
    assert modes["api_auth"] == "required"
    result = client.post("/v1/incidents/stream", json={"question": ${JSON.stringify(question)}, "environment": "prod-sg"},
                         headers={"Authorization": "Bearer cross-language-public-reader-test-credential-0001"})
    assert result.status_code == 200 and result.headers["content-type"].startswith("text/event-stream")
    final_frame = [frame for frame in result.text.split("\\n\\n") if "event: complete\\n" in frame][-1]
    final = json.loads([line[6:] for line in final_frame.splitlines() if line.startswith("data: ")][0])
    assert client.portal.call(app.state.workflow.is_waiting_for_approval, final["incident_id"])
    retained = client.get("/v1/incidents/" + final["incident_id"],
                          headers={"Authorization": "Bearer cross-language-public-reader-test-credential-0001"})
    assert retained.status_code == 200 and retained.json() == final
    print(result.text, end="")
`;

async function fingerprint(directory, suffix) {
  const digest = createHash("sha256");
  async function visit(current) {
    const entries = await readdir(current, { withFileTypes: true });
    for (const item of entries.sort((left, right) => left.name.localeCompare(right.name))) {
      const filename = path.join(current, item.name);
      if (item.isDirectory() && !item.name.startsWith(".") && item.name !== "__pycache__") await visit(filename);
      else if (item.isFile() && suffix.test(item.name)) {
        digest.update(path.relative(directory, filename)); digest.update("\0"); digest.update(await readFile(filename)); digest.update("\0");
      }
    }
  }
  await visit(directory); return digest.digest("hex");
}

test("actual Python API/compiled LangGraph/Pydantic SSE decodes to the genuine final approval checkpoint", async () => {
  const sourceBefore = await fingerprint(path.join(root, "services/orchestrator/incident_copilot"), /\.py$/);
  const generated = spawnSync(python, ["-c", code], {
    cwd: root, encoding: "utf8", timeout: 60_000, maxBuffer: 32 * 1024 * 1024,
    // Deliberate allowlist: never inherit .env/model/DB/Redis/MCP/telemetry URLs.
    env: { PATH: "/usr/bin:/bin:/usr/sbin:/sbin", LANG: "en_US.UTF-8", PYTHONDONTWRITEBYTECODE: "1",
      PYTHONPATH: `${path.join(root, "services/orchestrator")}:${path.join(root, "services/mcp-server")}`,
      ORCHESTRATOR_AUTH_MODE: "required", ORCHESTRATOR_READ_TOKEN: "cross-language-public-reader-test-credential-0001",
      ORCHESTRATOR_OPERATOR_TOKEN: "cross-language-public-operator-test-credential-0002",
      ORCHESTRATOR_OPERATOR_ACTOR: "cross-language-public-test-operator" },
  });
  assert.equal(generated.error, undefined, "A configured backend .venv is required for this explicit smoke; no skip or synthetic substitute.");
  assert.equal(generated.status, 0, "The real isolated backend API must successfully emit its stream; raw provider output is not printed.");
  const decoder = new IncidentStreamDecoder(question), accepted = [], wireFrames = generated.stdout.split("\n\n").filter(Boolean);
  let broadQueryObserved = false;
  for (const wire of wireFrames) {
    const events = decoder.push(`${wire}\n\n`);
    accepted.push(...events);
    for (const event of events) if (event.incident?.evidence.some(item => item.kind === "log" && item.query === "")) broadQueryObserved = true;
  }
  decoder.finish();
  assert.equal(broadQueryObserved, true, "Exercise the actual broad-log query shape that broke hand-built frontend fixtures.");
  assert.ok(accepted.some(item => item.incident?.current_step === "query_telemetry" && item.incident.evidence.length > 0));
  assert.equal(accepted.at(-1).event, "complete"); assert.equal(accepted.at(-1).incident.status, "waiting_approval");
  assert.equal(accepted.at(-1).cursor, accepted.at(-1).incident.trace.length);
  assert.deepEqual(accepted.at(-1).incident, accepted.at(-2).incident);
  assert.ok(accepted.some(item => item.incident?.trace.some(trace => trace.event === "workflow_interrupted")));
  assert.equal(await fingerprint(path.join(root, "services/orchestrator/incident_copilot"), /\.py$/), sourceBefore);

  // Exercise the fetch/body/UTF-8 decoder with real backend bytes as well, rather
  // than assuming complete-frame parsing covers arbitrary network boundaries.
  // The fetch seam stays local and makes no second backend/model/action call.
  const endpoint = "http://127.0.0.1:8128";
  const readerToken = "cross-language-public-reader-test-credential-0001";
  const operatorToken = "cross-language-public-operator-test-credential-0002";
  const credentials = { mode: "protected", trustedEndpoint: endpoint, readToken: readerToken,
    operatorToken, operatorActor: "cross-language-public-test-operator" };
  for (const incomplete of [false, true]) {
    const body = incomplete ? wireFrames.filter(frame => !frame.split("\n").includes("event: complete")).join("\n\n") + "\n\n"
      : generated.stdout;
    const bytes = new TextEncoder().encode(body), chunks = [], calls = [], observed = [];
    const sizes = [1, 2, 3, 5, 7, 13, 61, 127, 1021];
    for (let offset = 0, index = 0; offset < bytes.length; index++) {
      const end = Math.min(bytes.length, offset + sizes[index % sizes.length]);
      chunks.push(bytes.slice(offset, end)); offset = end;
    }
    const transport = createConsoleTransport(endpoint, credentials, async (url, init) => {
      calls.push({ url, init });
      return new Response(new ReadableStream({ start(controller) {
        for (const chunk of chunks) controller.enqueue(chunk);
        controller.close();
      } }), { headers: { "Content-Type": "text/event-stream; charset=utf-8" } });
    });
    const result = await streamConsoleIncident(endpoint, question, transport, new AbortController().signal,
      incident => observed.push(incident));
    assert.equal(calls.length, 1, "No automatic POST retry or synchronous fallback.");
    assert.equal(calls[0].url, `${endpoint}/v1/incidents/stream`);
    assert.equal(calls[0].init.method, "POST");
    assert.equal(calls[0].init.headers.get("Authorization"), `Bearer ${readerToken}`);
    assert.deepEqual(JSON.parse(calls[0].init.body), { question, environment: "prod-sg" });
    assert.ok(!calls[0].url.includes(readerToken) && !calls[0].init.body.includes(readerToken)
      && !calls[0].init.body.includes(operatorToken));
    assert.equal(result.complete, !incomplete); assert.equal(result.uncertain, incomplete);
    assert.deepEqual(result.incident, accepted.at(-1).incident);
    assert.equal(result.incident.question, question, "Preserve the real backend's escaped Chinese JSON across byte chunks.");
    assert.deepEqual(observed, accepted.filter(event => !incomplete || event.event !== "complete").map(event => event.incident));
  }

  // The exact real payload still fails closed on type/scope tampering. Empty
  // query metadata is not permission to accept arbitrary query types or IDs.
  const telemetry = accepted.find(item => item.incident?.current_step === "query_telemetry").incident;
  for (const mutate of [item => item.evidence.find(evidence => evidence.kind === "log").query = false,
    item => item.environment = "foreign-ns", item => item.trace[0].sequence = 999]) {
    const altered = structuredClone(telemetry); mutate(altered);
    const validator = new IncidentStreamDecoder(question);
    assert.throws(() => validator.push(`id: ${altered.trace.length}\nevent: incident\ndata: ${JSON.stringify(altered)}\n\n`), IncidentStreamProtocolError);
  }
  const parserSha = createHash("sha256").update(await readFile(new URL("../app/incident-stream.ts", import.meta.url))).digest("hex");
  process.stdout.write(`${JSON.stringify({ proof: "actual-in-process-backend-to-frontend-wire-contract", reasoning: "deterministic",
    telemetry: "fixture-not-live", backend_source_sha256: sourceBefore, decoder_sha256: parserSha,
    frames: accepted.map(item => ({ event: item.event, cursor: item.cursor, step: item.incident?.current_step })),
    final_status: accepted.at(-1).incident.status, fragmented_body_verified: true,
    incomplete_body_retains_unconfirmed_prefix: true, model_calls: 0, real_kubernetes_actions: 0 })}\n`);
});
