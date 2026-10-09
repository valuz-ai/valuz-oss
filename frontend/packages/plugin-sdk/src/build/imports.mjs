// Bundle import check: a plugin's frontend entry is imported by the host from
// a Blob URL, where no module specifier can resolve (no import map, no base
// URL), and everything it needs from the host comes from the shared-module
// table. So a valid bundle has no import / export-from / import("…") / literal
// require("…") left at all.
import { loadEsbuild } from "./esbuild.mjs";

const IMPORT_KINDS = new Set(["import-statement", "dynamic-import", "require-call"]);

/**
 * The import records of ``code`` (an ES module): ``{ specifier, kind }`` for
 * every static import, ``export … from``, ``import("literal")`` and
 * ``require("literal")``. Uses esbuild's parser, so strings, comments and
 * regular expressions never produce false positives. Throws on a syntax error.
 */
export function listBundleImports(code) {
  const esbuild = loadEsbuild();
  const result = esbuild.buildSync({
    stdin: { contents: code, loader: "js", sourcefile: "bundle.js" },
    bundle: true,
    write: false,
    metafile: true,
    external: ["*"],
    format: "esm",
    platform: "neutral",
    logLevel: "silent",
  });
  const input = result.metafile.inputs["bundle.js"];
  const records = [];
  for (const item of input?.imports ?? []) {
    if (!IMPORT_KINDS.has(item.kind)) continue;
    records.push({ specifier: item.original ?? item.path, kind: item.kind });
  }
  return records;
}

/**
 * Disallowed import specifiers of a built plugin bundle (unique, in order of
 * appearance). Every import is disallowed: bare specifiers (``react``,
 * ``lodash``) cannot resolve in the host, the shared modules must go through
 * the build preset, relative / absolute paths cannot load from a Blob URL, and
 * URL imports would reach the network. An empty list means the bundle is OK.
 */
export function checkBundleImports(code) {
  const seen = new Set();
  const out = [];
  for (const { specifier } of listBundleImports(code)) {
    if (seen.has(specifier)) continue;
    seen.add(specifier);
    out.push(specifier);
  }
  return out;
}

/** Whether a built bundle has a default export (``export default definePlugin(…)``). */
export function bundleExports(code) {
  const esbuild = loadEsbuild();
  const result = esbuild.buildSync({
    stdin: { contents: code, loader: "js", sourcefile: "bundle.js" },
    bundle: false,
    write: false,
    metafile: true,
    format: "esm",
    logLevel: "silent",
  });
  const output = Object.values(result.metafile.outputs)[0];
  return output?.exports ?? [];
}
