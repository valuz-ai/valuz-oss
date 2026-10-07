import {
  ApiError,
  createPluginHost,
  personalProfile,
  useRegistryStore,
  type PluginHost,
  type AppPluginList,
  type AppPlugin,
} from "@valuz/core";
import { definePlugin, type PluginContext } from "@valuz/plugin-sdk";
import type { HostServices } from "@valuz/plugin-sdk/host";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createCrashGuard, LOADING_MARKER_KEY } from "./crash-guard";
import { AppPluginRuntime, type AppPluginRuntimeDeps } from "./runtime";

const item = (id: string, patch: Partial<AppPlugin> = {}): AppPlugin => ({
  id,
  version: "1.0.0",
  name: id,
  publisher: { name: "Acme" },
  source: { kind: "file" },
  status: "enabled",
  status_reason: null,
  enabled: true,
  permissions: [],
  requires: [],
  unmet_requires: [],
  engines: {},
  entry_url: `/v1/app-plugin-assets/${id}/1/frontend/index.js`,
  style_urls: [`/v1/app-plugin-assets/${id}/1/frontend/index.css`],
  locales: {},
  config_schema: null,
  automations: [],
  revision: 1,
  dev_path: null,
  sha256: "abc",
  installed_at: 1,
  ...patch,
});

const listOf = (generation: number, plugins: AppPlugin[], extra: Partial<AppPluginList> = {}): AppPluginList => ({
  api_version: "1.0.0",
  safe_mode: false,
  safe_mode_reason: null,
  generation,
  plugins,
  ...extra,
});

const Noop = () => null;
const memoryStorage = () => {
  const data = new Map<string, string>();
  return {
    data,
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => void data.set(key, value),
    removeItem: (key: string) => void data.delete(key),
  };
};

type Behaviour = "ok" | "throws" | "bad-id" | "contract" | "no-default" | "import-fails";

interface Harness {
  runtime: AppPluginRuntime;
  host: PluginHost;
  api: {
    list: ReturnType<typeof vi.fn>;
    watch: ReturnType<typeof vi.fn>;
    getConfig: ReturnType<typeof vi.fn>;
    assetText: ReturnType<typeof vi.fn>;
    writeLog: ReturnType<typeof vi.fn>;
    setSafeMode: ReturnType<typeof vi.fn>;
  };
  styles: { injected: string[]; removed: string[] };
  storage: ReturnType<typeof memoryStorage>;
  behaviours: Map<string, Behaviour>;
  contexts: Map<string, PluginContext>;
  mountBridge: ReturnType<typeof vi.fn>;
  disposeBridge: ReturnType<typeof vi.fn>;
  /** Make the long poll return ``generation``. */
  bump(generation: number): void;
  slotIds(): string[];
}

