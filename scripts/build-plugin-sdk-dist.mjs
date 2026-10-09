#!/usr/bin/env node
// build-plugin-sdk-dist.mjs — the self-contained distribution of the plugin
// SDK CLI (`valuz-plugin`: create / build / dev / test / validate / pack).
//
// In the monorepo the CLI runs from frontend/packages/plugin-sdk and
// `valuz-plugin test` bundles @valuz/plugin-sdk (+ /testing, /ui) from the
// TypeScript sources and @valuz/ui from the workspace. The packaged desktop app
// (libexec/plugin-sdk, run under its own Electron as Node) and the cloud kernel
// image (/app/vendor/plugin-sdk) have no monorepo, so they ship this instead:
//
//   <out>/
//     package.json              @valuz/plugin-sdk, exports → runtime/*.mjs
//     bin/valuz-plugin.mjs      the CLI (bin/lib/* unchanged)
//     src/build/*.mjs           the plain-ESM build core the CLI imports
//     templates/                `valuz-plugin create` scaffolds
//     valuz-plugin.schema.json  the manifest schema
//     runtime/{index,testing,ui}.mjs (+ chunks/)
//                               @valuz/plugin-sdk, /testing and /ui pre-bundled
//                               with esbuild (React kept external; shared code
//                               split into chunks so the three share one
//                               instance of the SDK core)
//     node_modules/             with --install: the pinned runtime deps
//                               (esbuild + React) from
//                               backend/vendor/plugin-sdk-cli
//
// The CLI tells the two layouts apart by runtime/index.mjs
// (frontend/packages/plugin-sdk/bin/lib/layout.mjs), so the same bin works in
// both.
//
// Prerequisite: the frontend workspace is installed (pnpm install) — the
// runtime is bundled from the SDK sources and @valuz/ui's dependencies.
//
// esbuild ships its native binary as a per-platform optional dependency
// (@esbuild/<os>-<cpu>). `--install` installs the one for --target (a dist tag
// like darwin-arm64 / linux-amd64 / windows-amd64, default: this host) via
// npm --os/--cpu, NOT whatever the build host is — the mac Intel package is
// built under Rosetta, the kernel image for linux-x64 — and with
// --ignore-scripts, so esbuild's postinstall cannot swap in (or download) the
// build host's binary. It then checks the expected package landed.
//
// Usage:
//   node scripts/build-plugin-sdk-dist.mjs [--out DIR] [--install] [--target TAG]
//   node scripts/build-plugin-sdk-dist.mjs --update-pins   # refresh the pins
//
//   --out DIR      output directory (replaced). Default:
//                  frontend/packages/plugin-sdk/dist-cli (gitignored)
//   --install      npm ci the pinned runtime deps into <out>/node_modules
//   --target TAG   platform the install is for: darwin|linux|windows|win32 -
//                  arm64|amd64|x64 (default: this host)
//   --update-pins  set backend/vendor/plugin-sdk-cli's pins to the versions the
//                  workspace resolves for @valuz/plugin-sdk and regenerate its
//                  package-lock.json (commit both)
import { spawnSync } from "node:child_process";
import {
  copyFileSync,
  cpSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readdirSync,
  readFileSync,
  realpathSync,
  renameSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { fileURLToPath, pathToFileURL } from "node:url";

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(SCRIPT_DIR, "..");
const SDK_DIR = path.join(REPO_ROOT, "frontend", "packages", "plugin-sdk");
const PIN_DIR = path.join(REPO_ROOT, "backend", "vendor", "plugin-sdk-cli");
const DEFAULT_OUT = path.join(SDK_DIR, "dist-cli");
/** The runtime deps the distribution pins (their versions must match the workspace). */
const PINNED = ["esbuild", "react", "react-dom"];
const REACT_EXTERNALS = ["react", "react/*", "react-dom", "react-dom/*", "scheduler", "scheduler/*"];
/** Same loaders as `valuz-plugin test` (the runtime only ever runs under it). */
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
  ".ttf": "dataurl",
  ".otf": "dataurl",
  ".txt": "text",
  ".md": "text",
};

