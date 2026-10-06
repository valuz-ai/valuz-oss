// ``valuz-plugin test``: bundle every test/**/*.test.{ts,tsx,js,jsx,mjs} of a
// plugin with esbuild for Node and run the bundles with ``node --test``.
//
// No node_modules are needed in the plugin directory:
//   - ``@valuz/plugin-sdk`` and its subpaths are bundled from the SDK's own
//     sources (its package.json ``exports``), so the plugin, the SDK hooks and
//     the test host share one module instance;
//   - react / react-dom / scheduler stay external and are imported from the
//     SDK's node_modules by absolute file URL (realpath), so every importer —
//     the plugin, the SDK, react-dom/server — gets the same React;
//   - CSS imports load as empty modules, assets as data URLs.
import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, readdirSync, realpathSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { fileURLToPath, pathToFileURL } from "node:url";

import { loadEsbuild } from "../../src/build/esbuild.mjs";

const SDK_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const sdkRequire = createRequire(path.join(SDK_ROOT, "package.json"));

const TEST_FILE = /\.test\.(?:ts|tsx|js|jsx|mjs)$/;
const SKIP_DIRS = new Set(["node_modules", "dist", "frontend"]);
const REACT_FAMILY = /^(?:react|react-dom|scheduler)(?:\/.*)?$/;

/** test/**\/*.test.* files of the plugin (absolute, sorted). */
export function findTestFiles(root) {
  const start = path.join(root, "test");
  if (!existsSync(start)) return [];
  const out = [];
  const visit = (dir) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      if (entry.name.startsWith(".")) continue;
      const abs = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        if (!SKIP_DIRS.has(entry.name)) visit(abs);
      } else if (TEST_FILE.test(entry.name)) {
        out.push(abs);
      }
    }
  };
  visit(start);
  return out.sort();
}

/** ``@valuz/plugin-sdk[/sub]`` → the SDK source file its package.json exports. */
function sdkExportsMap() {
  const pkg = JSON.parse(readFileSync(path.join(SDK_ROOT, "package.json"), "utf8"));
  const map = new Map();
  for (const [subpath, target] of Object.entries(pkg.exports ?? {})) {
    const file = typeof target === "string" ? target : (target?.import ?? target?.default);
    if (typeof file !== "string") continue;
    const id = subpath === "." ? pkg.name : `${pkg.name}/${subpath.replace(/^\.\//, "")}`;
    map.set(id, path.join(SDK_ROOT, file));
  }
  return map;
}

function testResolvePlugin() {
  const sdkExports = sdkExportsMap();
  const reactCache = new Map();
  return {
    name: "valuz-plugin-test",
    setup(build) {
      build.onResolve({ filter: /^@valuz\/plugin-sdk(?:\/.*)?$/ }, (args) => {
        const file = sdkExports.get(args.path);
        if (!file) {
          return { errors: [{ text: `"${args.path}" is not an export of @valuz/plugin-sdk` }] };
        }
        return { path: file };
      });
      // Bundler-specific asset imports of the host's UI library (Vite's
      // ``?url`` / ``?raw`` / ``?worker``): a placeholder string under Node.
      build.onResolve({ filter: /\?(?:url|raw|inline|worker|sharedworker)(?:&.*)?$/ }, (args) => ({
        path: args.path,
        namespace: "valuz-test-asset",
      }));
      build.onLoad({ filter: /.*/, namespace: "valuz-test-asset" }, (args) => ({
        contents: `export default ${JSON.stringify(args.path.includes("?raw") ? "" : `/${path.basename(args.path.split("?")[0])}`)};`,
        loader: "js",
      }));
      build.onResolve({ filter: REACT_FAMILY }, (args) => {
        if (!reactCache.has(args.path)) {
          try {
            reactCache.set(args.path, realpathSync(sdkRequire.resolve(args.path)));
          } catch (error) {
            return { errors: [{ text: `cannot resolve "${args.path}" from @valuz/plugin-sdk: ${error.message}` }] };
          }
        }
        const file = reactCache.get(args.path);
        // ESM imports take a file URL (portable); require() of a bundled
        // CommonJS dependency takes the plain path.
        return { path: args.kind === "require-call" ? file : pathToFileURL(file).href, external: true };
      });
    },
  };
}

