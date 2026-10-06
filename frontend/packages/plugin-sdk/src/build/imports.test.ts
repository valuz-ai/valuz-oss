import "./test-env";

import { describe, expect, it } from "vitest";

import { checkBundleImports, listBundleImports } from "./imports.mjs";

describe("checkBundleImports", () => {
  it("returns nothing for a self-contained bundle", () => {
    const code = `
      const shared = globalThis.__VALUZ_PLUGIN_SHARED__;
      const React = shared["react"];
      // import x from "commented";
      const s = 'import y from "in-a-string"';
      const re = /import z from "in-a-regexp"/;
      export default { id: "acme.x", apply() { return [React, s, re]; } };
    `;
    expect(checkBundleImports(code)).toEqual([]);
  });

  it("reports static, export-from, dynamic and require imports", () => {
    const code = `
      import React from "react";
      import { a } from "./chunk.js";
      import "side-effect";
      export { b } from "@valuz/plugin-sdk";
      export * from "https://cdn.example.com/x.js";
      const lazy = () => import("lodash");
      const fs = require("fs");
      const dynamic = (name) => import(name);
      export default { React, a, lazy, fs, dynamic };
    `;
    expect(checkBundleImports(code)).toEqual([
      "react",
      "./chunk.js",
      "side-effect",
      "@valuz/plugin-sdk",
      "https://cdn.example.com/x.js",
      "lodash",
      "fs",
    ]);
  });

  it("de-duplicates and keeps the kind in listBundleImports", () => {
    const code = `import a from "react"; import { b } from "react"; const c = import("x"); export default [a, b, c];`;
    expect(checkBundleImports(code)).toEqual(["react", "x"]);
    expect(listBundleImports(code)).toEqual([
      { specifier: "react", kind: "import-statement" },
      { specifier: "react", kind: "import-statement" },
      { specifier: "x", kind: "dynamic-import" },
    ]);
  });

  it("throws on a syntax error", () => {
    expect(() => checkBundleImports("export default {")).toThrow();
  });
});
