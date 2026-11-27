import { readdir, readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { brotliDecompressSync } from "node:zlib";
import type { Plugin } from "vite";

export const NOTICES_FILE = "third-party-licenses.txt";

export type BundledPackage = { name: string; version: string; license: string; text: string | null };
export type BundledFont = { family: string; copyright: string; license: string | null; licenseUrl: string };

/** node_modules package roots (the innermost one) that contributed a module. */
export function bundledPackageRoots(moduleIds: readonly string[]): string[] {
  const roots = new Set<string>();
  for (const id of moduleIds) {
    const path = id.replaceAll("\\", "/").replace(/^\0+/, "").split("?")[0];
    const marker = path.lastIndexOf("/node_modules/");
    if (marker < 0) continue;
    const [first, second] = path.slice(marker + "/node_modules/".length).split("/");
    if (!first || (first.startsWith("@") && !second)) continue;
    roots.add(`${path.slice(0, marker)}/node_modules/${first.startsWith("@") ? `${first}/${second}` : first}`);
  }
  return [...roots].sort();
}

async function describePackage(root: string): Promise<BundledPackage> {
  const manifest = JSON.parse(await readFile(join(root, "package.json"), "utf8")) as Record<string, unknown>;
  if (typeof manifest.name !== "string" || typeof manifest.version !== "string" || typeof manifest.license !== "string") {
    throw new Error(`Bundled package has no name, version or SPDX license: ${String(manifest.name)}`);
  }
  const licenseFile = (await readdir(root)).filter((name) => /^licen[cs]e(\.(md|txt))?$/i.test(name)).sort()[0];
  return { name: manifest.name, version: manifest.version, license: manifest.license,
    text: licenseFile ? (await readFile(join(root, licenseFile), "utf8")).trim() : null };
}

function readBase128(data: Uint8Array, start: number): [number, number] {
  let value = 0, offset = start;
  for (let index = 0; index < 5; index += 1) {
    const byte = data[offset++];
    value = value * 128 + (byte & 0x7f);
    if ((byte & 0x80) === 0) return [value, offset];
  }
  throw new Error("Invalid WOFF2 table directory.");
}

const knownTags = ["cmap", "head", "hhea", "hmtx", "maxp", "name", "OS/2", "post", "cvt ", "fpgm", "glyf", "loca", "prep",
  "CFF ", "VORG", "EBDT", "EBLC", "gasp", "hdmx", "kern", "LTSH", "PCLT", "VDMX", "vhea", "vmtx", "BASE", "GDEF", "GPOS",
  "GSUB", "EBSC", "JSTF", "MATH", "CBDT", "CBLC", "COLR", "CPAL", "SVG ", "sbix", "acnt", "avar", "bdat", "bloc", "bsln",
  "cvar", "fdsc", "feat", "fmtx", "fvar", "gvar", "hsty", "just", "lcar", "mort", "morx", "opbd", "prop", "trak", "Zapf",
  "Silf", "Glat", "Gloc", "Feat", "Sill"];

/** The `name` records a WOFF2 font carries about itself (name ID -> text). */
export function woff2NameRecords(font: Uint8Array): Map<number, string> {
  const view = new DataView(font.buffer, font.byteOffset, font.byteLength);
  if (view.getUint32(0) !== 0x774f4632) throw new Error("Not a WOFF2 font.");
  const tableCount = view.getUint16(12), compressedLength = view.getUint32(20);
  let offset = 48, streamOffset = 0, name: [number, number] | null = null;
  for (let index = 0; index < tableCount; index += 1) {
    const flags = font[offset++];
    let tag = knownTags[flags & 0x3f];
    if ((flags & 0x3f) === 0x3f) { tag = new TextDecoder("latin1").decode(font.subarray(offset, offset + 4)); offset += 4; }
    let originalLength: number, storedLength: number;
    [originalLength, offset] = readBase128(font, offset);
    storedLength = originalLength;
    // glyf and loca are transformed by default (version 0); every other table only when its version bits are set.
    const transformed = tag === "glyf" || tag === "loca" ? (flags >> 6) === 0 : (flags >> 6) !== 0;
    if (transformed) [storedLength, offset] = readBase128(font, offset);
    if (tag === "name") name = [streamOffset, originalLength];
    streamOffset += storedLength;
  }
  if (!name) throw new Error("WOFF2 font has no name table.");
  const stream = brotliDecompressSync(font.subarray(offset, offset + compressedLength));
  const table = new DataView(stream.buffer, stream.byteOffset + name[0], name[1]);
  const records = new Map<number, string>(), strings = table.getUint16(4);
  for (let index = 0; index < table.getUint16(2); index += 1) {
    const record = 6 + index * 12;
    const platform = table.getUint16(record), nameId = table.getUint16(record + 6);
    const bytes = new Uint8Array(table.buffer, table.byteOffset + strings + table.getUint16(record + 10), table.getUint16(record + 8));
    // Windows/Unicode records are UTF-16BE; keep the first one found for each ID.
    if ((platform === 3 || platform === 0) && !records.has(nameId)) records.set(nameId, new TextDecoder("utf-16be").decode(bytes));
  }
  return records;
}

export function describeFont(font: Uint8Array): BundledFont {
  const records = woff2NameRecords(font);
  const [family, copyright, licenseUrl] = [records.get(16) ?? records.get(1), records.get(0), records.get(14)];
  if (!family || !copyright || !licenseUrl) throw new Error("Bundled font does not declare its family, copyright and license URL.");
  return { family, copyright, license: records.get(13) ?? null, licenseUrl };
}

export function renderNotices(packages: readonly BundledPackage[], fonts: readonly BundledFont[]): string {
  const lines = ["Third-party software and fonts in the OpsPilot console bundle",
    "", "This file is generated at build time from the modules and font files that",
    "were actually bundled. OpsPilot's own source is MIT licensed.", ""];
  for (const item of packages) {
    lines.push("=".repeat(72), `${item.name} ${item.version} (${item.license})`, "=".repeat(72), "",
      item.text ?? `The package declares the ${item.license} license and ships no license file.`, "");
  }
  for (const font of fonts) {
    lines.push("=".repeat(72), `${font.family} (font)`, "=".repeat(72), "", font.copyright, "",
      ...(font.license ? [font.license] : []), `License declared by the font files: ${font.licenseUrl}`, "");
  }
  return `${lines.join("\n").trimEnd()}\n`;
}

/** Writes the licenses of everything bundled for the browser next to the client assets. */
export function consoleBundleNotices(): Plugin {
  return {
    name: "opspilot-console-bundle-notices",
    apply: "build",
    // After vinext's own post-order writeBundle, which copies the self-hosted fonts.
    writeBundle: {
      sequential: true,
      order: "post",
      async handler(outputOptions, bundle) {
        if (this.environment.name !== "client" || !outputOptions.dir) return;
        const moduleIds = Object.values(bundle).flatMap((output) => output.type === "chunk" ? Object.keys(output.modules) : []);
        const packages = new Map<string, BundledPackage>();
        for (const root of bundledPackageRoots(moduleIds)) {
          const item = await describePackage(root);
          packages.set(`${item.name}@${item.version}`, item);
        }
        const fonts = new Map<string, BundledFont>();
        for (const name of (await readdir(outputOptions.dir, { recursive: true })).sort()) {
          if (/\.(woff|ttf|otf|eot)$/i.test(name)) throw new Error("Bundled fonts must be WOFF2 so their license can be read.");
          if (!name.endsWith(".woff2")) continue;
          const font = describeFont(await readFile(join(outputOptions.dir, name)));
          fonts.set(font.family, font);
        }
        await writeFile(join(outputOptions.dir, NOTICES_FILE),
          renderNotices([...packages.values()], [...fonts.values()].sort((a, b) => a.family.localeCompare(b.family))));
        this.info(`Console bundle notices: ${packages.size} packages, ${fonts.size} font families`);
      },
    },
  };
}
