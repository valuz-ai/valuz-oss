import {
  ApiError,
  extensionsApi,
  pluginHost,
  type PluginHost,
} from "@valuz/core";

import { ossPluginSpecs, type OssPluginSpec } from "./specs";

/**
 * A required OSS plugin failed to load. Hosts turn it into their boot failure
 * page: an app without ``oss-core`` / ``oss-agents`` has no usable surface, and
 * mounting it would look like a working app that silently lacks everything.
 */
export class RequiredOssPluginError extends Error {
  readonly pluginId: string;
  readonly cause: unknown;

  // Explicit fields, not parameter properties: the webui's tsconfig only
  // allows syntax that can be erased.
  constructor(pluginId: string, cause: unknown) {
    super(
      `required plugin "${pluginId}" failed to load: ${
        cause instanceof Error ? cause.message : String(cause)
      }`,
    );
    this.name = "RequiredOssPluginError";
    this.pluginId = pluginId;
    this.cause = cause;
  }
}

/**
 * What a read of ``GET /v1/extensions/backend/state`` came to.
 *
 * - ``known`` — the backend answered; ``inactive`` are its plugins that are off.
 * - ``unreachable`` — no answer yet (connection refused, timeout): the backend
 *   may simply still be starting, so asking again later can succeed.
 * - ``unsupported`` — it answered, but not with the state: an older backend
 *   (404), an auth wall (401 / 403), a body that is not the contract. Asking
 *   again will not change that.
 */
export type BackendStateRead =
  | { kind: "known"; inactive: string[] }
  | { kind: "unreachable" }
  | { kind: "unsupported" };

export async function readBackendState(): Promise<BackendStateRead> {
  try {
    const state = await extensionsApi.backendState();
    if (!Array.isArray(state?.inactive)) return { kind: "unsupported" };
    return {
      kind: "known",
      inactive: state.inactive.filter(
        (id): id is string => typeof id === "string",
      ),
    };
  } catch (error) {
    return error instanceof ApiError
      ? { kind: "unsupported" }
      : { kind: "unreachable" };
  }
}

/**
 * The backend plugins that are not running, or ``null`` when that cannot be
 * known. "Not known" means "load everything", which is what the app did before
 * this coupling existed.
 */
export async function readInactiveBackendPlugins(): Promise<string[] | null> {
  const read = await readBackendState();
  return read.kind === "known" ? read.inactive : null;
}

export interface LoadOssPluginsOptions {
  /**
   * Backend plugin ids known to be off. Omit to read them from the backend
   * (``GET /v1/extensions/backend/state``).
   */
  inactive?: readonly string[];
  /** The plugins to load; the OSS set by default. */
  specs?: readonly OssPluginSpec[];
}

export interface LoadOssPluginsResult {
  /** What the backend reported inactive (empty when it could not be read). */
  inactive: string[];
  /**
   * Whether the inactive list is real — given by the caller or read from the
   * backend. ``false`` means everything loaded because nothing could be
   * learned; see {@link settleOssPlugins}.
   */
  stateKnown: boolean;
  loaded: string[];
  /** Optional plugins left unloaded because their backend counterpart is off. */
  skipped: string[];
  /** Optional plugins whose ``apply`` threw; the rest still loaded. */
  failed: string[];
}

/**
 * Load the OSS plugins into ``host``, in order, BEFORE any overlay plugin.
 *
 * - An optional plugin whose backend counterpart (same id) is inactive is not
 *   loaded; the host records it as ``backend-disabled`` so the extensions list
 *   still shows it and why.
 * - An optional plugin that throws is recorded ``failed`` (its partial
 *   contributions rolled back) and the rest load.
 * - A required plugin that throws rejects with {@link RequiredOssPluginError}.
 * - Idempotent: a plugin already loaded is left alone.
 */
