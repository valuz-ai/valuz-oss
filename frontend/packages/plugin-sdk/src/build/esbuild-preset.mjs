// The esbuild preset for a plugin's frontend bundle (doc 12 §7, doc 02 §2.3):
// one ES module, shared modules read from the host's table, CSS scoped to the
// plugin root and emitted next to the JS.
import { readFile } from "node:fs/promises";
import path from "node:path";

import { scopeCss } from "./css-scope.mjs";
import {
  JSX_DEV_RUNTIME_ID,
  SHARED_MODULE_FILTER,
  forbiddenImportReason,
  jsxDevRuntimeCommonJs,
  sharedModuleCommonJs,
} from "./shared.mjs";

const SHARED_NAMESPACE = "valuz-shared";

/** Assets are inlined: the frontend is one JS file (+ one CSS file). */
const ASSET_LOADERS = {
  ".png": "dataurl",
  ".jpg": "dataurl",
  ".jpeg": "dataurl",
  ".gif": "dataurl",
  ".webp": "dataurl",
  ".avif": "dataurl",
  ".svg": "dataurl",
  ".ico": "dataurl",
  ".woff": "dataurl",
  ".woff2": "dataurl",
  ".ttf": "dataurl",
  ".otf": "dataurl",
  ".txt": "text",
  ".md": "text",
};

/**
 * The esbuild plugin: shared modules → virtual modules reading
 * ``globalThis.__VALUZ_PLUGIN_SHARED__``; internal ``@valuz/*`` packages →
 * build error; ``.css`` → scoped to ``[data-valuz-ext="<pluginId>"]``.
 */
export function valuzEsbuildPlugin(options = {}) {
  const { pluginId, scopeStyles = true } = options;
  return {
    name: "valuz-plugin",
    setup(build) {
      build.onResolve({ filter: SHARED_MODULE_FILTER }, (args) => ({
        path: args.path,
        namespace: SHARED_NAMESPACE,
      }));
      build.onResolve({ filter: /^react\/jsx-dev-runtime$/ }, (args) => ({
        path: args.path,
        namespace: SHARED_NAMESPACE,
      }));
      build.onResolve({ filter: /^@valuz(?:\/|$)/ }, (args) => {
        const reason = forbiddenImportReason(args.path);
        return reason ? { errors: [{ text: reason }] } : undefined;
      });
      build.onLoad({ filter: /.*/, namespace: SHARED_NAMESPACE }, (args) => ({
        contents:
          args.path === JSX_DEV_RUNTIME_ID
            ? jsxDevRuntimeCommonJs()
            : sharedModuleCommonJs(args.path),
        loader: "js",
      }));
      if (scopeStyles && pluginId) {
        build.onLoad({ filter: /\.css$/, namespace: "file" }, async (args) => {
          const text = await readFile(args.path, "utf8");
          return {
            contents: scopeCss(text, pluginId),
            loader: /\.module\.css$/.test(args.path) ? "local-css" : "css",
            resolveDir: path.dirname(args.path),
          };
        });
      }
    },
  };
}

/**
 * esbuild ``BuildOptions`` for a plugin frontend: ESM, one file (no
 * splitting), automatic JSX, es2022, browser; CSS imported from JS is bundled
 * into ``<outfile without .js>.css``.
 */
export function esbuildPreset(options) {
  const { pluginId, entry, outfile, minify = false, sourcemap = false } = options;
  if (!pluginId) throw new Error("esbuildPreset: pluginId is required");
  if (!entry) throw new Error("esbuildPreset: entry is required");
  if (!outfile) throw new Error("esbuildPreset: outfile is required");
  return {
    entryPoints: [entry],
    outfile,
    bundle: true,
    splitting: false,
    format: "esm",
    platform: "browser",
    target: "es2022",
    jsx: "automatic",
    minify,
    sourcemap,
    metafile: true,
    logLevel: "silent",
    loader: { ...ASSET_LOADERS },
    define: {
      "process.env.NODE_ENV": JSON.stringify(minify ? "production" : "development"),
    },
    plugins: [valuzEsbuildPlugin({ pluginId })],
  };
}
