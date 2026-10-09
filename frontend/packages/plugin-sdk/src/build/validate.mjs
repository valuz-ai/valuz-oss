// ``validatePlugin(dir)``: the manifest schema, the schema's x-valuz-rules and
// the package checks (files exist, locales parse, engines covers this plugin
// API), plus two checks of the built frontend only the SDK can do: the entry
// has a default export and imports nothing at runtime.
//
// Result shape = the backend's / Go CLI's validate(): {ok, errors, warnings, manifest}.
// Rules and messages follow cli/internal/pluginpkg/validate.go.
import { existsSync, readFileSync, readdirSync, realpathSync, statSync } from "node:fs";
import path from "node:path";

import { packFileSet } from "./fileset.mjs";
import { bundleExports, checkBundleImports } from "./imports.mjs";
import {
  MANIFEST_FILE,
  PLUGIN_API_VERSION,
  cleanRel,
  hasDotDot,
  loadSchema,
  localesDir,
  reservedPrefixes,
} from "./manifest.mjs";
import { validateSchema } from "./schema.mjs";
import { rangeIncludes } from "./semver.mjs";

const LOCALE_FILE_RE = /^[a-z]{2}(-[A-Z]{2})?\.json$/;

function isObjectSchema(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value) && value.type === "object";
}

/** Manifest fields holding package-relative paths: ``[{ field, value }]``. */
export function manifestPaths(manifest) {
  const out = [];
  const frontend = manifest?.frontend;
  if (frontend && typeof frontend === "object") {
    if (typeof frontend.entry === "string") out.push({ field: "frontend.entry", value: frontend.entry });
    if (Array.isArray(frontend.styles)) {
      frontend.styles.forEach((style, i) => {
        if (typeof style === "string") out.push({ field: `frontend.styles[${i}]`, value: style });
      });
    }
  }
  if (Array.isArray(manifest?.automations)) {
    manifest.automations.forEach((automation, i) => {
      if (automation && typeof automation.entry === "string") {
        out.push({ field: `automations[${i}].entry`, value: automation.entry });
      }
    });
  }
  for (const key of ["icon", "locales"]) {
    if (typeof manifest?.[key] === "string") out.push({ field: key, value: manifest[key] });
  }
  return out;
}

function within(root, target) {
  const rel = path.relative(root, target);
  return rel === "" || (!rel.startsWith(`..${path.sep}`) && rel !== ".." && !path.isAbsolute(rel));
}

/** A regular file inside ``root``? Reports to ``errors`` and returns the absolute path or null. */
function checkFile(root, field, rel, errors) {
  if (path.isAbsolute(rel) || hasDotDot(rel)) {
    errors.push(`${field}: "${rel}" is not a path inside the package`);
    return null;
  }
  const abs = path.join(root, cleanRel(rel));
  let info;
  try {
    info = statSync(abs);
  } catch {
    errors.push(`${field}: "${rel}" does not exist in the package`);
    return null;
  }
  if (!info.isFile()) {
    errors.push(`${field}: "${rel}" is not a file`);
    return null;
  }
  return abs;
}

function readJson(file) {
  return JSON.parse(readFileSync(file, "utf8"));
}

function flattenKeys(tree, prefix = "", into = new Set()) {
  for (const [key, value] of Object.entries(tree ?? {})) {
    const full = prefix ? `${prefix}.${key}` : key;
    if (value && typeof value === "object" && !Array.isArray(value)) flattenKeys(value, full, into);
    else into.add(full);
  }
  return into;
}

