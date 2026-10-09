#!/usr/bin/env node
/**
 * Valuz's dsh launcher — the upstream CLI (`@deepseek-ai/dsh` `runCli`), plus
 * the two things a Valuz-managed installation needs:
 *
 * 1. The closure's own pnpm as the package manager, exactly as the dsh desktop
 *    app's carrier does it (dsh `apps/desktop-host/src/cli.ts`). Without it,
 *    plugin installs need a `pnpm` on PATH, which a packaged app lacks.
 * 2. The managed profile. With VALUZ_DSH_MANAGED_PROFILE set, the profile
 *    under DSH_HOME is initialized/repaired before boot from
 *    `managed-profile.json`: Valuz's bundle layers first, every bundle the
 *    user selected the dsh way kept after them, the user's patch layer never
 *    touched. One implementation for every Valuz process (kernel session
 *    children and the resident manager host).
 *
 * Usage is the dsh CLI's own: `dsh.mjs --profile valuz …`,
 * `dsh.mjs plugin --profile valuz add <spec>`.
 */
import { createRequire } from "node:module";
import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { runCli } from "@deepseek-ai/dsh/lib/bin.js";

const require = createRequire(import.meta.url);
const here = dirname(fileURLToPath(import.meta.url));

const PROFILE_PATCH_TEMPLATE = `# Your patch layer for the Valuz-managed dsh profile, applied after every
# bundle layer (a top-level YAML array of loader patch entries).
[]
`;
const PROFILE_PNPM_WORKSPACE = `packages:
  - .

nodeLinker: hoisted
autoInstallPeers: false
`;

function writeAtomic(path, text) {
  mkdirSync(dirname(path), { recursive: true });
  const tmp = `${path}.${process.pid}.${Date.now()}.tmp`;
  writeFileSync(tmp, text);
  renameSync(tmp, path);
}

/** Managed layers first, user-selected bundles after them in their order. */
export function managedBundleList(managed, current) {
  const rest = (Array.isArray(current) ? current : []).filter((name) => !managed.includes(name));
  return [...new Set([...managed, ...rest])];
}

export function ensureManagedProfile(home, spec) {
  const dir = join(home, "profiles", spec.profile);
  mkdirSync(dir, { recursive: true });
  const manifestPath = join(dir, "package.json");
  let manifest;
  try {
    manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
    if (manifest === null || typeof manifest !== "object") throw new Error("not an object");
  } catch {
    manifest = { name: `dsh-profile-${spec.profile}`, private: true, dependencies: {} };
  }
  manifest.dsh ??= {};
  manifest.dsh.profile ??= {};
  const current = manifest.dsh.profile.bundles;
  const desired = managedBundleList(spec.bundles, current);
  if (!existsSync(manifestPath) || JSON.stringify(current) !== JSON.stringify(desired)) {
    manifest.dsh.profile.bundles = desired;
    writeAtomic(manifestPath, `${JSON.stringify(manifest, undefined, 2)}\n`);
  }
  const patchPath = join(dir, "cordis.patch.yml");
  if (!existsSync(patchPath)) writeAtomic(patchPath, PROFILE_PATCH_TEMPLATE);
  const workspacePath = join(dir, "pnpm-workspace.yaml");
  if (!existsSync(workspacePath)) writeAtomic(workspacePath, PROFILE_PNPM_WORKSPACE);
  return dir;
}

function bundledPnpm() {
  try {
    return join(dirname(require.resolve("pnpm/package.json")), "bin", "pnpm.mjs");
  } catch {
    return undefined;
  }
}

if (import.meta.main) {
  if (process.env.VALUZ_DSH_MANAGED_PROFILE) {
    const spec = JSON.parse(readFileSync(join(here, "..", "managed-profile.json"), "utf8"));
    if (process.env.VALUZ_DSH_MANAGED_PROFILE !== spec.profile) {
      throw new Error(
        `VALUZ_DSH_MANAGED_PROFILE=${process.env.VALUZ_DSH_MANAGED_PROFILE} does not name the managed profile ${spec.profile}`,
      );
    }
    ensureManagedProfile(process.env.DSH_HOME || join(homedir(), ".dsh"), spec);
  }
  const pnpm = bundledPnpm();
  await runCli(
    pnpm === undefined
      ? {}
      : {
          packageManager: {
            command: process.execPath,
            args: ["--expose-internals", pnpm],
            env: { ELECTRON_RUN_AS_NODE: "1" },
          },
        },
  );
}
