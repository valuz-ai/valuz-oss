import "./test-env";

import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import type * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import * as jsxRuntime from "react/jsx-runtime";
import { afterAll, describe, expect, it } from "vitest";

import { buildPlugin } from "./build-plugin.mjs";
import { esbuildPreset } from "./esbuild-preset.mjs";
import { checkBundleImports } from "./imports.mjs";
import { ManifestError } from "./manifest.mjs";
import { SHARED_MODULES, SHARED_MODULE_IDS } from "./shared.mjs";
import { SHARED_MODULE_IDS as RUNTIME_SHARED_MODULE_IDS } from "../index";
import {
  cleanupTempDirs,
  evaluateBundle,
  expectFixtureBundleRuns,
  fixturePlugin,
  tempDir,
  writeFiles,
} from "./test-fixtures";

afterAll(cleanupTempDirs);

const SCOPE = '[data-valuz-app-plugin="acme.fixture"]';


describe("shared modules", () => {
  it("match the runtime's table", () => {
    expect([...SHARED_MODULE_IDS]).toEqual([...RUNTIME_SHARED_MODULE_IDS]);
    expect(SHARED_MODULES["react/jsx-runtime"]).toBe(
      'globalThis.__VALUZ_PLUGIN_SHARED__["react/jsx-runtime"]',
    );
  });
});

describe("esbuildPreset", () => {
  it("is a single-file browser ESM build", () => {
    const options = esbuildPreset({
      pluginId: "acme.x",
      entry: "src/index.tsx",
      outfile: "frontend/index.js",
    });
    expect(options).toMatchObject({
      bundle: true,
      splitting: false,
      format: "esm",
      platform: "browser",
      target: "es2022",
      jsx: "automatic",
      outfile: "frontend/index.js",
    });
    expect(options.plugins?.map((p) => p.name)).toEqual(["valuz-plugin"]);
  });
});