const ASSET_LOADERS = {
  ".css": "empty",
  ".png": "dataurl",
  ".jpg": "dataurl",
  ".jpeg": "dataurl",
  ".gif": "dataurl",
  ".webp": "dataurl",
  ".svg": "dataurl",
  ".woff": "dataurl",
  ".woff2": "dataurl",
  ".txt": "text",
  ".md": "text",
};

/**
 * Bundle the tests of ``dir`` into a temp directory. Resolves to
 * ``{ outDir, files: [{ source, bundle }], errors, warnings }``.
 */
export async function bundleTests(dir, testFiles) {
  const root = path.resolve(dir);
  const outDir = mkdtempSync(path.join(os.tmpdir(), "valuz-plugin-test-"));
  const esbuild = loadEsbuild();
  const format = (m) =>
    m.location ? `${path.relative(root, path.resolve(root, m.location.file))}:${m.location.line}:${m.location.column}: ${m.text}` : m.text;
  let result;
  try {
    result = await esbuild.build({
      entryPoints: testFiles,
      outdir: outDir,
      outbase: root,
      outExtension: { ".js": ".mjs" },
      bundle: true,
      format: "esm",
      platform: "node",
      target: "node18",
      jsx: "automatic",
      sourcemap: "inline",
      splitting: false,
      write: true,
      logLevel: "silent",
      loader: ASSET_LOADERS,
      // CommonJS dependencies bundled into an ES module still call require().
      banner: {
        js: 'import { createRequire as __valuzCreateRequire } from "node:module"; const require = __valuzCreateRequire(import.meta.url);',
      },
      plugins: [testResolvePlugin()],
      absWorkingDir: root,
    });
  } catch (error) {
    return {
      outDir,
      files: [],
      errors: (error.errors ?? [{ text: String(error.message ?? error) }]).map(format),
      warnings: (error.warnings ?? []).map(format),
    };
  }
  const files = testFiles.map((source) => {
    const rel = path.relative(root, source).replace(/\.(?:ts|tsx|js|jsx|mjs)$/, ".mjs");
    return { source, bundle: path.join(outDir, rel) };
  });
  return { outDir, files, errors: [], warnings: result.warnings.map(format) };
}

/**
 * Run the plugin's tests. Resolves to the exit code (0 = all passed).
 * ``options.log`` / ``options.error`` print progress; ``options.nodeArgs``
 * are extra arguments for ``node --test``.
 */
export async function runPluginTests(dir, options = {}) {
  const root = path.resolve(dir);
  const log = options.log ?? ((line) => process.stdout.write(`${line}\n`));
  const error = options.error ?? ((line) => process.stderr.write(`${line}\n`));
  const testFiles = findTestFiles(root);
  if (!testFiles.length) {
    log("no tests: add test/<name>.test.tsx (see the scaffold's test/plugin.test.tsx)");
    return 0;
  }
  const bundled = await bundleTests(root, testFiles);
  try {
    for (const warning of bundled.warnings) error(`warning: ${warning}`);
    if (bundled.errors.length) {
      for (const message of bundled.errors) error(`error: ${message}`);
      return 1;
    }
    const args = ["--test", ...(options.nodeArgs ?? []), ...bundled.files.map((f) => f.bundle)];
    return await new Promise((resolve) => {
      const child = spawn(process.execPath, args, {
        cwd: root,
        stdio: options.stdio ?? "inherit",
        env: { ...process.env, ELECTRON_RUN_AS_NODE: "1" },
      });
      child.on("error", (spawnError) => {
        error(`error: cannot run node --test: ${spawnError.message}`);
        resolve(1);
      });
      child.on("exit", (code, signal) => resolve(code ?? (signal ? 1 : 0)));
    });
  } finally {
    if (!options.keep) rmSync(bundled.outDir, { recursive: true, force: true });
  }
}
