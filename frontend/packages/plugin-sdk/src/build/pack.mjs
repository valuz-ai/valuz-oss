// ``packPlugin(dir)``: validate, then zip the package file set
// deterministically into ``<outDir>/<id>-<version>.zip`` (outDir defaults to
// ``<dir>/dist``). File set: see ``fileset.mjs``. Same sources → same bytes →
// same sha256 (sorted entries, fixed 1980-01-01 timestamps, mode 0644, no
// directory entries, no extra fields).
import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import path from "node:path";
import process from "node:process";

import { PackError, packFileSet } from "./fileset.mjs";
import { cleanRel } from "./manifest.mjs";
import { manifestPaths, validatePlugin } from "./validate.mjs";
import { createZip } from "./zip.mjs";

export { PackError, packFileSet };

/**
 * Validate and pack ``dir``. Resolves to ``{ path, sha256, size, id, version,
 * files, warnings }``; throws ``PackError`` (with ``validation``) when the
 * plugin is invalid or a manifest file would be left out of the package.
 */
export function packPlugin(dir, options = {}) {
  const root = path.resolve(dir);
  const validation = validatePlugin(root);
  if (!validation.ok) {
    throw new PackError(
      `invalid plugin: ${validation.errors.length} error(s): ${validation.errors.join("; ")}`,
      validation,
    );
  }
  const { manifest } = validation;
  const outDir = path.resolve(options.outDir ?? path.join(root, "dist"));
  const outPath = path.join(outDir, `${manifest.id}-${manifest.version}.zip`);

  const files = packFileSet(root, manifest, outPath);
  // Everything the manifest points at must ship (e.g. not frontend.entry under dist/).
  const missing = manifestPaths(manifest)
    .filter((ref) => ref.field !== "locales" && !files.includes(cleanRel(ref.value)))
    .map((ref) => `${ref.field}: "${ref.value}"`);
  if (missing.length) {
    throw new PackError(
      `the packed file set is incomplete (only frontend/, locales, automations/, the icon, README.md and LICENSE* are packed): ${missing.join(", ")}`,
      validation,
    );
  }

  const data = createZip(
    files.map((name) => ({ name, data: readFileSync(path.join(root, name)) })),
  );
  mkdirSync(outDir, { recursive: true });
  const tmp = path.join(outDir, `.pack-${process.pid}-${Date.now()}.zip`);
  writeFileSync(tmp, data, { mode: 0o644 });
  renameSync(tmp, outPath);

  return {
    path: outPath,
    sha256: createHash("sha256").update(data).digest("hex"),
    size: data.length,
    id: manifest.id,
    version: manifest.version,
    files,
    warnings: validation.warnings,
  };
}
