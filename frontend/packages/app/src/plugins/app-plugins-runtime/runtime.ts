import { useSyncExternalStore, type ComponentType } from "react";
import {
  ApiError,
  pluginHost,
  appPluginsApi,
  useRegistryStore,
  type PluginHost,
  type AppPluginList,
  type AppPlugin,
} from "@valuz/core";
import {
  adaptAppPlugin,
  type AdaptedPlugin,
  type HostServices,
} from "@valuz/plugin-sdk/host";
import type { PluginDefinition } from "@valuz/plugin-sdk";

import { whenAppPluginBootSettled } from "./boot-gate";
import { createCrashGuard, type CrashGuard } from "./crash-guard";
import {
  planAppPluginChanges,
  type LoadedPluginRecord,
  type AppPluginChanges,
} from "./plan";
import { AppPluginBridge } from "./bridge";
import { createAppPluginHostServices } from "./services";

export type AppPluginPhase =
  /** Being fetched, imported or applied. */
  | "loading"
  | "active"
  /** Import, ``apply`` or a contract violation failed; ``error`` says why. */
  | "failed"
  /** Not running by choice or by status: disabled, incompatible, safe mode … */
  | "skipped";

export interface AppPluginState {
  id: string;
  phase: AppPluginPhase;
  /** Why it is ``failed``. */
  error?: string;
  /** Why it is ``skipped``: a backend status or ``safe-mode`` / ``no-entry``. */
  reason?: string;
  version?: string;
  revision?: number;
}

export type AppPluginRuntimeStatus =
  /** Not started (the first-party boot has not settled, or not a local app). */
  | "idle"
  /** Waiting for the backend's list. */
  | "starting"
  | "running"
  /** The backend answered 403 / 404: a cloud deployment or no ``oss-app-plugins``. */
  | "unavailable"
  | "stopped";

export interface AppPluginRuntimeSnapshot {
  status: AppPluginRuntimeStatus;
  safeMode: boolean;
  safeModeReason: string | null;
  /** The backend's app-plugin generation this state reflects. */
  generation: number | null;
  plugins: Readonly<Record<string, AppPluginState>>;
}

export interface AppPluginRuntimeDeps {
  api: Pick<
    typeof appPluginsApi,
    "list" | "watch" | "getConfig" | "assetText" | "writeLog" | "setSafeMode"
  >;
  host: PluginHost;
  services: HostServices;
  /** Evaluate module source text: Blob URL + ``import()`` in the app. */
  importModule: (source: string) => Promise<unknown>;
  styles: { inject(pluginId: string, css: string): void; remove(pluginId: string): void };
  guard: CrashGuard;
  waitForBoot: (signal: AbortSignal) => Promise<void>;
  sleep: (ms: number, signal: AbortSignal) => Promise<void>;
  /** Mount the navigate / confirm bridge; returns its disposer. */
  mountBridge?: () => () => void;
  retryBaseMs?: number;
  retryMaxMs?: number;
}

const SAFE_MODE_REASON =
  "The renderer stopped twice in a row while app plugins were loading.";

const isUnavailable = (error: unknown): boolean =>
  error instanceof ApiError && (error.status === 403 || error.status === 404);

const messageOf = (error: unknown): string =>
  error instanceof Error ? error.message : String(error);

/**
 * Loads the backend's app plugins into the plugin host and keeps them
 * in step with it (doc 04 §4.2, §6): list → fetch → ``import()`` → adapt →
 * ``pluginHost.load``; a long poll on ``watch`` re-lists on every change and
 * loads, unloads or reloads exactly what moved. One failing plugin never
 * affects another, nor the app.
 */
