import type { Plugin } from "vite";

export const buildOnlyGlobPackages = [
  "braces", "micromatch", "fast-glob", "vite-plugin-dynamic-import", "vite-plugin-commonjs",
] as const;

export function assertRuntimeModuleBoundary(moduleIds: readonly string[]): void {
  for (const id of moduleIds) {
    const normalized = id.replaceAll("\\", "/");
    for (const dependency of buildOnlyGlobPackages) {
      if (normalized === dependency || normalized.startsWith(`${dependency}/`)
          || normalized.includes(`/node_modules/${dependency}/`)) {
        // Do not include host module paths or source content in the error.
        throw new Error(`Build-only glob dependency entered a runtime bundle: ${dependency}`);
      }
    }
  }
}

export function releaseBundleBoundary(): Plugin {
  return {
    name: "opspilot-release-bundle-boundary",
    apply: "build",
    generateBundle(_options, bundle) {
      const moduleIds = Object.values(bundle).flatMap((output) =>
        output.type === "chunk"
          ? [...Object.keys(output.modules), ...output.imports, ...output.dynamicImports] : [],
      );
      assertRuntimeModuleBoundary(moduleIds);
      this.info(`Release runtime module boundary: ${moduleIds.length} module/import records, 0 build-only glob dependencies`);
    },
  };
}
