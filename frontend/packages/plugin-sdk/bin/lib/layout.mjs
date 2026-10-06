// Where ``@valuz/plugin-sdk`` comes from when ``valuz-plugin test`` bundles a
// plugin's tests. The same bin/ runs from two layouts:
//
//   source  the package inside the Valuz monorepo (frontend/packages/plugin-sdk):
//           the specifiers map to the TypeScript sources its package.json
//           ``exports`` names, and ``/ui`` pulls the host's component library
//           (@valuz/ui) from the workspace.
//   dist    the self-contained CLI distribution the packaged desktop app
//           (libexec/plugin-sdk) and the cloud kernel image ship, built by
//           scripts/build-plugin-sdk-dist.mjs: no monorepo sources, the runtime
//           pre-bundled into runtime/*.mjs with React kept external, so its
//           node_modules only needs esbuild and React.
//
// The dist layout wins whenever runtime/index.mjs exists.
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

/** Root of the SDK this bin/ belongs to (the package, or the distribution). */
export const SDK_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");

export const PACKAGE_NAME = "@valuz/plugin-sdk";
export const SCHEMA_FILE = "valuz-plugin.schema.json";

/** Directory of the pre-bundled runtime inside a distribution. */
export const RUNTIME_DIR = "runtime";

/**
 * The pre-bundled runtime: each specifier a plugin (or its tests) imports, the
 * bundle the distribution carries for it, and the source it is built from.
 */
export const RUNTIME_ENTRIES = Object.freeze([
  { id: PACKAGE_NAME, bundle: "index.mjs", source: "src/index.ts" },
  { id: `${PACKAGE_NAME}/testing`, bundle: "testing.mjs", source: "src/testing/index.ts" },
  { id: `${PACKAGE_NAME}/ui`, bundle: "ui.mjs", source: "src/ui/index.ts" },
]);

/** ``@valuz/plugin-sdk[/sub]`` → the file the package.json ``exports`` names. */
function exportsMap(root) {
  const file = path.join(root, "package.json");
  if (!existsSync(file)) return new Map();
  const pkg = JSON.parse(readFileSync(file, "utf8"));
  const name = pkg.name ?? PACKAGE_NAME;
  const map = new Map();
  for (const [subpath, target] of Object.entries(pkg.exports ?? {})) {
    const rel = typeof target === "string" ? target : (target?.import ?? target?.default);
    if (typeof rel !== "string") continue;
    const id = subpath === "." ? name : `${name}/${subpath.replace(/^\.\//, "")}`;
    map.set(id, path.join(root, rel));
  }
  return map;
}

/** Whether ``root`` is a distribution (carries the pre-bundled runtime). */
export function isDistLayout(root = SDK_ROOT) {
  return existsSync(path.join(root, RUNTIME_DIR, RUNTIME_ENTRIES[0].bundle));
}

/**
 * The SDK layout at ``root``: ``{ kind: "dist" | "source", root, modules }``,
 * ``modules`` mapping each importable specifier to an absolute file. A dist
 * maps the runtime specifiers to its bundles (a bundle that is missing still
 * maps — the caller reports it) plus the manifest schema; a source checkout
 * maps everything its package.json exports.
 */
export function sdkLayout(root = SDK_ROOT) {
  if (isDistLayout(root)) {
    const modules = new Map(
      RUNTIME_ENTRIES.map((entry) => [entry.id, path.join(root, RUNTIME_DIR, entry.bundle)]),
    );
    modules.set(`${PACKAGE_NAME}/${SCHEMA_FILE}`, path.join(root, SCHEMA_FILE));
    return { kind: "dist", root, modules };
  }
  return { kind: "source", root, modules: exportsMap(root) };
}