export class AppPluginRuntime {
  private snapshot: AppPluginRuntimeSnapshot = {
    status: "idle",
    safeMode: false,
    safeModeReason: null,
    generation: null,
    plugins: {},
  };
  private readonly listeners = new Set<() => void>();
  private readonly loaded = new Map<
    string,
    LoadedPluginRecord & { adapted: AdaptedPlugin }
  >();
  private abort: AbortController | null = null;
  private running: Promise<void> | null = null;
  private queue: Promise<void> = Promise.resolve();
  private disposeBridge: (() => void) | null = null;

  private readonly deps: AppPluginRuntimeDeps;

  // An explicit field, not a parameter property: the webui's tsconfig only
  // allows syntax that can be erased.
  constructor(deps: AppPluginRuntimeDeps) {
    this.deps = deps;
  }

  // ── store ───────────────────────────────────────────────────────────────

  readonly subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  readonly getSnapshot = (): AppPluginRuntimeSnapshot => this.snapshot;

  private set(patch: Partial<AppPluginRuntimeSnapshot>): void {
    this.snapshot = { ...this.snapshot, ...patch };
    for (const listener of [...this.listeners]) listener();
  }

  private patchPlugin(id: string, state: AppPluginState | null): void {
    const plugins = { ...this.snapshot.plugins };
    if (state) plugins[id] = state;
    else delete plugins[id];
    this.set({ plugins });
  }

  // ── lifecycle ───────────────────────────────────────────────────────────

  /** Start once the first-party boot settled; resolves when stopped. Idempotent. */
  start(): Promise<void> {
    if (this.running) return this.running;
    const abort = new AbortController();
    this.abort = abort;
    this.set({ status: "starting" });
    this.running = this.run(abort.signal).finally(() => {
      if (this.abort === abort) this.abort = null;
      this.running = null;
    });
    return this.running;
  }

  /** Stop watching and unload every app plugin. */
  async stop(): Promise<void> {
    this.abort?.abort();
    const running = this.running;
    await running?.catch(() => undefined);
    await this.enqueue(async () => {
      for (const id of [...this.loaded.keys()]) await this.unloadOne(id);
    });
    this.deps.guard.markHealthy();
    this.disposeBridge?.();
    this.disposeBridge = null;
    this.set({ status: "stopped", generation: null, plugins: {} });
  }

  /** Re-read the list now and apply it (the Plugins page after an action). */
  async refresh(): Promise<void> {
    try {
      await this.applyList(await this.deps.api.list());
    } catch (error) {
      console.warn("[app-plugins] refresh failed", error);
    }
  }

  /** Re-read the configuration of one plugin (or all) and push it to the plugin. */
  async refreshConfig(pluginId?: string): Promise<void> {
    const ids = pluginId ? [pluginId] : [...this.loaded.keys()];
    await Promise.all(
      ids.map(async (id) => {
        const entry = this.loaded.get(id);
        if (!entry) return;
        try {
          const { values } = await this.deps.api.getConfig(id);
          entry.adapted.scope.config.set(values ?? {});
        } catch {
          // keep the current values
        }
      }),
    );
  }

  // ── the loop ────────────────────────────────────────────────────────────

  private async run(signal: AbortSignal): Promise<void> {
    await this.deps.waitForBoot(signal);
    if (signal.aborted) return;

    const verdict = this.deps.guard.evaluateStartup();

    let list: AppPluginList | null = null;
    let delay = this.deps.retryBaseMs ?? 1500;
    while (!signal.aborted && !list) {
      try {
        list = await this.deps.api.list();
      } catch (error) {
        if (isUnavailable(error)) {
          this.set({ status: "unavailable" });
          return;
        }
        // The backend may still be starting: ask again.
        await this.deps.sleep(delay, signal);
        delay = Math.min(delay * 1.5, this.deps.retryMaxMs ?? 15_000);
      }
    }
    if (!list || signal.aborted) return;

    if (verdict.safeMode && !list.safe_mode) {
      try {
        await this.deps.api.setSafeMode(true, SAFE_MODE_REASON);
        list = { ...list, safe_mode: true, safe_mode_reason: SAFE_MODE_REASON };
      } catch (error) {
        console.error("[app-plugins] could not enter safe mode", error);
      }
    }
    this.set({ status: "running" });
    await this.applyList(list);

    let backoff = this.deps.retryBaseMs ?? 1500;
    while (!signal.aborted) {
      try {
        const generation = this.snapshot.generation ?? list.generation;
        const next = await this.deps.api.watch(generation, 25, signal);
        if (signal.aborted) return;
        backoff = this.deps.retryBaseMs ?? 1500;
        if (next.generation !== generation) {
          await this.applyList(await this.deps.api.list());
        }
      } catch (error) {
        if (signal.aborted) return;
        if (isUnavailable(error)) {
          this.set({ status: "unavailable" });
          return;
        }
        await this.deps.sleep(backoff, signal);
        backoff = Math.min(backoff * 1.5, this.deps.retryMaxMs ?? 15_000);
      }
    }
  }