function harness(initial: AppPluginList, options: { storage?: ReturnType<typeof memoryStorage> } = {}): Harness {
  const host = createPluginHost();
  const behaviours = new Map<string, Behaviour>();
  const contexts = new Map<string, PluginContext>();
  const styles = { injected: [] as string[], removed: [] as string[] };
  const storage = options.storage ?? memoryStorage();
  let pending: { resolve: (value: { generation: number }) => void } | null = null;
  const initialList = initial;

  const api = {
    list: vi.fn(async () => initialList),
    watch: vi.fn(
      (_since: number, _timeout: number, signal?: AbortSignal) =>
        new Promise<{ generation: number }>((resolve, reject) => {
          pending = { resolve };
          signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
        }),
    ),
    getConfig: vi.fn(async () => ({ values: {}, schema: null })),
    assetText: vi.fn(async (url: string) => url),
    writeLog: vi.fn(async () => undefined),
    setSafeMode: vi.fn(async () => ({ safe_mode: true })),
  };
  const mountBridge = vi.fn();
  const disposeBridge = vi.fn();
  mountBridge.mockReturnValue(disposeBridge);

  const services = {
    request: async () => ({}),
    navigate: () => {},
    getLocale: () => "en-US",
    subscribeLocale: () => () => {},
    getHostContext: () => ({
      locale: "en-US",
      theme: "light",
      edition: "personal",
      deployment: "local",
      orgId: null,
    }),
  } as unknown as HostServices;

  const deps: AppPluginRuntimeDeps = {
    api: api as unknown as AppPluginRuntimeDeps["api"],
    host,
    services,
    importModule: async (source) => {
      // ``assetText`` returned the asset URL: ``/v1/app-plugin-assets/<id>/…``.
      const id = source.split("/")[3] ?? "";
      const behaviour = behaviours.get(id) ?? "ok";
      if (behaviour === "import-fails") throw new SyntaxError("Unexpected token");
      if (behaviour === "no-default") return {};
      return {
        default: definePlugin({
          id: behaviour === "bad-id" ? "someone.else" : id,
          apply(ctx) {
            contexts.set(id, ctx);
            ctx.registry.slot("shell.topbar.actions", { id: "button", component: Noop });
            if (behaviour === "contract") {
              ctx.registry.slot("shell.brand.mark" as "shell.topbar.actions", { id: "x", component: Noop });
            }
            if (behaviour === "throws") throw new Error("boom");
          },
        }),
      };
    },
    styles: {
      inject: (id) => void styles.injected.push(id),
      remove: (id) => void styles.removed.push(id),
    },
    guard: createCrashGuard(storage),
    waitForBoot: async () => undefined,
    sleep: async () => undefined,
    mountBridge: mountBridge as unknown as () => () => void,
  };
  const runtime = new AppPluginRuntime(deps);

  return {
    runtime,
    host,
    api,
    styles,
    storage,
    behaviours,
    contexts,
    mountBridge,
    disposeBridge,
    bump(generation) {
      pending?.resolve({ generation });
    },
    slotIds: () => (useRegistryStore.getState().slots["shell.topbar.actions"] ?? []).map((entry) => entry.id),
  };
}

const quiet = () => {
  vi.spyOn(console, "error").mockImplementation(() => {});
  vi.spyOn(console, "warn").mockImplementation(() => {});
};

let current: Harness | null = null;

beforeEach(() => {
  const store = useRegistryStore.getState();
  store.clearLayers();
  store.hydrate(personalProfile);
});

afterEach(async () => {
  await current?.runtime.stop();
  current = null;
  vi.restoreAllMocks();
});

const start = async (h: Harness) => {
  current = h;
  void h.runtime.start();
  await vi.waitFor(() => expect(h.runtime.getSnapshot().status).toBe("running"));
  await vi.waitFor(() => expect(h.api.watch).toHaveBeenCalled());
};