const log = (line) => process.stdout.write(`[plugin-sdk-dist] ${line}\n`);
const fail = (message) => {
  process.stderr.write(`[plugin-sdk-dist] ERROR: ${message}\n`);
  process.exit(1);
};

// ── arguments ─────────────────────────────────────────────────────────────────

function parseArgs(argv) {
  const options = { out: DEFAULT_OUT, install: false, target: null, updatePins: false };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    const value = () => {
      const eq = arg.indexOf("=");
      if (eq !== -1) return arg.slice(eq + 1);
      i += 1;
      if (argv[i] === undefined) fail(`${arg} needs a value`);
      return argv[i];
    };
    if (arg === "--install") options.install = true;
    else if (arg === "--update-pins") options.updatePins = true;
    else if (arg === "--out" || arg.startsWith("--out=")) options.out = path.resolve(value());
    else if (arg === "--target" || arg.startsWith("--target=")) options.target = value();
    else if (arg === "-h" || arg === "--help") {
      process.stdout.write(`${readFileSync(fileURLToPath(import.meta.url), "utf8").split("\nimport ")[0]}\n`);
      process.exit(0);
    } else fail(`unknown argument ${arg}`);
  }
  return options;
}

/** A dist tag (darwin-arm64, linux-amd64, windows-amd64, …) → npm's os / cpu. */
function npmPlatform(tag) {
  const [platform, arch, ...rest] = String(tag).toLowerCase().split("-");
  const osName = { darwin: "darwin", mac: "darwin", linux: "linux", windows: "win32", win32: "win32" }[platform];
  const cpu = { arm64: "arm64", aarch64: "arm64", amd64: "x64", x64: "x64", x86_64: "x64" }[arch];
  if (!osName || !cpu || rest.length) {
    fail(`unsupported --target ${tag} (expected <darwin|linux|windows>-<arm64|amd64>)`);
  }
  return { os: osName, cpu };
}

// ── workspace ─────────────────────────────────────────────────────────────────

const sdkRequire = createRequire(path.join(SDK_DIR, "package.json"));

/** @valuz/ui's sources as esbuild sees them (symlinks resolved). */
function uiSourceDir() {
  const dir = path.join(SDK_DIR, "node_modules", "@valuz", "ui", "src");
  if (!existsSync(dir)) fail(`${dir} not found — install the frontend workspace first (pnpm install)`);
  return realpathSync(dir);
}

function workspaceVersion(name) {
  try {
    return JSON.parse(readFileSync(sdkRequire.resolve(`${name}/package.json`), "utf8")).version;
  } catch {
    fail(`cannot resolve ${name} from ${SDK_DIR} — install the frontend workspace first (pnpm install)`);
  }
}

function readJson(file) {
  return JSON.parse(readFileSync(file, "utf8"));
}

/** The pins must equal what the workspace resolves: the runtime is built against those. */
function checkPins() {
  const pins = readJson(path.join(PIN_DIR, "package.json")).dependencies ?? {};
  const drift = PINNED.filter((name) => pins[name] !== workspaceVersion(name)).map(
    (name) => `${name}: pinned ${pins[name] ?? "(none)"}, workspace ${workspaceVersion(name)}`,
  );
  if (drift.length) {
    fail(
      `backend/vendor/plugin-sdk-cli pins drifted from the workspace:\n  ${drift.join("\n  ")}\n` +
        "Refresh: node scripts/build-plugin-sdk-dist.mjs --update-pins (then commit package.json + package-lock.json)",
    );
  }
  return pins;
}

function run(command, args, cwd) {
  const result = spawnSync(command, args, {
    cwd,
    stdio: "inherit",
    // npm is npm.cmd on Windows (git-bash runner).
    shell: process.platform === "win32",
  });
  if (result.error) fail(`${command} ${args.join(" ")}: ${result.error.message}`);
  if (result.status !== 0) fail(`${command} ${args.join(" ")} exited ${result.status}`);
}

