import assert from "node:assert/strict";
import test from "node:test";
import { brotliCompressSync } from "node:zlib";
import { bundledPackageRoots, describeFont, renderNotices, woff2NameRecords } from "../build/console-bundle-notices.ts";

test("only node_modules packages count, each once, by their innermost package root", () => {
  assert.deepEqual(bundledPackageRoots([
    "/workspace/app/IncidentConsole.tsx",
    "\0virtual:vite-rsc/client-references",
    "/workspace/node_modules/react/index.js",
    "/workspace/node_modules/react/cjs/react.production.js?commonjs-module",
    "/workspace/node_modules/@vitejs/plugin-rsc/dist/browser.js",
    "/workspace/node_modules/vinext/node_modules/scheduler/index.js",
    "C:\\workspace\\node_modules\\react-dom\\client.js",
    "/workspace/node_modules/@scope",
  ]), [
    "/workspace/node_modules/@vitejs/plugin-rsc",
    "/workspace/node_modules/react",
    "/workspace/node_modules/vinext/node_modules/scheduler",
    "C:/workspace/node_modules/react-dom",
  ]);
});

const base128 = (value) => {
  const bytes = [value & 0x7f];
  while ((value >>= 7) > 0) bytes.unshift((value & 0x7f) | 0x80);
  return bytes;
};

function woff2(tables) {
  // A minimal WOFF2 container: header, table directory, one Brotli stream.
  const directory = [], bodies = [];
  for (const { tag, index, body, transformedLength } of tables) {
    const entry = index === undefined ? [0x3f, ...Buffer.from(tag, "latin1")] : [index];
    entry.push(...base128(body.length));
    if (transformedLength !== undefined) entry.push(...base128(transformedLength));
    directory.push(...entry);
    bodies.push(transformedLength === undefined ? body : body.subarray(0, transformedLength));
  }
  const stream = brotliCompressSync(Buffer.concat(bodies));
  const header = Buffer.alloc(48);
  header.writeUInt32BE(0x774f4632, 0);
  header.writeUInt16BE(tables.length, 12);
  header.writeUInt32BE(stream.length, 20);
  return new Uint8Array(Buffer.concat([header, Buffer.from(directory), stream]));
}

function nameTable(records) {
  const strings = records.map(([, text]) => Buffer.from(text, "utf16le").swap16());
  const header = Buffer.alloc(6 + records.length * 12);
  header.writeUInt16BE(records.length, 2);
  header.writeUInt16BE(header.length, 4);
  let offset = 0;
  records.forEach(([id], index) => {
    const at = 6 + index * 12;
    header.writeUInt16BE(3, at); header.writeUInt16BE(1, at + 2); header.writeUInt16BE(0x409, at + 4);
    header.writeUInt16BE(id, at + 6); header.writeUInt16BE(strings[index].length, at + 8); header.writeUInt16BE(offset, at + 10);
    offset += strings[index].length;
  });
  return Buffer.concat([header, ...strings]);
}

test("a font's own name records are read past transformed and custom-tag tables", () => {
  const name = nameTable([[0, "Copyright 2024 Example Authors"], [1, "Example Sans"], [14, "https://openfontlicense.org"]]);
  const font = woff2([
    { index: 10, body: Buffer.alloc(300, 1), transformedLength: 12 },  // glyf: transformed by default
    { tag: "zzzz", body: Buffer.alloc(9, 2) },                         // a tag outside the known list
    { index: 1, body: Buffer.alloc(54, 3) },                           // head: stored as is
    { index: 5, body: name },
  ]);
  assert.deepEqual([...woff2NameRecords(font)], [[0, "Copyright 2024 Example Authors"], [1, "Example Sans"], [14, "https://openfontlicense.org"]]);
  assert.deepEqual(describeFont(font), { family: "Example Sans", copyright: "Copyright 2024 Example Authors",
    license: null, licenseUrl: "https://openfontlicense.org" });
  assert.throws(() => describeFont(woff2([{ index: 5, body: nameTable([[1, "Unlicensed Sans"]]) }])), /does not declare/);
  assert.throws(() => woff2NameRecords(new Uint8Array(64)), /Not a WOFF2 font/);
  assert.throws(() => woff2NameRecords(woff2([{ index: 1, body: Buffer.alloc(54) }])), /no name table/);
});

test("notices name each package and font with the license it declares", () => {
  const text = renderNotices(
    [{ name: "react", version: "19.2.9", license: "MIT", text: "MIT License\n\nCopyright (c) Meta Platforms, Inc." },
     { name: "no-file", version: "1.0.0", license: "ISC", text: null }],
    [{ family: "Example Sans", copyright: "Copyright 2024 Example Authors", license: null, licenseUrl: "https://openfontlicense.org" }]);
  assert.match(text, /^Third-party software and fonts in the OpsPilot console bundle\n/);
  assert.match(text, /\nreact 19\.2\.9 \(MIT\)\n=+\n\nMIT License\n\nCopyright \(c\) Meta Platforms, Inc\.\n/);
  assert.match(text, /\nno-file 1\.0\.0 \(ISC\)\n=+\n\nThe package declares the ISC license and ships no license file\.\n/);
  assert.match(text, /\nExample Sans \(font\)\n=+\n\nCopyright 2024 Example Authors\n\nLicense declared by the font files: https:\/\/openfontlicense\.org\n$/);
});