  // ── applying a list ─────────────────────────────────────────────────────

  private enqueue(task: () => Promise<void>): Promise<void> {
    const next = this.queue.then(task, task);
    this.queue = next.catch(() => undefined);
    return next;
  }

  /** Bring the loaded plugins in line with ``list``. Never throws. */
  applyList(list: AppPluginList): Promise<void> {
    return this.enqueue(async () => {
      this.set({
        generation: list.generation,
        safeMode: list.safe_mode === true,
        safeModeReason: list.safe_mode_reason ?? null,
      });
      const changes = planAppPluginChanges(this.loaded, list);
      await this.applyChanges(changes);
      await this.refreshConfig();
    });
  }

  private async applyChanges(changes: AppPluginChanges): Promise<void> {
    for (const id of changes.unload) await this.unloadOne(id);
    for (const { id, reason } of changes.skipped) {
      this.patchPlugin(id, { id, phase: "skipped", reason });
    }
    // A plugin that left the list altogether has no state to show.
    const known = new Set([
      ...changes.skipped.map((entry) => entry.id),
      ...changes.load.map((plugin) => plugin.id),
      ...changes.reload.map((plugin) => plugin.id),
    ]);
    for (const id of Object.keys(this.snapshot.plugins)) {
      if (!known.has(id) && !this.loaded.has(id)) this.patchPlugin(id, null);
    }

    const toLoad = [...changes.reload, ...changes.load];
    if (toLoad.length === 0) return;

    this.deps.guard.markLoading();
    this.disposeBridge ??= this.deps.mountBridge?.() ?? null;
    for (const plugin of changes.reload) await this.unloadOne(plugin.id);
    for (const plugin of toLoad) await this.loadOne(plugin);
    this.deps.guard.scheduleHealthy();
  }

  private async unloadOne(id: string): Promise<void> {
    try {
      await this.deps.host.unload(id);
    } catch (error) {
      console.error(`[app-plugins] unloading "${id}" failed`, error);
    }
    this.deps.styles.remove(id);
    this.loaded.delete(id);
    this.patchPlugin(id, null);
  }

