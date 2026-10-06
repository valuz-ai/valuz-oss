// Shared modules: what the host provides to every third-party plugin at runtime
// (task card 04 §E, doc 02 §2.3). A plugin bundle must not carry its own copy
// of these; the build preset rewrites every import of them to a read of
// ``globalThis.__VALUZ_PLUGIN_SHARED__[id]``.
//
// Plain ESM (no TypeScript) so the ``valuz-plugin`` bin can import it without a
// build step; types are in ``shared.d.mts``.

/** The global the host fills before it imports a plugin. */
export const SHARED_GLOBAL = "__VALUZ_PLUGIN_SHARED__";

/** Module ids the host provides (the runtime's own table must match). */
export const SHARED_MODULE_IDS = Object.freeze([
  "react",
  "react/jsx-runtime",
  "react-dom",
  "react-dom/client",
  "@valuz/plugin-sdk",
  "@valuz/plugin-sdk/ui",
]);

/** id → the runtime expression a bundle reads it from. */
export const SHARED_MODULES = Object.freeze(
  Object.fromEntries(
    SHARED_MODULE_IDS.map((id) => [
      id,
      `globalThis.${SHARED_GLOBAL}[${JSON.stringify(id)}]`,
    ]),
  ),
);

/** Whether ``id`` is one of the host's shared modules. */
export function isSharedModule(id) {
  return SHARED_MODULE_IDS.includes(id);
}

const escapeRegExp = (text) => text.replace(/[.*+?^${}()|[\]\\/]/g, "\\$&");

/** Matches exactly the shared module ids (for esbuild ``onResolve`` filters). */
export const SHARED_MODULE_FILTER = new RegExp(
  `^(?:${SHARED_MODULE_IDS.map(escapeRegExp).join("|")})$`,
);

/**
 * Why a bare import cannot be used by a plugin, or ``null`` when it may be
 * bundled. Internal Valuz packages are never available at runtime (bundling
 * them would create a second registry), and the SDK's build / testing / host
 * entries are Node-side or host-side only.
 */
export function forbiddenImportReason(id) {
  if (id === "@valuz/plugin-sdk" || id === "@valuz/plugin-sdk/ui") return null;
  if (id.startsWith("@valuz/plugin-sdk/")) {
    return `"${id}" is not available inside a plugin at runtime; a plugin may import only "@valuz/plugin-sdk" and "@valuz/plugin-sdk/ui"`;
  }
  if (id === "@valuz" || id.startsWith("@valuz/")) {
    return `"${id}" is an internal Valuz package; plugins import Valuz APIs only from "@valuz/plugin-sdk" and "@valuz/plugin-sdk/ui"`;
  }
  return null;
}

/**
 * The CommonJS body of the virtual module for shared id ``id``. Bundlers wrap
 * a CommonJS module's ``module.exports`` for ESM importers and turn every named
 * import into a property read, so ``import React, { useState, anyName } from
 * "react"`` works for any name the plugin uses (default = ``m`` unless ``m``
 * is flagged ``__esModule``, then ``m.default``).
 */
export function sharedModuleCommonJs(id) {
  const key = JSON.stringify(id);
  return [
    `var shared = globalThis.${SHARED_GLOBAL};`,
    `var m = shared && shared[${key}];`,
    `if (!m) throw new Error(${JSON.stringify(
      `[valuz-plugin] the host does not provide the shared module "${id}" (globalThis.${SHARED_GLOBAL})`,
    )});`,
    `module.exports = m;`,
    ``,
  ].join("\n");
}

/**
 * ``react/jsx-dev-runtime`` is not shared (the host ships a production React):
 * a development-mode JSX transform in a plugin is mapped onto the shared
 * ``react/jsx-runtime`` (``jsxDEV(type, props, key, isStatic)`` → ``jsx`` / ``jsxs``).
 */
export const JSX_DEV_RUNTIME_ID = "react/jsx-dev-runtime";

const jsxDevBody = (exportLine) => [
  `var shared = globalThis.${SHARED_GLOBAL};`,
  `var m = shared && shared["react/jsx-runtime"];`,
  `if (!m) throw new Error(${JSON.stringify(
    `[valuz-plugin] the host does not provide the shared module "react/jsx-runtime" (globalThis.${SHARED_GLOBAL})`,
  )});`,
  `function jsxDEV(type, props, key, isStatic) { return (isStatic ? m.jsxs : m.jsx)(type, props, key); }`,
  exportLine,
  ``,
].join("\n");

/** CommonJS body of the ``react/jsx-dev-runtime`` virtual module. */
export function jsxDevRuntimeCommonJs() {
  return jsxDevBody("module.exports = { jsxDEV: jsxDEV, Fragment: m.Fragment };");
}

/** ESM body of the ``react/jsx-dev-runtime`` virtual module. */
export function jsxDevRuntimeEsm() {
  return jsxDevBody("var Fragment = m.Fragment;\nexport { jsxDEV, Fragment };");
}

/** Name of the export Rollup reads synthetic named exports from. */
export const SYNTHETIC_EXPORT = "__valuzShared";

/**
 * The ESM body of the virtual module for Rollup / Vite. Rollup's
 * ``syntheticNamedExports`` resolves any named import that the module does not
 * declare as a property of ``SYNTHETIC_EXPORT``.
 */
export function sharedModuleEsm(id) {
  const key = JSON.stringify(id);
  return [
    `const shared = globalThis.${SHARED_GLOBAL};`,
    `const m = shared && shared[${key}];`,
    `if (!m) throw new Error(${JSON.stringify(
      `[valuz-plugin] the host does not provide the shared module "${id}" (globalThis.${SHARED_GLOBAL})`,
    )});`,
    `export const ${SYNTHETIC_EXPORT} = m;`,
    `export default (m.default ?? m);`,
    ``,
  ].join("\n");
}
