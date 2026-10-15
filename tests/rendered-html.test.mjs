import assert from "node:assert/strict";
import { access, readFile, readdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { Miniflare, convertV4MiniflareOptions } from "miniflare";
import test from "node:test";

async function render(t) {
  // Cloudflare-target output now imports cloudflare:workers. Exercise the real
  // local workerd runtime instead of impersonating that module in Node.
  const outputRoot = fileURLToPath(new URL("../dist/server/", import.meta.url));
  const modulePaths = (await readdir(outputRoot, { recursive: true }))
    .filter((name) => name.endsWith(".js"));
  modulePaths.sort((a, b) => a === "index.js" ? -1 : b === "index.js" ? 1 : a.localeCompare(b));
  const modules = modulePaths.map((name) => ({ type: "ESModule", path: `${outputRoot}${name}` }));
  let outboundCalls = 0;
  const runtime = new Miniflare({
    ...convertV4MiniflareOptions({
      host: "127.0.0.1", port: 0,
      modules,
      modulesRoot: outputRoot,
      compatibilityDate: "2026-10-06",
      compatibilityFlags: ["nodejs_compat"],
      serviceBindings: {
        ASSETS: async (request) => {
          const url = new URL(request.url);
          if (url.pathname === "/og.png") {
            return new Response(new Uint8Array([137, 80, 78, 71]), {
              headers: { "content-type": "image/png" },
            });
          }
          return new Response("Not found", { status: 404 });
        },
      },
      outboundService: async () => {
        outboundCalls += 1;
        return new Response("External transport forbidden in rendering test", { status: 502 });
      },
    }),
    telemetry: { enabled: false },
  });
  t.after(async () => {
    await runtime.dispose();
    assert.equal(outboundCalls, 0, "Console SSR must not contact external systems");
  });
  return runtime.dispatchFetch("http://localhost/", {
    headers: { accept: "text/html", host: "localhost" },
  });
}

test("server renders the incident console and production metadata", async (t) => {
  const response = await render(t);
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(html, /OpsPilot/);
  assert.match(html, /Payment latency regression/);
  assert.match(html, /Database connection pool exhaustion/);
  assert.match(html, /HUMAN APPROVAL REQUIRED/);
  assert.match(html, /Agent trace/);
  assert.match(html, /property="og:image"/);
  assert.match(html, /\/og\.png/);
  assert.doesNotMatch(html, /codex-preview/);
  assert.doesNotMatch(html, /react-loading-skeleton/);
});

test("starter preview is removed and interactions remain client-side", async () => {
  const [page, consoleSource, authSource, streamSource, layout, packageJson] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/IncidentConsole.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/console-auth.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/incident-stream.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/layout.tsx", import.meta.url), "utf8"),
    readFile(new URL("../package.json", import.meta.url), "utf8"),
  ]);

  assert.match(page, /<IncidentConsole \/>/);
  assert.match(consoleSource, /"use client"/);
  assert.match(consoleSource, /async function decide/);
  assert.match(consoleSource, /streamConsoleIncident<ApiIncident>/);
  assert.match(authSource, /transport\(`\$\{backend\}\/v1\/incidents`/);
  assert.match(streamSource, /transport\(`\$\{backend\}\/v1\/incidents\/stream`/);
  assert.match(consoleSource, /idempotency_key/);
  assert.match(layout, /generateMetadata/);
  assert.doesNotMatch(packageJson, /react-loading-skeleton/);

  await assert.rejects(access(new URL("../app/_sites-preview", import.meta.url)));
  await access(new URL("../public/og.png", import.meta.url));
});