  private async loadOne(plugin: AppPlugin): Promise<void> {
    const { id } = plugin;
    this.patchPlugin(id, {
      id,
      phase: "loading",
      version: plugin.version,
      revision: plugin.revision,
    });
    try {
      const [config, source, styles] = await Promise.all([
        this.deps.api
          .getConfig(id)
          .then((response) => response.values ?? {})
          .catch((): Record<string, unknown> => ({})),
        this.deps.api.assetText(plugin.entry_url!),
        Promise.all(plugin.style_urls.map((url) => this.deps.api.assetText(url))),
      ]);

      const module = (await this.deps.importModule(source)) as {
        default?: unknown;
      } | null;
      const adapted = adaptAppPlugin(
        module?.default as PluginDefinition,
        plugin,
        this.deps.services,
        { config },
      );

      for (const css of styles) this.deps.styles.inject(id, css);
      const record = await this.deps.host.load(adapted);
      if (record.status !== "active") {
        throw record.error ?? new Error(`plugin host left it ${record.status}`);
      }
      this.loaded.set(id, {
        id,
        version: plugin.version,
        revision: plugin.revision,
        sha256: plugin.sha256 ?? null,
        adapted,
      });
      this.patchPlugin(id, {
        id,
        phase: "active",
        version: plugin.version,
        revision: plugin.revision,
      });
    } catch (error) {
      this.deps.styles.remove(id);
      const message = messageOf(error);
      console.error(`[app-plugins] plugin "${id}" failed to load`, error);
      this.patchPlugin(id, {
        id,
        phase: "failed",
        error: message,
        version: plugin.version,
        revision: plugin.revision,
      });
      void this.deps.api
        .writeLog(id, "error", `failed to load: ${message}`)
        .catch(() => undefined);
    }
  }
}

// ── defaults for the app ────────────────────────────────────────────────────

/** Evaluate ``source`` as an ES module without a file: Blob URL + ``import()``. */
export async function importModuleFromText(source: string): Promise<unknown> {
  const url = URL.createObjectURL(
    new Blob([source], { type: "text/javascript" }),
  );
  try {
    return await import(/* @vite-ignore */ url);
  } finally {
    URL.revokeObjectURL(url);
  }
}

/** ``<style data-valuz-app-plugin="<id>">`` elements in ``document.head``. */
export const documentStyles: AppPluginRuntimeDeps["styles"] = {
  inject(pluginId, css) {
    const element = document.createElement("style");
    element.setAttribute("data-valuz-app-plugin", pluginId);
    element.textContent = css;
    document.head.appendChild(element);
  },
  remove(pluginId) {
    for (const element of document.head.querySelectorAll("style[data-valuz-app-plugin]")) {
      if (element.getAttribute("data-valuz-app-plugin") === pluginId) element.remove();
    }
  },
};

export const abortableSleep = (ms: number, signal: AbortSignal): Promise<void> =>
  new Promise((resolve) => {
    if (signal.aborted) return resolve();
    const timer = setTimeout(done, ms);
    function done() {
      clearTimeout(timer);
      signal.removeEventListener("abort", done);
      resolve();
    }
    signal.addEventListener("abort", done, { once: true });
  });

let instance: AppPluginRuntime | null = null;

/** The app's runtime (one per window), created on first use. */
export function getAppPluginRuntime(): AppPluginRuntime {
  instance ??= new AppPluginRuntime({
    api: appPluginsApi,
    host: pluginHost,
    services: createAppPluginHostServices(),
    importModule: importModuleFromText,
    styles: documentStyles,
    guard: createCrashGuard(
      typeof localStorage === "undefined" ? null : localStorage,
      { target: typeof window === "undefined" ? null : window },
    ),
    waitForBoot: whenAppPluginBootSettled,
    sleep: abortableSleep,
    // Router access and the confirm dialog, mounted only while plugins run.
    mountBridge: () =>
      useRegistryStore.getState().registerSlot("shell.overlay", {
        id: "oss-app-plugins:bridge",
        component: AppPluginBridge as ComponentType<Record<string, unknown>>,
        priority: 900,
      }),
  });
  return instance;
}

/** Test seam: replace (or clear) the app's runtime. */
export function setAppPluginRuntimeForTests(runtime: AppPluginRuntime | null): void {
  instance = runtime;
}

/**
 * The renderer's app-plugin state: runtime status, safe mode, the backend
 * generation it reflects, and per plugin whether it is loading, active,
 * failed (with the error) or skipped (with the reason).
 */
export function useAppPluginRuntime(): AppPluginRuntimeSnapshot {
  const runtime = getAppPluginRuntime();
  return useSyncExternalStore(
    runtime.subscribe,
    runtime.getSnapshot,
    runtime.getSnapshot,
  );
}
