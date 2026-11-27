#!/usr/bin/env node
// Export the built console as static files the orchestrator serves itself
// (CONSOLE_STATIC_DIR), so an install has one URL for the API and its console.
//
//   npm run build:console      # same-origin build, then this export -> dist/console
//
// The page is rendered once by the built Worker under local workerd, the way
// tests/rendered-html.test.mjs renders it, so the exported document is what the
// hosted console would send. The export fails rather than ship a page that is
// bound to another backend, references a missing file or contacts another host.
import { createHash } from "node:crypto";
import { mkdir, readFile, readdir, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { Miniflare, convertV4MiniflareOptions } from "miniflare";

export const MANIFEST_NAME = "console-manifest.json";
// Extension -> media type. A file with any other extension stops the export.
const MEDIA_TYPES = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".svg": "image/svg+xml",
  ".txt": "text/plain; charset=utf-8",
  ".woff2": "font/woff2",
};
// Public files the page links to. Hashed build output under _next/static is always included.
const PUBLIC_FILES = ["favicon.svg", "third-party-licenses.txt"];

async function renderDocument(serverRoot) {
  const modulePaths = (await readdir(serverRoot, { recursive: true })).filter((name) => name.endsWith(".js"));
  modulePaths.sort((a, b) => a === "index.js" ? -1 : b === "index.js" ? 1 : a.localeCompare(b));
  let externalCalls = 0;
  const runtime = new Miniflare({
    ...convertV4MiniflareOptions({
      host: "127.0.0.1", port: 0,
      modules: modulePaths.map((name) => ({ type: "ESModule", path: path.join(serverRoot, name) })),
      modulesRoot: serverRoot,
      compatibilityDate: "2026-10-06",
      compatibilityFlags: ["nodejs_compat"],
      serviceBindings: { ASSETS: async () => { externalCalls += 1; return new Response("Not found", { status: 404 }); } },
      outboundService: async () => { externalCalls += 1; return new Response("Forbidden during export", { status: 502 }); },
    }),
    telemetry: { enabled: false },
  });
  try {
    // The forwarded host only fixes the absolute URLs in the page metadata.
    const response = await runtime.dispatchFetch("http://localhost/", {
      headers: { accept: "text/html", "x-forwarded-host": "localhost", "x-forwarded-proto": "http" },
    });
    const html = await response.text();
    if (response.status !== 200 || !/^text\/html\b/i.test(response.headers.get("content-type") ?? "")) {
      throw new Error(`Console render failed with status ${response.status}.`);
    }
    if (externalCalls) throw new Error("Console render tried to load an asset or contact another service.");
    return html;
  } finally {
    await runtime.dispose();
  }
}

/** SHA-256 (base64) of each inline script, for the served Content-Security-Policy. */
export function inlineScriptHashes(html) {
  const hashes = [];
  for (const match of html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g)) {
    if (/\bsrc\s*=/.test(match[1])) {
      if (match[2].trim()) throw new Error("A script element has both a source and inline code.");
      continue;
    }
    hashes.push(createHash("sha256").update(match[2], "utf8").digest("base64"));
  }
  return [...new Set(hashes)].sort();
}

/** Same-origin paths the document loads; any other host stops the export. */
export function documentReferences(html) {
  const references = new Set();
  for (const tag of html.matchAll(/<(?:script|link|img|source|iframe|audio|video)\b[^>]*>/g)) {
    for (const attribute of tag[0].matchAll(/\b(?:src|href)="([^"]*)"/g)) {
      const value = attribute[1];
      if (!value.startsWith("/") || value.startsWith("//")) throw new Error(`Console document loads from another origin: ${value}`);
      references.add(value.slice(1).split(/[?#]/)[0]);
    }
  }
  return [...references].sort();
}

export async function exportConsole({ root, outDir }) {
  const clientRoot = path.join(root, "dist", "client");
  const html = await renderDocument(path.join(root, "dist", "server") + path.sep);
  if (!html.includes('data-backend="same-origin"')) {
    throw new Error("This build is not same-origin. Build with NEXT_PUBLIC_ORCHESTRATOR_URL=same-origin (npm run build:console).");
  }
  const sources = new Map([["index.html", Buffer.from(html, "utf8")]]);
  for (const name of (await readdir(path.join(clientRoot, "_next", "static"), { recursive: true, withFileTypes: true }))) {
    if (!name.isFile()) continue;
    const relative = path.relative(clientRoot, path.join(name.parentPath, name.name)).split(path.sep).join("/");
    sources.set(relative, await readFile(path.join(clientRoot, relative)));
  }
  for (const name of PUBLIC_FILES) sources.set(name, await readFile(path.join(clientRoot, name)));
  for (const reference of documentReferences(html)) {
    if (!sources.has(reference)) throw new Error(`Console document references a file that is not exported: /${reference}`);
  }

  await rm(outDir, { recursive: true, force: true });
  const files = {};
  for (const name of [...sources.keys()].sort()) {
    const type = MEDIA_TYPES[path.extname(name)];
    if (!type) throw new Error(`No media type is declared for exported file: ${name}`);
    const body = sources.get(name);
    await mkdir(path.dirname(path.join(outDir, name)), { recursive: true });
    await writeFile(path.join(outDir, name), body);
    files[name] = { bytes: body.length, sha256: createHash("sha256").update(body).digest("hex"), type };
  }
  const manifest = { schema: 1, backend: "same-origin", inline_scripts_sha256: inlineScriptHashes(html), files };
  await writeFile(path.join(outDir, MANIFEST_NAME), `${JSON.stringify(manifest, null, 2)}\n`);
  return manifest;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
  const outDir = path.resolve(process.argv[2] ?? path.join(root, "dist", "console"));
  const manifest = await exportConsole({ root, outDir });
  const bytes = Object.values(manifest.files).reduce((total, file) => total + file.bytes, 0);
  console.log(`Exported the console to ${path.relative(process.cwd(), outDir) || "."}: ${Object.keys(manifest.files).length} files, ${bytes} bytes, ${manifest.inline_scripts_sha256.length} inline script hashes.`);
}
