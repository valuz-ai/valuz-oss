// esbuild, resolved from the SDK's own dependencies (not from the plugin
// directory, which may have no node_modules at all).
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);

let cached = null;

/** The SDK's esbuild module (loaded on first use). */
export function loadEsbuild() {
  if (!cached) cached = require("esbuild");
  return cached;
}

/** ``require.resolve`` from the SDK package (React, @types, …). */
export function resolveFromSdk(request) {
  return require.resolve(request);
}
