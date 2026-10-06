// @valuz/plugin-sdk/build — the build preset for third-party Valuz plugins
// (doc 12 §7, task card 04 §E).
//
// The implementation is plain ESM (``*.mjs`` + ``*.d.mts``) so the
// ``valuz-plugin`` bin can run it with bare Node, without a build step and
// from a plugin directory that has no node_modules; this entry only gathers
// the public surface for TypeScript / Vite users.

export {
  SHARED_GLOBAL,
  SHARED_MODULES,
  SHARED_MODULE_IDS,
  isSharedModule,
  type SharedModuleId,
} from "./shared.mjs";
export { scopeCss, scopeSelector } from "./css-scope.mjs";
export {
  checkBundleImports,
  listBundleImports,
  type BundleImport,
} from "./imports.mjs";
export {
  esbuildPreset,
  valuzEsbuildPlugin,
  type EsbuildPresetOptions,
  type ValuzEsbuildPluginOptions,
} from "./esbuild-preset.mjs";
export {
  buildPlugin,
  resolveSourceEntry,
  type BuildOutput,
  type BuildPluginOptions,
  type BuildReport,
  type WatchHandle,
} from "./build-plugin.mjs";
export { validatePlugin, type ValidationResult } from "./validate.mjs";
export {
  PackError,
  packFileSet,
  packPlugin,
  type PackResult,
} from "./pack.mjs";
export {
  MANIFEST_FILE,
  ManifestError,
  SCHEMA_PATH,
  readManifest,
  type PluginManifest,
} from "./manifest.mjs";
export {
  valuzPlugin,
  type ValuzVitePlugin,
  type ValuzVitePluginOptions,
} from "./vite.mjs";
