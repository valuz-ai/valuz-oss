// The package file set: what ``pack`` ships (cli/internal/pluginpkg/pack.go
// and the backend's archive.py ship the same set): valuz-plugin.json,
// frontend/, the manifest's locales directory (default locales/),
// automations/, the icon file, README.md and every top-level LICENSE* file.
// node_modules, .git and src directories (any depth) and dot-files /
// dot-directories (.env, .DS_Store, …) are never packed. Symlinked files are
// followed when they stay inside the plugin; symlinked directories are refused.
import { readdirSync, realpathSync, statSync } from "node:fs";
import path from "node:path";

import { MANIFEST_FILE, cleanRel, hasDotDot, localesDir } from "./manifest.mjs";

const SKIP_DIRS = new Set(["node_modules", ".git", "src"]);

export class PackError extends Error {
  constructor(message, validation) {
    super(message);
    this.name = "PackError";
    this.validation = validation ?? null;
  }
}

function within(root, target) {
  const rel = path.relative(root, target);
  return rel === "" || (rel !== ".." && !rel.startsWith(`..${path.sep}`) && !path.isAbsolute(rel));
}

function skipped(rel) {
  return cleanRel(rel)
    .split("/")
    .some((segment) => SKIP_DIRS.has(segment) || (segment.startsWith(".") && segment !== "."));
}

/** A regular file inside root (symlinks followed, must stay inside). */
function regularFile(root, rel) {
  const abs = path.join(root, rel);
  let info;
  try {
    info = statSync(abs);
  } catch {
    return false;
  }
  if (!info.isFile()) return false;
  if (!within(realpathSync(root), realpathSync(abs))) {
    throw new PackError(`${rel} is a symlink pointing outside the plugin directory`);
  }
  return true;
}

function walk(root, sub, exclude, add) {
  const start = path.join(root, sub);
  let info;
  try {
    info = statSync(start);
  } catch {
    return;
  }
  if (!info.isDirectory()) return;
  if (!within(realpathSync(root), realpathSync(start))) {
    throw new PackError(`${sub} is a symlink pointing outside the plugin directory`);
  }
  const visit = (dirAbs) => {
    for (const entry of readdirSync(dirAbs, { withFileTypes: true })) {
      const name = entry.name;
      const abs = path.join(dirAbs, name);
      const rel = cleanRel(path.relative(root, abs));
      if (name.startsWith(".")) continue;
      if (entry.isDirectory()) {
        if (!SKIP_DIRS.has(name)) visit(abs);
        continue;
      }
      if (exclude && path.resolve(abs) === path.resolve(exclude)) continue;
      if (entry.isSymbolicLink()) {
        let target;
        try {
          target = statSync(abs);
        } catch {
          throw new PackError(`${rel}: broken symlink`);
        }
        if (target.isDirectory()) {
          throw new PackError(`${rel}: symlinked directories are not packed; copy the files instead`);
        }
        if (!within(realpathSync(root), realpathSync(abs))) {
          throw new PackError(`${rel} is a symlink pointing outside the plugin directory`);
        }
      } else if (!entry.isFile()) {
        continue;
      }
      add(rel);
    }
  };
  visit(start);
}

/** The package files of ``dir`` (sorted slash paths). */
export function packFileSet(dir, manifest, exclude = null) {
  const root = path.resolve(dir);
  const set = new Set();
  const add = (rel) => set.add(cleanRel(rel));

  if (!regularFile(root, MANIFEST_FILE)) throw new PackError(`${MANIFEST_FILE} not found in ${root}`);
  add(MANIFEST_FILE);

  for (const name of readdirSync(root)) {
    if (name !== "README.md" && !name.startsWith("LICENSE")) continue;
    if (regularFile(root, name)) add(name);
  }

  if (typeof manifest.icon === "string" && manifest.icon && !hasDotDot(manifest.icon)) {
    const icon = cleanRel(manifest.icon);
    if (!skipped(icon) && regularFile(root, icon)) add(icon);
  }

  for (const sub of ["frontend", localesDir(manifest), "automations"]) {
    if (hasDotDot(sub) || skipped(sub)) continue;
    walk(root, cleanRel(sub), exclude, add);
  }
  return [...set].sort();
}