function updatePins() {
  const file = path.join(PIN_DIR, "package.json");
  const pkg = readJson(file);
  pkg.dependencies = Object.fromEntries(PINNED.map((name) => [name, workspaceVersion(name)]));
  writeFileSync(file, `${JSON.stringify(pkg, null, 2)}\n`);
  log(`pins: ${PINNED.map((name) => `${name}@${pkg.dependencies[name]}`).join(", ")}`);
  // Resolved URLs on registry.npmjs.org: npm rewrites that host to whichever
  // registry an install is configured with (the kernel image's mirror, …).
  run(
    "npm",
    [
      "install",
      "--package-lock-only",
      "--ignore-scripts",
      "--no-audit",
      "--no-fund",
      "--loglevel=error",
      "--registry=https://registry.npmjs.org/",
    ],
    PIN_DIR,
  );
  log(`regenerated ${path.relative(REPO_ROOT, path.join(PIN_DIR, "package-lock.json"))}`);
}

// ── distribution ──────────────────────────────────────────────────────────────

function guardOut(out) {
  const inside = (child, parent) => {
    const rel = path.relative(parent, child);
    return rel === "" || (!rel.startsWith("..") && !path.isAbsolute(rel));
  };
  if (out === path.parse(out).root || inside(SDK_DIR, out) || inside(REPO_ROOT, out)) {
    fail(`refusing to replace ${out} (it contains the SDK or the repository)`);
  }
}

function copySdkFiles(out) {
  // The CLI as is, without the .d.mts declarations its tests type against.
  cpSync(path.join(SDK_DIR, "bin"), path.join(out, "bin"), {
    recursive: true,
    filter: (source) => !source.endsWith(".d.mts"),
  });
  cpSync(path.join(SDK_DIR, "templates"), path.join(out, "templates"), { recursive: true });
  const buildDir = path.join(out, "src", "build");
  mkdirSync(buildDir, { recursive: true });
  for (const name of readdirSync(path.join(SDK_DIR, "src", "build"))) {
    // The plain-ESM core only: not the .d.mts declarations, tests or index.ts.
    if (name.endsWith(".mjs")) copyFileSync(path.join(SDK_DIR, "src", "build", name), path.join(buildDir, name));
  }
  copyFileSync(path.join(SDK_DIR, "valuz-plugin.schema.json"), path.join(out, "valuz-plugin.schema.json"));
  const license = path.join(REPO_ROOT, "LICENSE");
  if (existsSync(license)) copyFileSync(license, path.join(out, "LICENSE"));
}

/** Vite-only asset imports (``?url`` / ``?raw`` / ``?worker``) of @valuz/ui → placeholders. */
const viteAssetPlugin = {
  name: "valuz-plugin-sdk-dist-assets",
  setup(build) {
    build.onResolve({ filter: /\?(?:url|raw|inline|worker|sharedworker)(?:&.*)?$/ }, (args) => ({
      path: args.path,
      namespace: "valuz-dist-asset",
    }));
    build.onLoad({ filter: /.*/, namespace: "valuz-dist-asset" }, (args) => ({
      contents: `export default ${JSON.stringify(args.path.includes("?raw") ? "" : `/${path.basename(args.path.split("?")[0])}`)};`,
      loader: "js",
    }));
  },
};

/**
 * @valuz/ui's own modules are side-effect free (its only bare imports are
 * CSS, empty under Node): mark them so, letting esbuild drop the parts of the
 * barrel the SDK's /ui subset does not re-export — the document renderers,
 * editors, diagram and syntax-highlighting grammars that would otherwise ride
 * along (and their lazy imports become hundreds of chunks).
 */