function checkLocales(root, manifest, errors, warnings) {
  const dir = localesDir(manifest);
  if (hasDotDot(dir) || path.isAbsolute(dir)) return;
  const abs = path.join(root, dir);
  let entries;
  try {
    entries = readdirSync(abs, { withFileTypes: true });
  } catch {
    if ("locales" in manifest) warnings.push(`locales: "${dir}" does not exist in the package`);
    return;
  }
  const keySets = new Map();
  for (const entry of entries.sort((a, b) => (a.name < b.name ? -1 : 1))) {
    if (!entry.isFile() || !entry.name.endsWith(".json")) continue;
    const name = `${dir}/${entry.name}`;
    if (!LOCALE_FILE_RE.test(entry.name)) {
      warnings.push(`${name}: locale files are named <lang>.json (e.g. zh-CN.json, en-US.json)`);
    }
    let value;
    try {
      value = readJson(path.join(abs, entry.name));
    } catch (error) {
      errors.push(`${name}: invalid JSON: ${error.message}`);
      continue;
    }
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      errors.push(`${name}: must be a JSON object of message keys`);
      continue;
    }
    keySets.set(name, flattenKeys(value));
  }
  // Every locale should carry the same keys (doc 06 §2).
  const all = new Set();
  for (const keys of keySets.values()) for (const key of keys) all.add(key);
  for (const [name, keys] of keySets) {
    const missing = [...all].filter((key) => !keys.has(key)).sort();
    if (missing.length) {
      const shown = missing.slice(0, 10).join(", ");
      warnings.push(
        `${name}: missing ${missing.length} key${missing.length === 1 ? "" : "s"} other locales have (${shown}${missing.length > 10 ? ", …" : ""})`,
      );
    }
  }
}

function checkEngines(manifest, warnings) {
  const range = manifest?.engines?.["valuz-plugin-api"];
  if (typeof range !== "string" || !range.trim()) return;
  let included;
  try {
    included = rangeIncludes(range, PLUGIN_API_VERSION);
  } catch (error) {
    warnings.push(`engines.valuz-plugin-api: cannot parse "${range}" as a SemVer range: ${error.message}`);
    return;
  }
  if (!included) {
    warnings.push(
      `engines.valuz-plugin-api: "${range}" does not include plugin API ${PLUGIN_API_VERSION}; Valuz will mark the plugin incompatible`,
    );
  }
}

function checkSymlinkEscapes(root, manifest, errors) {
  let realRoot;
  try {
    realRoot = realpathSync(root);
  } catch {
    return;
  }
  for (const ref of manifestPaths(manifest)) {
    if (hasDotDot(ref.value) || path.isAbsolute(ref.value)) continue;
    let resolved;
    try {
      resolved = realpathSync(path.join(root, ref.value));
    } catch {
      continue; // missing files are reported by the existence checks
    }
    if (!within(realRoot, resolved)) {
      errors.push(`${ref.field}: "${ref.value}" resolves outside the package`);
    }
  }
}

/** Manifest files that exist but lie outside the file set ``pack`` ships (pack refuses them). */
function checkPackedSet(root, manifest, warnings) {
  let files;
  try {
    files = new Set(packFileSet(root, manifest));
  } catch {
    return; // symlink problems are reported by pack itself
  }
  for (const ref of manifestPaths(manifest)) {
    if (ref.field === "locales" || hasDotDot(ref.value) || path.isAbsolute(ref.value)) continue;
    const rel = cleanRel(ref.value);
    let info;
    try {
      info = statSync(path.join(root, rel));
    } catch {
      continue;
    }
    if (info.isFile() && !files.has(rel)) {
      warnings.push(
        `${ref.field}: "${ref.value}" is outside the packed file set (frontend/, the locales directory, automations/, the icon, README.md, LICENSE*); \`pack\` will refuse it`,
      );
    }
  }
}

/** Static checks of the built frontend entry. */
function checkEntryBundle(entryAbs, entryRel, manifest, errors, warnings) {
  let code;
  try {
    code = readFileSync(entryAbs, "utf8");
  } catch (error) {
    errors.push(`frontend.entry: cannot read "${entryRel}": ${error.message}`);
    return;
  }
  let disallowed;
  let exportsList;
  try {
    disallowed = checkBundleImports(code);
    exportsList = bundleExports(code);
  } catch (error) {
    const detail = error.errors?.[0]?.text ?? error.message;
    errors.push(`frontend.entry: "${entryRel}" is not a valid ES module: ${detail}`);
    return;
  }
  if (disallowed.length) {
    errors.push(
      `frontend.entry: "${entryRel}" imports ${disallowed.map((s) => `"${s}"`).join(", ")} at runtime; build it with "valuz-plugin build" so react, react-dom and @valuz/plugin-sdk come from the host and everything else is bundled`,
    );
  }
  if (!exportsList.includes("default")) {
    errors.push(`frontend.entry: "${entryRel}" has no default export; it must "export default definePlugin({ id, apply })"`);
  }
  if (typeof manifest.id === "string" && !code.includes(manifest.id)) {
    warnings.push(
      `frontend.entry: "${entryRel}" does not mention the id "${manifest.id}"; definePlugin({ id }) must equal the manifest id`,
    );
  }
}

