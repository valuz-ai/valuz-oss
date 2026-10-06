// ``valuzPlugin()``: the build preset as a Vite (Rollup) plugin, for plugin
// authors who build with Vite (doc 12 §7):
//
//   // vite.config.ts
//   import { defineConfig } from "vite";
//   import { valuzPlugin } from "@valuz/plugin-sdk/build";
//   export default defineConfig({ plugins: [valuzPlugin()] });
//
// Same output contract as the esbuild preset: library mode, one ES module at
// the manifest's ``frontend.entry`` (+ ``<entry>.css``), shared modules read
// from ``globalThis.__VALUZ_PLUGIN_SHARED__`` (virtual modules with Rollup's
// ``syntheticNamedExports``, so any named import works), CSS scoped to the
// plugin root, and the bundle import check on the emitted chunk. Applies to
// ``vite build`` only.
import path from "node:path";
import process from "node:process";

import { resolveSourceEntry } from "./build-plugin.mjs";
import { scopeCss } from "./css-scope.mjs";
import { checkBundleImports } from "./imports.mjs";
import { frontendEntry, readManifest } from "./manifest.mjs";
import {
  JSX_DEV_RUNTIME_ID,
  SYNTHETIC_EXPORT,
  forbiddenImportReason,
  isSharedModule,
  jsxDevRuntimeEsm,
  sharedModuleEsm,
} from "./shared.mjs";

const VIRTUAL_PREFIX = "\0valuz-shared:";
// Style modules Vite compiles to CSS (the transform below sees the compiled
// CSS); ``?raw`` / ``?url`` imports are strings / URLs, not CSS.
const CSS_ID = /\.(?:css|scss|sass|less|styl|stylus|pcss|postcss)(?:$|\?)/;
const NOT_CSS_QUERY = /[?&](?:raw|url|worker|sharedworker)(?:&|=|$)/;

/**
 * Options: ``root`` (the plugin directory; default Vite's root), ``pluginId``
 * (default the manifest id), ``entry`` (source entry; default as
 * ``valuz-plugin build``), ``scopeStyles`` (default true).
 */
export function valuzPlugin(options = {}) {
  const state = { pluginId: options.pluginId ?? null };

  const shared = {
    name: "valuz-plugin:shared",
    enforce: "pre",
    apply: "build",
    config(userConfig, env) {
      const root = path.resolve(options.root ?? userConfig.root ?? process.cwd());
      const manifest = readManifest(root);
      state.pluginId ??= manifest.id;
      const entryRel = frontendEntry(manifest);
      const outDir = path.dirname(entryRel);
      const fileName = path.basename(entryRel);
      const source = options.entry
        ? path.resolve(root, options.entry)
        : resolveSourceEntry(root);
      const production = (env?.mode ?? "production") === "production";
      return {
        // The automatic JSX runtime (react/jsx-runtime is a shared module);
        // @vitejs/plugin-react, when present, does the same.
        esbuild: { jsx: "automatic", jsxImportSource: "react", jsxDev: false },
        define: {
          "process.env.NODE_ENV": JSON.stringify(production ? "production" : "development"),
        },
        build: {
          outDir: path.join(root, outDir === "." ? "" : outDir),
          emptyOutDir: false,
          copyPublicDir: false,
          target: "es2022",
          cssCodeSplit: false,
          assetsInlineLimit: () => true,
          lib: {
            entry: source,
            formats: ["es"],
            fileName: () => fileName,
            cssFileName: fileName.replace(/\.m?js$/, ""),
          },
          rollupOptions: {
            output: { inlineDynamicImports: true },
          },
        },
      };
    },
    resolveId(id) {
      if (isSharedModule(id) || id === JSX_DEV_RUNTIME_ID) return VIRTUAL_PREFIX + id;
      const reason = forbiddenImportReason(id);
      if (reason) this.error(reason);
      return null;
    },
    load(id) {
      if (!id.startsWith(VIRTUAL_PREFIX)) return null;
      if (id === VIRTUAL_PREFIX + JSX_DEV_RUNTIME_ID) return { code: jsxDevRuntimeEsm() };
      return {
        code: sharedModuleEsm(id.slice(VIRTUAL_PREFIX.length)),
        syntheticNamedExports: SYNTHETIC_EXPORT,
      };
    },
  };

  // A normal-order plugin: runs after Vite's CSS compile (so @import and
  // preprocessors are resolved) and before the CSS is extracted.
  const styles = {
    name: "valuz-plugin:styles",
    apply: "build",
    transform(code, id) {
      if (options.scopeStyles === false || !state.pluginId) return null;
      if (!CSS_ID.test(id) || NOT_CSS_QUERY.test(id) || id.startsWith("\0")) return null;
      return { code: scopeCss(code, state.pluginId), map: null };
    },
    generateBundle(_outputOptions, bundle) {
      for (const output of Object.values(bundle)) {
        if (output.type !== "chunk") continue;
        const disallowed = checkBundleImports(output.code);
        if (disallowed.length) {
          this.error(
            `${output.fileName} would import ${disallowed.map((s) => `"${s}"`).join(", ")} at runtime; a plugin bundle must be self-contained (only react, react-dom and @valuz/plugin-sdk come from the host)`,
          );
        }
      }
    },
  };

  return [shared, styles];
}