function sideEffectFreeUiPlugin(uiSrc) {
  const inUi = (file) => file === uiSrc || file.startsWith(`${uiSrc}${path.sep}`);
  return {
    name: "valuz-plugin-sdk-dist-ui-tree-shaking",
    setup(build) {
      build.onResolve({ filter: /.*/ }, async (args) => {
        if (args.pluginData?.valuzUiResolving) return undefined;
        if (args.path !== "@valuz/ui" && !inUi(args.importer)) return undefined;
        const result = await build.resolve(args.path, {
          kind: args.kind,
          importer: args.importer,
          resolveDir: args.resolveDir,
          pluginData: { valuzUiResolving: true },
        });
        if (result.errors.length) return { errors: result.errors };
        if (result.external || !inUi(result.path) || result.path.endsWith(".css")) return undefined;
        return { path: result.path, namespace: result.namespace, sideEffects: false };
      });
    },
  };
}

async function bundleRuntime(out, layout) {
  let esbuild;
  try {
    esbuild = sdkRequire("esbuild");
  } catch {
    fail(`cannot load esbuild from ${SDK_DIR} — install the frontend workspace first (pnpm install)`);
  }
  const runtimeDir = path.join(out, layout.RUNTIME_DIR);
  const entryPoints = Object.fromEntries(
    layout.RUNTIME_ENTRIES.map((entry) => [
      entry.bundle.replace(/\.mjs$/, ""),
      path.join(SDK_DIR, entry.source),
    ]),
  );
  let result;
  try {
    result = await esbuild.build({
      entryPoints,
      outdir: runtimeDir,
      outExtension: { ".js": ".mjs" },
      chunkNames: "chunks/[name]-[hash]",
      bundle: true,
      splitting: true,
      format: "esm",
      // The runtime is only ever bundled again by `valuz-plugin test`
      // (platform node, target node18) — build it the same way.
      platform: "node",
      target: "node18",
      jsx: "automatic",
      external: REACT_EXTERNALS,
      loader: ASSET_LOADERS,
      plugins: [viteAssetPlugin, sideEffectFreeUiPlugin(uiSourceDir())],
      legalComments: "eof",
      metafile: true,
      logLevel: "silent",
      absWorkingDir: SDK_DIR,
    });
  } catch (error) {
    const lines = (error.errors ?? [{ text: String(error.message ?? error) }]).map(
      (m) => (m.location ? `${m.location.file}:${m.location.line}: ${m.text}` : m.text),
    );
    fail(`bundling the runtime failed:\n  ${lines.join("\n  ")}`);
  }
  for (const warning of result.warnings) log(`warning: ${warning.text}`);
  // esbuild makes a chunk of every ``import()`` target it parses, even when
  // tree-shaking then drops the importing code — keep only the files the
  // three entries actually reach.
  const metaOutputs = new Map(
    Object.entries(result.metafile.outputs).map(([file, meta]) => [path.resolve(SDK_DIR, file), meta]),
  );
  const reachable = new Set();
  const pending = layout.RUNTIME_ENTRIES.map((entry) => path.join(runtimeDir, entry.bundle));
  while (pending.length) {
    const file = pending.pop();
    if (reachable.has(file)) continue;
    reachable.add(file);
    for (const imported of metaOutputs.get(file)?.imports ?? []) {
      if (!imported.external) pending.push(path.resolve(SDK_DIR, imported.path));
    }
  }
  const outputs = [];
  for (const [file, meta] of metaOutputs) {
    if (reachable.has(file)) outputs.push({ file: path.relative(out, file), bytes: meta.bytes });
    else rmSync(file, { force: true });
  }
  return outputs;
}

function writePackageJson(out, layout, pins) {
  const source = readJson(path.join(SDK_DIR, "package.json"));
  const exports = {};
  for (const entry of layout.RUNTIME_ENTRIES) {
    const subpath = entry.id === layout.PACKAGE_NAME ? "." : `./${entry.id.slice(layout.PACKAGE_NAME.length + 1)}`;
    exports[subpath] = `./${layout.RUNTIME_DIR}/${entry.bundle}`;
  }
  exports[`./${layout.SCHEMA_FILE}`] = `./${layout.SCHEMA_FILE}`;
  const pkg = {
    name: source.name,
    version: source.version,
    private: true,
    description: `${source.description} — self-contained CLI distribution (scripts/build-plugin-sdk-dist.mjs)`,
    type: "module",
    exports,
    bin: source.bin,
    dependencies: pins,
  };
  writeFileSync(path.join(out, "package.json"), `${JSON.stringify(pkg, null, 2)}\n`);
}