export async function loadOssPlugins(
  host: PluginHost = pluginHost,
  options: LoadOssPluginsOptions = {},
): Promise<LoadOssPluginsResult> {
  const specs = options.specs ?? ossPluginSpecs;
  const given = options.inactive;
  const read = given ? null : await readInactiveBackendPlugins();
  const inactive = [...(given ?? read ?? [])];
  const off = new Set(inactive);
  const result: LoadOssPluginsResult = {
    inactive,
    stateKnown: given !== undefined || read !== null,
    loaded: [],
    skipped: [],
    failed: [],
  };

  for (const { plugin, required } of specs) {
    if (!required && off.has(plugin.id)) {
      host.skip(plugin.id, "backend-disabled");
      result.skipped.push(plugin.id);
      continue;
    }
    const known = host.get(plugin.id);
    if (known?.status === "active" || known?.status === "loading") {
      result.loaded.push(plugin.id);
      continue;
    }
    const record = await host.load(plugin);
    if (record.status === "failed") {
      if (required) throw new RequiredOssPluginError(plugin.id, record.error);
      result.failed.push(plugin.id);
    } else {
      result.loaded.push(plugin.id);
    }
  }
  return result;
}

/**
 * Bring the loaded OSS plugins in line with what the backend reports NOW.
 *
 * The first read happens while the app boots, and on the desktop the local
 * backend is usually still starting then — the read fails and everything loads.
 * This applies the state once it is known: optional plugins whose backend
 * counterpart is inactive are unloaded (their pages, settings and sidebar
 * entries go), and ones that are active again are loaded. Required plugins are
 * never touched. Returns whether anything moved.
 */
export async function reconcileOssPlugins(
  host: PluginHost = pluginHost,
  options: LoadOssPluginsOptions = {},
): Promise<boolean> {
  const specs = options.specs ?? ossPluginSpecs;
  const inactive = options.inactive ?? (await readInactiveBackendPlugins());
  if (inactive === null) return false;
  const off = new Set(inactive);
  let changed = false;

  for (const { plugin, required } of specs) {
    if (required) continue;
    const status = host.get(plugin.id)?.status;
    if (off.has(plugin.id)) {
      if (status === "backend-disabled") continue;
      if (status === "active") await host.unload(plugin.id);
      host.skip(plugin.id, "backend-disabled");
      changed = true;
    } else if (status === undefined || status === "backend-disabled") {
      await host.load(plugin);
      changed = true;
    }
  }
  return changed;
}

export interface SettleOssPluginsOptions extends LoadOssPluginsOptions {
  /** Pause between reads while the backend is unreachable. Default 1500 ms. */
  intervalMs?: number;
  /** Stop asking after this long. Default 120 000 ms. */
  timeoutMs?: number;
  /** Stops the loop (a host unmounting, a test). */
  signal?: AbortSignal;
}

/**
 * Keep asking the backend which of its plugins are off until it answers, then
 * {@link reconcileOssPlugins}. For hosts whose backend starts alongside the
 * UI (the desktop): the boot-time read found nothing to read, so the OSS
 * plugins all loaded; this applies the real state as soon as there is one.
 *
 * Gives up quietly — everything stays loaded — when the backend answers but
 * cannot say (older backend, auth wall) or never answers within ``timeoutMs``.
 * Resolves with whether the state was applied.
 */
export async function settleOssPlugins(
  host: PluginHost = pluginHost,
  options: SettleOssPluginsOptions = {},
): Promise<boolean> {
  const { intervalMs = 1500, timeoutMs = 120_000, signal } = options;
  const deadline = Date.now() + timeoutMs;

  for (;;) {
    if (signal?.aborted) return false;
    const read = await readBackendState();
    if (read.kind === "known") {
      await reconcileOssPlugins(host, { ...options, inactive: read.inactive });
      return true;
    }
    if (read.kind === "unsupported") return false;
    if (Date.now() + intervalMs > deadline) return false;
    await new Promise<void>((resolve) => setTimeout(resolve, intervalMs));
  }
}
