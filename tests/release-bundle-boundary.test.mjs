import assert from "node:assert/strict";
import test from "node:test";
import { assertRuntimeModuleBoundary, buildOnlyGlobPackages } from "../build/release-bundle-boundary.ts";

test("release guard accepts actual runtime package paths and application routes", () => {
  assertRuntimeModuleBoundary(["/workspace/app/IncidentConsole.tsx", "/workspace/node_modules/react/index.js",
    "/workspace/node_modules/vinext/dist/server/app-router.js"]);
});

test("release guard refuses every unfixed glob/build path, including nested and Windows packages", () => {
  for (const name of buildOnlyGlobPackages) {
    assert.throws(() => assertRuntimeModuleBoundary([name]), /Build-only glob dependency/);
    assert.throws(() => assertRuntimeModuleBoundary([`${name}/index.js`]), /Build-only glob dependency/);
    for (const root of ["/workspace/node_modules/", "/workspace/node_modules/vinext/node_modules/",
      "C:\\workspace\\node_modules\\"]) {
      assert.throws(() => assertRuntimeModuleBoundary([`${root}${name}/index.js`]), /Build-only glob dependency/);
    }
  }
});