function installDeps(out, target) {
  const { os: npmOs, cpu } = target
    ? npmPlatform(target)
    : { os: process.platform, cpu: process.arch };
  const work = mkdtempSync(path.join(os.tmpdir(), "valuz-plugin-sdk-deps-"));
  try {
    copyFileSync(path.join(PIN_DIR, "package.json"), path.join(work, "package.json"));
    copyFileSync(path.join(PIN_DIR, "package-lock.json"), path.join(work, "package-lock.json"));
    log(`npm ci (pinned runtime deps, ${npmOs}-${cpu}) ...`);
    run(
      "npm",
      [
        "ci",
        "--omit=dev",
        "--ignore-scripts",
        `--os=${npmOs}`,
        `--cpu=${cpu}`,
        "--no-audit",
        "--no-fund",
        "--loglevel=error",
      ],
      work,
    );
    const nodeModules = path.join(out, "node_modules");
    rmSync(nodeModules, { recursive: true, force: true });
    try {
      renameSync(path.join(work, "node_modules"), nodeModules);
    } catch {
      // Different filesystem (tmp vs. the app's resources): copy instead.
      cpSync(path.join(work, "node_modules"), nodeModules, { recursive: true });
    }
  } finally {
    rmSync(work, { recursive: true, force: true });
  }
  const nativePackage = `@esbuild/${npmOs}-${cpu}`;
  for (const name of [...PINNED, nativePackage]) {
    if (!existsSync(path.join(out, "node_modules", name, "package.json"))) {
      fail(`npm ci did not install ${name} into ${out}/node_modules (npm too old for --os/--cpu?)`);
    }
  }
  const strays = readdirSync(path.join(out, "node_modules", "@esbuild")).filter(
    (name) => `@esbuild/${name}` !== nativePackage,
  );
  if (strays.length) fail(`unexpected esbuild binaries for other platforms: ${strays.join(", ")}`);
  return nativePackage;
}

function dirSize(dir) {
  let total = 0;
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const abs = path.join(dir, entry.name);
    if (entry.isDirectory()) total += dirSize(abs);
    else if (entry.isFile()) total += statSync(abs).size;
  }
  return total;
}

const mb = (bytes) => `${(bytes / 1024 / 1024).toFixed(1)} MB`;

async function main() {
  const options = parseArgs(process.argv.slice(2));
  if (options.updatePins) {
    updatePins();
    return;
  }
  const pins = checkPins();
  const layout = await import(pathToFileURL(path.join(SDK_DIR, "bin", "lib", "layout.mjs")).href);
  const out = options.out;
  guardOut(out);

  rmSync(out, { recursive: true, force: true });
  mkdirSync(out, { recursive: true });
  copySdkFiles(out);
  const outputs = await bundleRuntime(out, layout);
  writePackageJson(out, layout, pins);
  if (!layout.isDistLayout(out)) fail(`${out} is not recognised as a distribution (layout.mjs)`);
  for (const output of outputs.filter((o) => !o.file.includes("chunks"))) {
    log(`  ${output.file.padEnd(20)} ${(output.bytes / 1024).toFixed(0)} KB`);
  }
  log(`  ${outputs.length} runtime files (${mb(outputs.reduce((sum, o) => sum + o.bytes, 0))})`);
  if (options.install) {
    const nativePackage = installDeps(out, options.target);
    log(`installed ${PINNED.map((name) => `${name}@${pins[name]}`).join(", ")} + ${nativePackage}`);
  }
  log(`built ${out} (${mb(dirSize(out))})`);
}

await main();
