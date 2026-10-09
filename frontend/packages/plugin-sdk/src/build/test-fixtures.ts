// Fixtures for the build tests: temp plugin directories on disk, and a check
// that a built fixture bundle runs against the host's shared modules.
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import * as React from "react";
import * as ReactDOM from "react-dom";
import * as ReactDOMClient from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import * as jsxRuntime from "react/jsx-runtime";
import { expect } from "vitest";

import { loadEsbuild } from "./esbuild.mjs";

const created: string[] = [];

/** A fresh temp directory, removed by ``cleanupTempDirs``. */
export function tempDir(prefix = "vzp-test-"): string {
  const dir = mkdtempSync(path.join(os.tmpdir(), prefix));
  created.push(dir);
  return dir;
}

export function cleanupTempDirs(): void {
  for (const dir of created.splice(0))
    rmSync(dir, { recursive: true, force: true });
}

/** Write ``{ "rel/path": content }`` under ``root`` (objects become JSON). */
export function writeFiles(root: string, files: Record<string, unknown>): void {
  for (const [rel, content] of Object.entries(files)) {
    const file = path.join(root, rel);
    mkdirSync(path.dirname(file), { recursive: true });
    writeFileSync(
      file,
      typeof content === "string"
        ? content
        : `${JSON.stringify(content, null, 2)}\n`,
    );
  }
}

export function manifest(
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    manifestVersion: 1,
    id: "acme.fixture",
    version: "1.2.3",
    name: { "zh-CN": "夹具", "en-US": "Fixture" },
    publisher: { name: "Acme" },
    engines: { "valuz-plugin-api": "^1.0.0" },
    frontend: { entry: "frontend/index.js" },
    permissions: ["projects:read"],
    ...overrides,
  };
}

/** A plugin source using every shared module, CSS and a bundled helper. */
export const FIXTURE_SOURCE = `
import React, { useState } from "react";
import * as ReactNs from "react";
import { createRoot } from "react-dom/client";
import { flushSync } from "react-dom";
import { definePlugin, useValuz } from "@valuz/plugin-sdk";
import { Button } from "@valuz/plugin-sdk/ui";
import { shout } from "./helper";
import "./style.css";

export function Counter() {
  const [n] = useState(1);
  const valuz = useValuz();
  return <Button data-n={n}>{shout(String(Boolean(valuz)))}</Button>;
}

export default definePlugin({
  id: "acme.fixture",
  apply(ctx) {
    ctx.registry.slot("conversation.title.actions", { id: "counter", component: Counter });
    return { React, version: ReactNs.version, createRoot, flushSync, element: <Counter /> };
  },
});
`;

export const FIXTURE_CSS = `
:root { --fixture-gap: 4px; }
.card, .card > .title { gap: var(--fixture-gap); }
@media (min-width: 600px) { .card { padding: 8px; } }
@keyframes pulse { from { opacity: 0; } to { opacity: 1; } }
`;

/** A complete plugin source directory (not built). */
export function fixturePlugin(
  root: string,
  extra: Record<string, unknown> = {},
): string {
  writeFiles(root, {
    "valuz-plugin.json": manifest(),
    "src/index.tsx": FIXTURE_SOURCE,
    "src/helper.ts":
      "export const shout = (text: string): string => text.toUpperCase() + '!';\n",
    "src/style.css": FIXTURE_CSS,
    "locales/en-US.json": { hello: "Hello" },
    "locales/zh-CN.json": { hello: "你好" },
    "README.md": "# Fixture\n",
    ...extra,
  });
  return root;
}

/**
 * Evaluate a built plugin bundle the way the host imports it. Vitest cannot
 * import files outside the workspace, so the (import-free) module is converted
 * to CommonJS by esbuild — only its trailing ``export`` statement changes.
 */
export function evaluateBundle(code: string): { default: unknown } {
  const cjs = loadEsbuild().transformSync(code, { format: "cjs", loader: "js" }).code;
  const module = { exports: {} as { default: unknown } };
  new Function("module", "exports", cjs)(module, module.exports);
  return module.exports;
}

/**
 * Run the built ``FIXTURE_SOURCE`` bundle with a shared module table holding
 * the real React / ReactDOM and stub SDK / UI modules, and check that default,
 * namespace and named imports all reach the host's modules.
 */
export function expectFixtureBundleRuns(code: string): void {
  const registered: Array<{ name: string; id: string }> = [];
  const sdk = {
    definePlugin: (definition: unknown) => definition,
    useValuz: () => ({ projects: {} }),
  };
  const ui = {
    Button: (props: Record<string, unknown>) => React.createElement("button", props),
  };
  const shared = globalThis as unknown as Record<string, unknown>;
  shared.__VALUZ_PLUGIN_SHARED__ = {
    react: React,
    "react/jsx-runtime": jsxRuntime,
    "react-dom": ReactDOM,
    "react-dom/client": ReactDOMClient,
    "@valuz/plugin-sdk": sdk,
    "@valuz/plugin-sdk/ui": ui,
  };
  try {
    const plugin = evaluateBundle(code).default as {
      id: string;
      apply(ctx: unknown): {
        React: { useState: unknown };
        version: string;
        createRoot: unknown;
        flushSync: unknown;
        element: React.ReactElement;
      };
    };
    expect(plugin.id).toBe("acme.fixture");
    const result = plugin.apply({
      registry: {
        slot: (name: string, registration: { id: string }) =>
          registered.push({ name, id: registration.id }),
      },
    });
    expect(registered).toEqual([{ name: "conversation.title.actions", id: "counter" }]);
    expect(result.React.useState).toBe(React.useState);
    expect(result.version).toBe(React.version);
    expect(result.createRoot).toBe(ReactDOMClient.createRoot);
    expect(result.flushSync).toBe(ReactDOM.flushSync);
    // useState + jsx from the host's React, Button from the host's UI, useValuz from the host's SDK.
    expect(renderToStaticMarkup(result.element)).toBe('<button data-n="1">TRUE!</button>');
  } finally {
    delete shared.__VALUZ_PLUGIN_SHARED__;
  }
}
