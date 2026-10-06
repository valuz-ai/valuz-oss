// ``valuz-plugin create <dir> --id <publisher.name>``: scaffold a plugin from
// templates/plugin/ (``*.tmpl`` files, ``__PLUGIN_ID__``-style placeholders)
// plus the generated files that carry absolute SDK paths (valuz-plugin.json's
// "$schema", tsconfig.json's "paths", package.json).
import { existsSync, mkdirSync, readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { MANIFEST_FILE, SCHEMA_PATH, reservedPrefixes } from "../../src/build/manifest.mjs";

const SDK_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const TEMPLATE_DIR = path.join(SDK_ROOT, "templates", "plugin");
const BIN_PATH = path.join(SDK_ROOT, "bin", "valuz-plugin.mjs");
const ID_RE = /^[a-z0-9][a-z0-9-]*\.[a-z0-9][a-z0-9-]*$/;

/** File names npm would drop or tooling would pick up are stored renamed. */
const RENAMES = { gitignore: ".gitignore" };

export class CreateError extends Error {}

/** Why ``id`` is not a usable plugin id, or null. */
export function checkPluginId(id) {
  if (!id) return "--id is required (<publisher>.<name>, e.g. acme.dashboard)";
  if (id.length > 100 || !ID_RE.test(id)) {
    return `invalid id "${id}": use <publisher>.<name> with lowercase letters, digits and '-', exactly one '.' (e.g. acme.dashboard)`;
  }
  const prefix = reservedPrefixes().find((p) => id.startsWith(p));
  if (prefix) return `invalid id "${id}": the prefix "${prefix}" is reserved`;
  return null;
}

function titleCase(text) {
  return text
    .split(/[-.]/)
    .filter(Boolean)
    .map((word) => word[0].toUpperCase() + word.slice(1))
    .join(" ");
}

function tryResolve(request) {
  try {
    return createRequire(path.join(SDK_ROOT, "package.json")).resolve(request);
  } catch {
    return null;
  }
}

/** tsconfig.json for editors / agents: the SDK and React types by absolute path. */
function tsconfigFor() {
  const src = path.join(SDK_ROOT, "src");
  const paths = {
    "@valuz/plugin-sdk": [path.join(src, "index.ts")],
    "@valuz/plugin-sdk/ui": [path.join(src, "ui", "index.ts")],
    "@valuz/plugin-sdk/testing": [path.join(src, "testing", "index.ts")],
    "@valuz/plugin-sdk/build": [path.join(src, "build", "index.ts")],
  };
  const reactTypes = tryResolve("@types/react/package.json");
  if (reactTypes) {
    const dir = path.dirname(reactTypes);
    paths.react = [path.join(dir, "index.d.ts")];
    paths["react/jsx-runtime"] = [path.join(dir, "jsx-runtime.d.ts")];
  }
  const reactDomTypes = tryResolve("@types/react-dom/package.json");
  if (reactDomTypes) {
    const dir = path.dirname(reactDomTypes);
    paths["react-dom"] = [path.join(dir, "index.d.ts")];
    paths["react-dom/client"] = [path.join(dir, "client.d.ts")];
  }
  const nodeTypes = tryResolve("@types/node/package.json");
  const compilerOptions = {
    target: "ES2022",
    lib: ["ES2022", "DOM", "DOM.Iterable"],
    module: "ESNext",
    moduleResolution: "bundler",
    jsx: "react-jsx",
    strict: true,
    noEmit: true,
    skipLibCheck: true,
    resolveJsonModule: true,
    isolatedModules: true,
    allowImportingTsExtensions: true,
    paths,
  };
  if (nodeTypes) compilerOptions.typeRoots = [path.dirname(path.dirname(nodeTypes))];
  const config = { compilerOptions, include: ["src", "test"] };
  // The SDK's UI subset is the host's component library (Vite source):
  // its ``import.meta.env`` / ``?url`` imports need Vite's client types.
  const vitePkg = tryResolve("vite/package.json");
  const viteClient = vitePkg && path.join(path.dirname(vitePkg), "client.d.ts");
  if (viteClient && existsSync(viteClient)) config.files = [viteClient];
  return config;
}

function render(text, values) {
  return text.replace(/__([A-Z_]+)__/g, (whole, name) => (name in values ? values[name] : whole));
}

function copyTemplates(fromDir, toDir, values, written) {
  for (const entry of readdirSync(fromDir, { withFileTypes: true })) {
    const from = path.join(fromDir, entry.name);
    if (entry.isDirectory()) {
      copyTemplates(from, path.join(toDir, entry.name), values, written);
      continue;
    }
    if (!entry.name.endsWith(".tmpl")) continue;
    const base = entry.name.slice(0, -".tmpl".length);
    const to = path.join(toDir, RENAMES[base] ?? base);
    mkdirSync(path.dirname(to), { recursive: true });
    writeFileSync(to, render(readFileSync(from, "utf8"), values));
    written.push(to);
  }
}

function writeJson(file, value, written) {
  writeFileSync(file, `${JSON.stringify(value, null, 2)}\n`);
  written.push(file);
}

/**
 * Create a plugin in ``dir`` (must not exist or be empty). Returns
 * ``{ dir, id, files }`` (absolute paths).
 */
export function createPlugin(dir, options) {
  const id = options?.id;
  const problem = checkPluginId(id);
  if (problem) throw new CreateError(problem);
  const root = path.resolve(dir);
  if (existsSync(root)) {
    if (!statSync(root).isDirectory()) throw new CreateError(`${dir} exists and is not a directory`);
    if (readdirSync(root).length > 0) throw new CreateError(`${dir} is not empty`);
  }
  mkdirSync(root, { recursive: true });

  const [publisher, shortName] = id.split(".");
  const name = options.name?.trim() || titleCase(shortName);
  const values = {
    PLUGIN_ID: id,
    PLUGIN_NAME: name,
    PUBLISHER: publisher,
    SDK_BIN: BIN_PATH,
    SDK_DIR: SDK_ROOT,
  };
  const files = [];

  writeJson(
    path.join(root, MANIFEST_FILE),
    {
      $schema: SCHEMA_PATH,
      manifestVersion: 1,
      id,
      version: "0.1.0",
      name: { "zh-CN": name, "en-US": name },
      description: {
        "zh-CN": `${name}：一个 Valuz 插件`,
        "en-US": `${name}: a Valuz plugin`,
      },
      publisher: { name: publisher },
      engines: { "valuz-plugin-api": "^1.0.0" },
      frontend: { entry: "frontend/index.js", styles: ["frontend/index.css"] },
      permissions: ["projects:read"],
      locales: "locales",
    },
    files,
  );
  writeJson(
    path.join(root, "package.json"),
    {
      name: id.replace(".", "-"),
      version: "0.1.0",
      private: true,
      type: "module",
      scripts: {
        build: "valuz-plugin build",
        dev: "valuz-plugin dev",
        test: "valuz-plugin test",
        validate: "valuz-plugin validate",
        pack: "valuz-plugin pack",
      },
      valuzPlugin: { source: "src/index.tsx" },
    },
    files,
  );
  writeJson(path.join(root, "tsconfig.json"), tsconfigFor(), files);
  copyTemplates(TEMPLATE_DIR, root, values, files);
  return { dir: root, id, files: files.sort() };
}
