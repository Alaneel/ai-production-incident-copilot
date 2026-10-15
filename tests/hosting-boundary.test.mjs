import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { sites } from "../build/sites-vite-plugin.ts";

for (const publicBuild of [false, true]) {
  test(`hosting packaging preserves local override; public build=${publicBuild}`, async (t) => {
    const root = await mkdtemp(path.join(os.tmpdir(), "opspilot-hosting-boundary-"));
    t.after(() => rm(root, { recursive: true, force: true }));
    await mkdir(path.join(root, ".openai"));
    const neutral = { d1: "SITE_DB", r2: "SITE_BUCKET" };
    const local = { ...neutral, project_id: "synthetic-private-target-for-test-only" };
    await writeFile(path.join(root, ".openai/hosting.json"), JSON.stringify(neutral));
    await writeFile(path.join(root, ".openai/hosting.local.json"), JSON.stringify(local));
    const prior = process.env.OPSPILOT_PUBLIC_BUILD;
    process.env.OPSPILOT_PUBLIC_BUILD = publicBuild ? "1" : "0";
    try {
      const plugin = sites();
      plugin.configResolved({ root });
      await plugin.closeBundle();
      const packaged = JSON.parse(await readFile(path.join(root, "dist/.openai/hosting.json"), "utf8"));
      assert.deepEqual(packaged, publicBuild ? neutral : local);
      assert.equal("project_id" in packaged, !publicBuild);
    } finally {
      if (prior === undefined) delete process.env.OPSPILOT_PUBLIC_BUILD;
      else process.env.OPSPILOT_PUBLIC_BUILD = prior;
    }
  });
}