describe("buildPlugin", () => {
  it("bundles a plugin with shared modules from the host and scoped CSS", async () => {
    const dir = fixturePlugin(tempDir());
    const report = await buildPlugin(dir);
    expect(report.errors).toEqual([]);
    expect(report.ok).toBe(true);
    expect(report.outputs.map((o) => o.file).sort()).toEqual([
      "frontend/index.css",
      "frontend/index.js",
    ]);

    const js = readFileSync(path.join(dir, "frontend/index.js"), "utf8");
    expect(checkBundleImports(js)).toEqual([]);
    expect(js).toContain("globalThis.__VALUZ_PLUGIN_SHARED__");
    expect(js).toContain("toUpperCase"); // the local helper is bundled

    const css = readFileSync(path.join(dir, "frontend/index.css"), "utf8");
    expect(css).toContain(`${SCOPE} {`); // :root
    expect(css).toContain(`${SCOPE} .card,`);
    expect(css).toContain(`${SCOPE} .card > .title`);
    expect(css).toMatch(
      /@media \(min-width: 600px\) {\s+\[data-valuz-app-plugin="acme\.fixture"\] \.card/,
    );
    expect(css).toMatch(/@keyframes pulse {\s+from {/);

    // The produced CSS is listed in the manifest.
    expect(report.manifestUpdated).toBe(true);
    const manifest = JSON.parse(
      readFileSync(path.join(dir, "valuz-plugin.json"), "utf8"),
    );
    expect(manifest.frontend.styles).toEqual(["frontend/index.css"]);
    const again = await buildPlugin(dir);
    expect(again.manifestUpdated).toBe(false);
  });

  it("produces a bundle whose imports resolve from __VALUZ_PLUGIN_SHARED__", async () => {
    const dir = fixturePlugin(tempDir());
    expect((await buildPlugin(dir)).ok).toBe(true);

    expectFixtureBundleRuns(readFileSync(path.join(dir, "frontend/index.js"), "utf8"));
  });

  it("maps react/jsx-dev-runtime onto the shared jsx runtime", async () => {
    const dir = fixturePlugin(tempDir(), {
      "src/index.tsx": `import { jsxDEV, Fragment } from "react/jsx-dev-runtime";\nexport default { id: "acme.fixture", apply() { return jsxDEV(Fragment, { children: jsxDEV("b", { children: "dev" }, undefined, false) }, undefined, false); } };\n`,
    });
    expect((await buildPlugin(dir)).ok).toBe(true);
    const js = readFileSync(path.join(dir, "frontend/index.js"), "utf8");
    expect(checkBundleImports(js)).toEqual([]);
    const shared = globalThis as unknown as Record<string, unknown>;
    shared.__VALUZ_PLUGIN_SHARED__ = { "react/jsx-runtime": jsxRuntime };
    try {
      const plugin = evaluateBundle(js).default as { apply(): React.ReactElement };
      expect(renderToStaticMarkup(plugin.apply())).toBe("<b>dev</b>");
    } finally {
      delete shared.__VALUZ_PLUGIN_SHARED__;
    }
  });

  it("fails on internal @valuz packages and unresolvable imports", async () => {
    const dir = fixturePlugin(tempDir(), {
      "src/index.tsx": `import { useTranslation } from "@valuz/core";\nexport default { id: "acme.fixture", apply() { return useTranslation; } };\n`,
    });
    const report = await buildPlugin(dir);
    expect(report.ok).toBe(false);
    expect(report.errors.join("\n")).toMatch(
      /"@valuz\/core" is an internal Valuz package/,
    );

    writeFiles(dir, {
      "src/index.tsx": `import "@valuz/plugin-sdk/testing";\nimport pad from "left-pad-not-installed";\nexport default pad;\n`,
    });
    const second = await buildPlugin(dir);
    expect(second.ok).toBe(false);
    const text = second.errors.join("\n");
    expect(text).toMatch(
      /"@valuz\/plugin-sdk\/testing" is not available inside a plugin/,
    );
    expect(text).toMatch(/left-pad-not-installed/);
  });

  it("honours package.json valuzPlugin.source and a custom frontend.entry", async () => {
    const dir = fixturePlugin(tempDir(), {
      "package.json": { name: "x", valuzPlugin: { source: "lib/main.ts" } },
      "lib/main.ts": `import { definePlugin } from "@valuz/plugin-sdk";\nexport default definePlugin({ id: "acme.fixture", apply() {} });\n`,
    });
    const manifest = JSON.parse(
      readFileSync(path.join(dir, "valuz-plugin.json"), "utf8"),
    );
    manifest.frontend.entry = "frontend/plugin.mjs";
    writeFileSync(
      path.join(dir, "valuz-plugin.json"),
      JSON.stringify(manifest),
    );
    const report = await buildPlugin(dir, { minify: true });
    expect(report.ok).toBe(true);
    expect(report.source).toBe(path.join("lib", "main.ts"));
    expect(report.outputs.map((o) => o.file)).toEqual(["frontend/plugin.mjs"]);
    expect(report.manifestUpdated).toBe(false);
  });

  it("reports a missing source entry", async () => {
    const dir = tempDir();
    writeFiles(dir, {
      "valuz-plugin.json": {
        id: "acme.fixture",
        frontend: { entry: "frontend/index.js" },
      },
    });
    await expect(buildPlugin(dir)).rejects.toThrow(ManifestError);
    await expect(buildPlugin(dir)).rejects.toThrow(/create src\/index\.tsx/);
  });

  it("rebuilds in watch mode", async () => {
    const dir = fixturePlugin(tempDir());
    const reports: Array<{ ok: boolean }> = [];
    let notify: (() => void) | null = null;
    const handle = await buildPlugin(dir, {
      watch: true,
      onBuild(report) {
        reports.push(report);
        notify?.();
      },
    });
    try {
      expect(handle.report.ok).toBe(true);
      expect(reports).toHaveLength(1);
      const rebuilt = new Promise<void>((resolve) => {
        notify = resolve;
      });
      writeFiles(dir, {
        "src/helper.ts":
          "export const shout = (text: string): string => `rebuilt:${text}`;\n",
      });
      await rebuilt;
      expect(reports.at(-1)?.ok).toBe(true);
      expect(
        readFileSync(path.join(dir, "frontend/index.js"), "utf8"),
      ).toContain("rebuilt:");
    } finally {
      await handle.dispose();
    }
  }, 20_000);
});
