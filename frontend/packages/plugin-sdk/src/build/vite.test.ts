import "./test-env";

import { readFileSync } from "node:fs";
import path from "node:path";
import type { Plugin } from "vite";
import { afterAll, describe, expect, it } from "vitest";

import { checkBundleImports } from "./imports.mjs";
import {
  cleanupTempDirs,
  expectFixtureBundleRuns,
  fixturePlugin,
  tempDir,
} from "./test-fixtures";
import { valuzPlugin } from "./vite.mjs";

afterAll(cleanupTempDirs);

describe("valuzPlugin (Vite)", () => {
  it("is assignable to Vite's plugin type", () => {
    const plugins: Plugin[] = valuzPlugin();
    expect(plugins.map((p) => p.name)).toEqual([
      "valuz-plugin:shared",
      "valuz-plugin:styles",
    ]);
    expect(plugins.every((p) => p.apply === "build")).toBe(true);
  });

  it("builds a self-contained ES module with scoped CSS", async () => {
    const dir = fixturePlugin(tempDir());
    const { build } = await import("vite");
    await build({
      root: dir,
      configFile: false,
      logLevel: "silent",
      plugins: [valuzPlugin()],
    });

    const js = readFileSync(path.join(dir, "frontend/index.js"), "utf8");
    expect(checkBundleImports(js)).toEqual([]);
    expect(js).toContain("__VALUZ_PLUGIN_SHARED__");
    expect(js).not.toContain("React.createElement"); // automatic JSX runtime
    expectFixtureBundleRuns(js);

    const css = readFileSync(path.join(dir, "frontend/index.css"), "utf8");
    expect(css).toContain('[data-valuz-ext="acme.fixture"] .card');
    expect(css).not.toMatch(/(^|[^\w-]):root/);
  }, 60_000);
});