describe("start", () => {
  it("loads the enabled plugins into the host through the restricted context", async () => {
    const h = harness(
      listOf(3, [item("a.one"), item("a.two", { enabled: false, status: "disabled" }), item("a.three", { status: "requires-unmet" })]),
    );
    await start(h);

    expect(h.host.get("a.one")?.status).toBe("active");
    expect(h.host.get("a.two")).toBeUndefined();
    expect(h.slotIds()).toEqual(["x:a.one:button"]);
    expect(h.styles.injected).toEqual(["a.one"]);
    expect(h.mountBridge).toHaveBeenCalledTimes(1);
    expect(h.runtime.getSnapshot()).toMatchObject({
      status: "running",
      generation: 3,
      safeMode: false,
      plugins: {
        "a.one": { phase: "active", version: "1.0.0", revision: 1 },
        "a.two": { phase: "skipped", reason: "disabled" },
        "a.three": { phase: "skipped", reason: "requires-unmet" },
      },
    });
    // The marker is set while loading and cleared ~10s later.
    expect(h.storage.data.has(LOADING_MARKER_KEY)).toBe(true);
  });

  it("loads nothing and mounts no bridge when there is nothing to load", async () => {
    const h = harness(listOf(1, []));
    await start(h);
    expect(h.mountBridge).not.toHaveBeenCalled();
    expect(h.storage.data.has(LOADING_MARKER_KEY)).toBe(false);
  });

  it("keeps asking while the backend is unreachable", async () => {
    const h = harness(listOf(1, [item("a.one")]));
    h.api.list
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockRejectedValueOnce(new TypeError("Failed to fetch"));
    h.api.list.mockImplementation(async () => listOf(1, [item("a.one")]));
    await start(h);

    expect(h.api.list.mock.calls.length).toBeGreaterThanOrEqual(3);
    expect(h.host.get("a.one")?.status).toBe("active");
  });

  it.each([403, 404])("goes quiet when the backend answers %s (cloud / no oss-app-plugins)", async (status) => {
    const h = harness(listOf(1, []));
    h.api.list.mockRejectedValue(new ApiError("nope", status));
    current = h;
    await h.runtime.start();

    expect(h.runtime.getSnapshot().status).toBe("unavailable");
    expect(h.api.watch).not.toHaveBeenCalled();
    expect(h.api.list).toHaveBeenCalledTimes(1);
  });

  it("does not load in safe mode", async () => {
    const h = harness(listOf(5, [item("a.one")], { safe_mode: true, safe_mode_reason: "manual" }));
    await start(h);

    expect(h.host.get("a.one")).toBeUndefined();
    expect(h.runtime.getSnapshot()).toMatchObject({
      safeMode: true,
      safeModeReason: "manual",
      plugins: { "a.one": { phase: "skipped", reason: "safe-mode" } },
    });
  });

  it("enters safe mode after the loading marker survived two starts in a row", async () => {
    const storage = memoryStorage();
    storage.data.set(LOADING_MARKER_KEY, "1");
    storage.data.set("valuz.appPlugins.crashes", "1");
    const h = harness(listOf(5, [item("a.one")]), { storage });
    await start(h);

    expect(h.api.setSafeMode).toHaveBeenCalledWith(true, expect.stringContaining("twice in a row"));
    expect(h.host.get("a.one")).toBeUndefined();
    expect(h.runtime.getSnapshot().safeMode).toBe(true);
  });
});

describe("failures are isolated", () => {
  it.each<[Behaviour, RegExp]>([
    ["throws", /boom/],
    ["contract", /not a public slot/],
    ["bad-id", /does not match the manifest id/],
    ["no-default", /default-export/],
    ["import-fails", /Unexpected token/],
  ])("%s: only that plugin fails, and it is logged", async (behaviour, message) => {
    quiet();
    const h = harness(listOf(1, [item("a.bad"), item("a.good")]));
    h.behaviours.set("a.bad", behaviour);
    await start(h);

    expect(h.host.get("a.good")?.status).toBe("active");
    expect(h.slotIds()).toEqual(["x:a.good:button"]);
    const bad = h.runtime.getSnapshot().plugins["a.bad"];
    expect(bad?.phase).toBe("failed");
    expect(bad?.error).toMatch(message);
    expect(h.styles.removed).toContain("a.bad");
    expect(h.api.writeLog).toHaveBeenCalledWith("a.bad", "error", expect.stringMatching(/failed to load/));
  });

  it("a failed plugin loads again when its package changes", async () => {
    quiet();
    const h = harness(listOf(1, [item("a.bad")]));
    h.behaviours.set("a.bad", "throws");
    await start(h);
    expect(h.runtime.getSnapshot().plugins["a.bad"]?.phase).toBe("failed");

    h.behaviours.set("a.bad", "ok");
    h.api.list.mockResolvedValue(listOf(2, [item("a.bad", { revision: 2 })]));
    h.bump(2);
    await vi.waitFor(() => expect(h.runtime.getSnapshot().plugins["a.bad"]?.phase).toBe("active"));
    expect(h.slotIds()).toEqual(["x:a.bad:button"]);
  });
});

