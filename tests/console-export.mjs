import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile, readdir } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { documentReferences, inlineScriptHashes } from "../build/export-console.mjs";

// Checks the bundle `npm run build:console` just wrote to dist/console: the
// files the orchestrator image serves (see test:console-export in package.json).
const root = new URL("../dist/console/", import.meta.url);
const manifest = JSON.parse(await readFile(new URL("console-manifest.json", root), "utf8"));
const html = await readFile(new URL("index.html", root), "utf8");
const names = Object.keys(manifest.files);

test("the manifest lists exactly the exported files, with their real sizes and hashes", async () => {
  assert.equal(manifest.schema, 1);
  assert.equal(manifest.backend, "same-origin");
  const directory = fileURLToPath(root);
  const onDisk = (await readdir(directory, { recursive: true, withFileTypes: true })).filter(entry => entry.isFile())
    .map(entry => path.relative(directory, path.join(entry.parentPath, entry.name)).split(path.sep).join("/")).sort();
  assert.deepEqual(onDisk, [...names, "console-manifest.json"].sort());
  for (const [name, entry] of Object.entries(manifest.files)) {
    const body = await readFile(new URL(name, root));
    assert.equal(body.length, entry.bytes, name);
    assert.equal(createHash("sha256").update(body).digest("hex"), entry.sha256, name);
  }
});

test("only the page, hashed build output and two public files are exported", () => {
  for (const name of names) {
    assert.ok(name === "index.html" || name === "favicon.svg" || name === "third-party-licenses.txt" || name.startsWith("_next/static/"), name);
    assert.doesNotMatch(name, /\.map$|wrangler|\.vite|_headers|og\.png|hosting/);
  }
  const types = new Set(Object.values(manifest.files).map(entry => entry.type));
  assert.deepEqual([...types].sort(), ["font/woff2", "image/svg+xml", "text/css; charset=utf-8", "text/html; charset=utf-8",
    "text/javascript; charset=utf-8", "text/plain; charset=utf-8"]);
  assert.ok(names.some(name => name.endsWith(".woff2")), "fonts are self-hosted");
});

test("the page is the same-origin build and loads nothing from another origin", () => {
  assert.match(html, /<main class="shell" data-backend="same-origin">/);
  assert.match(html, /Payment latency regression/);
  const references = documentReferences(html);
  assert.ok(references.length >= 5);
  for (const reference of references) assert.ok(manifest.files[reference], `missing ${reference}`);
  assert.ok(references.includes("favicon.svg"));
  // Absolute URLs appear only as page metadata and SVG namespaces, never as something to load.
  assert.throws(() => documentReferences('<script src="https://cdn.example.com/x.js"></script>'), /another origin/);
  assert.throws(() => documentReferences('<link rel="stylesheet" href="//cdn.example.com/x.css">'), /another origin/);
});

test("every inline script is covered by a hash for the served script policy", () => {
  assert.deepEqual(manifest.inline_scripts_sha256, inlineScriptHashes(html));
  const inline = [...html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g)].filter(match => !/\bsrc\s*=/.test(match[1]));
  assert.ok(inline.length >= 3);
  for (const [, , body] of inline) {
    assert.ok(manifest.inline_scripts_sha256.includes(createHash("sha256").update(body, "utf8").digest("base64")));
  }
  for (const value of manifest.inline_scripts_sha256) assert.match(value, /^[A-Za-z0-9+/]{43}=$/);
  assert.doesNotMatch(html, /\son[a-z]+\s*=\s*["']/, "inline event handlers would need unsafe-inline");
  assert.throws(() => inlineScriptHashes('<script src="/a.js">alert(1)</script>'), /both a source and inline code/);
});

test("the scripts are bound to no fixed backend", async () => {
  const scripts = await Promise.all(names.filter(name => name.endsWith(".js")).map(name => readFile(new URL(name, root), "utf8")));
  const code = scripts.join("\n");
  // Booleans keep a failure from printing the whole bundle.
  assert.equal(/[`"']same-origin[`"']/.test(code), true, "the same-origin marker is compiled in");
  assert.equal(/https?:\/\/[A-Za-z0-9.-]+(?::\d+)?\/v1\b/.test(code), false, "no absolute API URL is compiled in");
  assert.equal(code.includes("NEXT_PUBLIC_ORCHESTRATOR_URL"), false, "the build-time variable is resolved");
});

test("the licenses of what the browser receives ship with the bundle", async () => {
  const notices = await readFile(new URL("third-party-licenses.txt", root), "utf8");
  for (const name of ["react", "react-dom", "scheduler", "vinext"]) assert.match(notices, new RegExp(`\\n${name} \\d+\\.\\d+\\.\\d+ \\(MIT\\)\\n`));
  assert.match(notices, /\nGeist \(font\)\n=+\n\nCopyright \d{4} The Geist Project Authors/);
  assert.match(notices, /\nGeist Mono \(font\)\n/);
  assert.match(notices, /License declared by the font files: https:\/\/openfontlicense\.org/);
  assert.doesNotMatch(notices, /\((?:GPL|AGPL|LGPL|UNLICENSED)[^)]*\)\n=/);
});
