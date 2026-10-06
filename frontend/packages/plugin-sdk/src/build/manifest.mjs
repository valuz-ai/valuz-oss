// valuz-plugin.json helpers shared by build, validate, pack and the CLI.
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const MANIFEST_FILE = "valuz-plugin.json";
export const DEFAULT_FRONTEND_ENTRY = "frontend/index.js";
export const DEFAULT_LOCALES_DIR = "locales";
/** Plugin API version this SDK implements (doc 12 §9). */
export const PLUGIN_API_VERSION = "1.0.0";

// Paths are derived from this file's location (not ``new URL(rel,
// import.meta.url)``, which Vite rewrites into an asset URL when the module is
// loaded through Vite / Vitest).
const HERE = path.dirname(fileURLToPath(import.meta.url));

/** Absolute path of the SDK package root. */
export const SDK_ROOT = path.resolve(HERE, "..", "..");

/** Absolute path of the canonical manifest schema shipped with the SDK. */
export const SCHEMA_PATH = path.join(SDK_ROOT, "valuz-plugin.schema.json");

let schemaCache = null;

/** The parsed manifest schema. */
export function loadSchema() {
  if (!schemaCache) schemaCache = JSON.parse(readFileSync(SCHEMA_PATH, "utf8"));
  return schemaCache;
}

/** Reserved id prefixes, read from the schema's x-valuz-rules (cannot drift). */
export function reservedPrefixes() {
  const lead = "id must not start with a reserved prefix:";
  for (const rule of loadSchema()["x-valuz-rules"] ?? []) {
    if (typeof rule === "string" && rule.startsWith(lead)) {
      return rule
        .slice(lead.length)
        .split(",")
        .map((p) => p.trim())
        .filter(Boolean)
        .sort();
    }
  }
  return [];
}

export class ManifestError extends Error {
  constructor(message) {
    super(message);
    this.name = "ManifestError";
  }
}

/** Read and parse ``<dir>/valuz-plugin.json`` (throws ``ManifestError``). */
export function readManifest(dir) {
  const file = path.join(dir, MANIFEST_FILE);
  if (!existsSync(file)) {
    throw new ManifestError(`${MANIFEST_FILE} not found in ${dir}`);
  }
  let manifest;
  try {
    manifest = JSON.parse(readFileSync(file, "utf8"));
  } catch (error) {
    throw new ManifestError(`${MANIFEST_FILE}: invalid JSON: ${error.message}`);
  }
  if (!manifest || typeof manifest !== "object" || Array.isArray(manifest)) {
    throw new ManifestError(`${MANIFEST_FILE}: must be a JSON object`);
  }
  return manifest;
}

/** Write the manifest back (2-space JSON, trailing newline). */
export function writeManifest(dir, manifest) {
  writeFileSync(
    path.join(dir, MANIFEST_FILE),
    `${JSON.stringify(manifest, null, 2)}\n`,
  );
}

/** Whether a package-relative path has a ``..`` segment. */
export function hasDotDot(rel) {
  return String(rel)
    .replace(/\\/g, "/")
    .split("/")
    .some((segment) => segment === "..");
}

/** Normalize a package-relative path to a clean slash path. */
export function cleanRel(rel) {
  return path.posix.normalize(String(rel).replace(/\\/g, "/")).replace(/^\.\//, "");
}

/** ``frontend.entry`` of the manifest (default ``frontend/index.js``). */
export function frontendEntry(manifest) {
  const entry = manifest?.frontend?.entry;
  return typeof entry === "string" && entry ? cleanRel(entry) : DEFAULT_FRONTEND_ENTRY;
}

/** The manifest's locales directory (default ``locales``). */
export function localesDir(manifest) {
  const value = manifest?.locales;
  return typeof value === "string" && value ? cleanRel(value).replace(/\/$/, "") : DEFAULT_LOCALES_DIR;
}

/** A display string for a localizedText value. */
export function localizedText(value) {
  if (typeof value === "string") return value;
  if (value && typeof value === "object") {
    for (const lang of ["en-US", "en", "zh-CN", "zh"]) {
      if (typeof value[lang] === "string" && value[lang]) return value[lang];
    }
    for (const key of Object.keys(value).sort()) {
      if (typeof value[key] === "string" && value[key]) return value[key];
    }
  }
  return "";
}