describe("generation changes", () => {
  it("loads new, unloads removed and disabled, reloads changed", async () => {
    const h = harness(listOf(1, [item("a.keep"), item("a.remove"), item("a.disable"), item("a.dev")]));
    await start(h);
    expect(h.slotIds().sort()).toEqual(["x:a.dev:button", "x:a.disable:button", "x:a.keep:button", "x:a.remove:button"]);
    const loadSpy = vi.spyOn(h.host, "load");
    h.styles.injected.length = 0;

    h.api.list.mockResolvedValue(
      listOf(2, [
        item("a.keep"),
        item("a.disable", { enabled: false, status: "disabled" }),
        item("a.dev", { revision: 2 }),
        item("a.new"),
      ]),
    );
    h.bump(2);
    await vi.waitFor(() => expect(h.runtime.getSnapshot().plugins["a.new"]?.phase).toBe("active"));

    expect(h.slotIds().sort()).toEqual(["x:a.dev:button", "x:a.keep:button", "x:a.new:button"]);
    expect(h.host.get("a.remove")?.status).toBe("disposed");
    expect(h.host.get("a.disable")?.status).toBe("disposed");
    // Only the reloaded and the new plugin were (re)loaded.
    expect(loadSpy.mock.calls.map(([plugin]) => plugin.id).sort()).toEqual(["a.dev", "a.new"]);
    expect(h.styles.injected.sort()).toEqual(["a.dev", "a.new"]);
    expect(h.runtime.getSnapshot()).toMatchObject({
      generation: 2,
      plugins: {
        "a.keep": { phase: "active" },
        "a.dev": { phase: "active", revision: 2 },
        "a.disable": { phase: "skipped", reason: "disabled" },
      },
    });
    expect(h.runtime.getSnapshot().plugins["a.remove"]).toBeUndefined();
  });

  it("unloads everything when safe mode turns on", async () => {
    const h = harness(listOf(1, [item("a.one")]));
    await start(h);
    h.api.list.mockResolvedValue(listOf(2, [item("a.one")], { safe_mode: true }));
    h.bump(2);
    await vi.waitFor(() => expect(h.host.get("a.one")?.status).toBe("disposed"));
    expect(h.slotIds()).toEqual([]);
  });

  it("pushes a changed configuration to the plugin", async () => {
    const h = harness(listOf(1, [item("a.one")]));
    h.api.getConfig.mockResolvedValue({ values: { region: "cn" }, schema: null });
    await start(h);
    expect(h.contexts.get("a.one")?.config).toEqual({ region: "cn" });

    h.api.getConfig.mockResolvedValue({ values: { region: "hk" }, schema: null });
    h.api.list.mockResolvedValue(listOf(2, [item("a.one")]));
    h.bump(2);
    await vi.waitFor(() => expect(h.contexts.get("a.one")?.config).toEqual({ region: "hk" }));
  });

  it("keeps watching after a failed poll", async () => {
    const h = harness(listOf(1, [item("a.one")]));
    await start(h);
    h.api.watch.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    h.bump(1); // resolves the pending poll with the same generation
    await vi.waitFor(() => expect(h.api.watch.mock.calls.length).toBeGreaterThanOrEqual(2));
    await vi.waitFor(() => expect(h.api.watch.mock.calls.length).toBeGreaterThanOrEqual(3));
  });
});

describe("stop", () => {
  it("unloads every app plugin, clears the bridge and the marker", async () => {
    const h = harness(listOf(1, [item("a.one"), item("a.two")]));
    await start(h);
    expect(h.storage.data.has(LOADING_MARKER_KEY)).toBe(true);

    await h.runtime.stop();

    expect(h.host.get("a.one")?.status).toBe("disposed");
    expect(h.host.get("a.two")?.status).toBe("disposed");
    expect(h.slotIds()).toEqual([]);
    expect(h.disposeBridge).toHaveBeenCalledTimes(1);
    expect(h.storage.data.has(LOADING_MARKER_KEY)).toBe(false);
    expect(h.runtime.getSnapshot()).toMatchObject({ status: "stopped", plugins: {} });
  });
});