/** Validate the plugin directory ``dir``: ``{ ok, errors, warnings, manifest }``. */
export function validatePlugin(dir) {
  const root = path.resolve(dir);
  const errors = [];
  const warnings = [];
  const finish = (manifest) => ({ ok: errors.length === 0, errors, warnings, manifest });

  if (!existsSync(root) || !statSync(root).isDirectory()) {
    errors.push(`${dir} is not a directory`);
    return finish(null);
  }
  const manifestFile = path.join(root, MANIFEST_FILE);
  if (!existsSync(manifestFile)) {
    errors.push(`${MANIFEST_FILE} not found at the package root`);
    return finish(null);
  }
  let manifest;
  try {
    manifest = readJson(manifestFile);
  } catch (error) {
    errors.push(`${MANIFEST_FILE}: invalid JSON: ${error.message}`);
    return finish(null);
  }
  if (!manifest || typeof manifest !== "object" || Array.isArray(manifest)) {
    errors.push(`${MANIFEST_FILE}: must be a JSON object`);
    return finish(null);
  }

  errors.push(...validateSchema(loadSchema(), manifest));

  // id must not start with a reserved prefix.
  if (typeof manifest.id === "string") {
    const prefix = reservedPrefixes().find((p) => manifest.id.startsWith(p));
    if (prefix) errors.push(`id: "${manifest.id}" uses the reserved prefix "${prefix}"`);
  }

  // backend (B-level) is not supported by plugin API 1.x.
  if ("backend" in manifest) {
    errors.push("backend: B-level plugins are not supported by plugin API 1.x");
  }

  // Relative paths: no '..' segment.
  for (const ref of manifestPaths(manifest)) {
    if (hasDotDot(ref.value)) errors.push(`${ref.field}: "${ref.value}" must not contain a '..' segment`);
  }

  // frontend.entry / styles exist; entry is an ES module with a default export
  // and no runtime imports.
  const frontend = manifest.frontend;
  if (frontend && typeof frontend === "object") {
    if (typeof frontend.entry === "string" && !hasDotDot(frontend.entry)) {
      const abs = checkFile(root, "frontend.entry", frontend.entry, errors);
      if (abs) {
        if (!/\.m?js$/i.test(frontend.entry)) {
          errors.push(`frontend.entry: "${frontend.entry}" must be an ES module (.js or .mjs)`);
        } else {
          checkEntryBundle(abs, frontend.entry, manifest, errors, warnings);
        }
      }
    }
    if (Array.isArray(frontend.styles)) {
      frontend.styles.forEach((style, i) => {
        if (typeof style === "string" && !hasDotDot(style)) {
          checkFile(root, `frontend.styles[${i}]`, style, errors);
        }
      });
    }
  }

  // automations: unique names; entries exist; input is an object schema.
  if (Array.isArray(manifest.automations)) {
    const seen = new Set();
    manifest.automations.forEach((automation, i) => {
      if (!automation || typeof automation !== "object") return;
      const field = `automations[${i}]`;
      if (typeof automation.name === "string") {
        if (seen.has(automation.name)) {
          errors.push(`${field}.name: "${automation.name}" is used by more than one automation`);
        }
        seen.add(automation.name);
      }
      if (typeof automation.entry === "string" && !hasDotDot(automation.entry)) {
        checkFile(root, `${field}.entry`, automation.entry, errors);
      }
      if ("input" in automation && !isObjectSchema(automation.input)) {
        errors.push(`${field}.input: must be a JSON Schema whose type is "object"`);
      }
    });
  }

  // config must be a JSON Schema whose type is object.
  if ("config" in manifest && !isObjectSchema(manifest.config)) {
    errors.push('config: must be a JSON Schema whose type is "object"');
  }

  // icon should exist (a missing icon falls back to the default).
  if (typeof manifest.icon === "string" && !hasDotDot(manifest.icon)) {
    const abs = path.join(root, cleanRel(manifest.icon));
    if (!existsSync(abs)) {
      warnings.push(`icon: "${manifest.icon}" does not exist in the package`);
    }
  }

  checkLocales(root, manifest, errors, warnings);
  checkEngines(manifest, warnings);
  checkSymlinkEscapes(root, manifest, errors);
  checkPackedSet(root, manifest, warnings);
  return finish(manifest);
}
