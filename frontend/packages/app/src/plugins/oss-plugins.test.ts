import {
  ApiError,
  createPluginHost,
  definePlugin,
  extensionsApi,
  useRegistryStore,
  type PluginHost,
} from "@valuz/core";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { sectionComponents } from "../pages/settings/section-components";
import { extensionsBlocks } from "../pages/settings/extensions/blocks";
import {
  RequiredOssPluginError,
  loadOssPlugins,
  readInactiveBackendPlugins,
  reconcileOssPlugins,
  settleOssPlugins,
} from "./boot";
import {
  OSS_NAV_ITEM_ORDER,
  OSS_ROUTE_ORDER,
  OSS_SETTINGS_SECTION_ORDER,
} from "./layout";
import { ossPluginSpecs, ossPlugins } from "./specs";
import { composeOss, disposeComposedOss } from "./testing/compose-oss";
import { captureOssComposition } from "./testing/composition-snapshot";

const OPTIONAL_IDS = ossPluginSpecs
  .filter((spec) => !spec.required)
  .map((spec) => spec.plugin.id);

const ids = (list: Array<{ id: string }>) => list.map((entry) => entry.id);
const state = () => useRegistryStore.getState();
const lists = () => ({
  routes: ids(state().desktopRoutes),
  sections: ids(state().settingsSections),
  nav: ids(state().navItems),
  panels: ids(state().projectPanels),
  services: state().services.map((service) => service.name),
});
const blockKeys = () => extensionsBlocks.entries().map((entry) => entry.key);

beforeEach(() => {
  vi.restoreAllMocks();
});

afterEach(async () => {
  await disposeComposedOss();
});

describe("the OSS plugin set", () => {
  it("has the agreed ids: two required, the rest optional, in canonical order", () => {
    expect(ossPlugins.map((plugin) => plugin.id)).toEqual([
      "oss-core",
      "oss-agents",
      "oss-tasks",
      "oss-automations",
      "oss-activity",
      "oss-skills",
      "oss-connectors",
      "oss-knowledge",
      "oss-memory",
      "oss-browser",
      "oss-backup",
      "oss-marketplace",
      "oss-agent-plugins",
      "oss-dsh-plugins",
      "oss-plugin-ui",
    ]);
    expect(
      ossPluginSpecs.filter((spec) => spec.required).map((s) => s.plugin.id),
    ).toEqual(["oss-core", "oss-agents"]);
  });

  it("registers exactly the pages the layout tables order — no entry unplaced, none unused", async () => {
    await composeOss();

    expect(lists().routes).toEqual([...OSS_ROUTE_ORDER]);
    expect(lists().sections).toEqual([...OSS_SETTINGS_SECTION_ORDER]);
    expect(lists().nav).toEqual([...OSS_NAV_ITEM_ORDER]);
    expect(lists().panels).toEqual(["conversations", "projects"]);
    expect(lists().services).toEqual(["agent-server"]);
  });

  it("gives every registered section and route something to render", async () => {
    await composeOss();

    for (const section of state().settingsSections) {
      expect(
        section.component ?? sectionComponents.get(section.id),
        `settings section "${section.id}" has no component`,
      ).toBeDefined();
    }
    const resolved = (
      captureOssComposition() as { resolvedRouteIds: string[] }
    ).resolvedRouteIds;
    expect(resolved).toEqual(lists().routes);
  });

  it("makes no slot, category or capability contribution of its own", async () => {
    await composeOss();

    expect(state().slots).toEqual({});
    expect(state().capabilities).toEqual(
      useRegistryStore.getState().base.capabilities,
    );
  });

  it("reads the same whatever order the optional plugins load in", async () => {
    await composeOss();
    const canonical = captureOssComposition();
    await disposeComposedOss();

    const [core, ...rest] = ossPluginSpecs;
    const orders = [
      [...rest].reverse(),
      [...rest.slice(5), ...rest.slice(0, 5)],
      [rest[3], rest[9], rest[0], rest[11], rest[5], ...rest.filter((_, at) => ![3, 9, 0, 11, 5].includes(at))],
    ];
    for (const order of orders) {
      await composeOss({ specs: [core, ...order] });
      expect(captureOssComposition()).toEqual(canonical);
      await disposeComposedOss();
    }
  });
});

/**
 * Optional plugins that register nothing at load: ``oss-plugin-ui`` mounts UI
 * bus surfaces only once the backend announces plugin UI.
 */
const ON_DEMAND_IDS: ReadonlySet<string> = new Set(["oss-plugin-ui"]);

describe.each(OPTIONAL_IDS)("optional plugin %s", (id) => {
  it("unloads cleanly: its pages go and the registry equals one that never loaded it", async () => {
    const host = await composeOss();
    const full = lists();
    const fullBlocks = blockKeys();

    await host.unload(id);
    const unloaded = captureOssComposition();
    const afterUnload = { ...lists(), blocks: blockKeys() };

    // It contributed something that is gone now — except a plugin that only
    // mounts on demand, which must leave the registry untouched.
    const contributed =
      JSON.stringify(afterUnload) !==
      JSON.stringify({ ...full, blocks: fullBlocks });
    expect(contributed, `${id} registered nothing`).toBe(
      !ON_DEMAND_IDS.has(id),
    );
    expect(host.get(id)?.status).toBe("disposed");

    // …and what remains is exactly the composition without it.
    await disposeComposedOss();
    await composeOss({ inactive: [id] });
    expect(captureOssComposition()).toEqual(unloaded);
    expect({ ...lists(), blocks: blockKeys() }).toEqual(afterUnload);
  });

  it("is skipped, and recorded as backend-disabled, when its backend counterpart is inactive", async () => {
    const host = await composeOss({ inactive: [id] });

    expect(host.get(id)?.status).toBe("backend-disabled");
    // The rest of the app is unaffected.
    expect(host.get("oss-core")?.status).toBe("active");
    expect(
      OPTIONAL_IDS.filter((other) => other !== id).every(
        (other) => host.get(other)?.status === "active",
      ),
    ).toBe(true);
  });
});

describe("a plugin that throws", () => {
  const throwing = definePlugin({
    id: "oss-broken",
    apply(ctx) {
      ctx.registry.route({
        id: "broken-route",
        path: "/broken",
        label: "broken",
        description: "broken",
        layout: "project",
        showInNav: false,
        edition: "personal",
      });
      throw new Error("boom");
    },
  });

  it("leaves the other plugins working when it is optional, and rolls its own pages back", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const specs = [
      ...ossPluginSpecs.slice(0, 4),
      { plugin: throwing, required: false },
      ...ossPluginSpecs.slice(4),
    ];

    const host = createPluginHost();
    useRegistryStore.getState().clearLayers();
    const result = await loadOssPlugins(host, { inactive: [], specs });

    expect(result.failed).toEqual(["oss-broken"]);
    expect(host.get("oss-broken")?.status).toBe("failed");
    expect(lists().routes).not.toContain("broken-route");
    expect(lists().routes).toEqual([...OSS_ROUTE_ORDER]);
    expect(
      ossPlugins.every((plugin) => host.get(plugin.id)?.status === "active"),
    ).toBe(true);
    for (const record of [...host.list()].reverse()) {
      await host.unload(record.id);
    }
  });

  it("stops the boot with a RequiredOssPluginError when it is required", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const host = createPluginHost();
    useRegistryStore.getState().clearLayers();

    await expect(
      loadOssPlugins(host, {
        inactive: [],
        specs: [{ plugin: throwing, required: true }, ...ossPluginSpecs],
      }),
    ).rejects.toMatchObject({
      name: "RequiredOssPluginError",
      pluginId: "oss-broken",
      message: expect.stringContaining('required plugin "oss-broken"'),
    });
    expect(host.get("oss-core")).toBeUndefined();
  });

  it("is exposed as an Error subclass carrying the cause", () => {
    const cause = new Error("inner");
    const error = new RequiredOssPluginError("oss-core", cause);

    expect(error).toBeInstanceOf(Error);
    expect(error.cause).toBe(cause);
  });
});

describe("backend state gating", () => {
  const load = async (host: PluginHost = createPluginHost()) => {
    useRegistryStore.getState().clearLayers();
    const result = await loadOssPlugins(host);
    return { host, result };
  };

  afterEach(async () => {
    await disposeComposedOss();
  });

  it("does not load the optional plugin whose backend counterpart is in `inactive`", async () => {
    const backendState = vi
      .spyOn(extensionsApi, "backendState")
      .mockResolvedValue({ inactive: ["oss-browser"] });

    const { host, result } = await load();

    expect(backendState).toHaveBeenCalledTimes(1);
    expect(result.inactive).toEqual(["oss-browser"]);
    expect(result.skipped).toEqual(["oss-browser"]);
    expect(lists().sections).not.toContain("browser");
    expect(lists().sections).toContain("model");
    expect(host.get("oss-browser")).toMatchObject({
      id: "oss-browser",
      status: "backend-disabled",
    });
    for (const record of [...host.list()].reverse()) await host.unload(record.id);
  });

  it("asks before it loads anything", async () => {
    const order: string[] = [];
    vi.spyOn(extensionsApi, "backendState").mockImplementation(async () => {
      order.push("state");
      return { inactive: [] };
    });
    const host = createPluginHost();
    const originalLoad = host.load.bind(host);
    host.load = async (plugin) => {
      order.push(plugin.id);
      return originalLoad(plugin);
    };

    await load(host);

    expect(order[0]).toBe("state");
    expect(order[1]).toBe("oss-core");
    for (const record of [...host.list()].reverse()) await host.unload(record.id);
  });

  it.each([
    ["an older backend (404)", () => Promise.reject(new ApiError("Not Found", 404))],
    ["an auth wall (401)", () => Promise.reject(new ApiError("Unauthorized", 401))],
    ["an offline backend", () => Promise.reject(new TypeError("Failed to fetch"))],
    ["a response that is not the contract", () => Promise.resolve({} as never)],
  ])("loads everything when the state cannot be read: %s", async (_name, answer) => {
    vi.spyOn(extensionsApi, "backendState").mockImplementation(answer);

    const { host, result } = await load();

    expect(result.skipped).toEqual([]);
    expect(result.inactive).toEqual([]);
    expect(lists().routes).toEqual([...OSS_ROUTE_ORDER]);
    expect(lists().sections).toEqual([...OSS_SETTINGS_SECTION_ORDER]);
    expect(
      ossPlugins.every((plugin) => host.get(plugin.id)?.status === "active"),
    ).toBe(true);
    for (const record of [...host.list()].reverse()) await host.unload(record.id);
  });

  it("never skips a required plugin, whatever the backend says", async () => {
    vi.spyOn(extensionsApi, "backendState").mockResolvedValue({
      inactive: ["oss-core", "oss-agents", "oss-tasks"],
    });

    const { host, result } = await load();

    expect(result.skipped).toEqual(["oss-tasks"]);
    expect(host.get("oss-core")?.status).toBe("active");
    expect(host.get("oss-agents")?.status).toBe("active");
    for (const record of [...host.list()].reverse()) await host.unload(record.id);
  });

  it("is idempotent: loading again leaves loaded plugins alone", async () => {
    vi.spyOn(extensionsApi, "backendState").mockResolvedValue({ inactive: [] });
    const { host } = await load();
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const before = lists();

    const again = await loadOssPlugins(host);

    expect(again.loaded).toHaveLength(ossPlugins.length);
    expect(lists()).toEqual(before);
    expect(warn).not.toHaveBeenCalled();
    for (const record of [...host.list()].reverse()) await host.unload(record.id);
  });

  it("readInactiveBackendPlugins answers null instead of throwing", async () => {
    vi.spyOn(extensionsApi, "backendState").mockRejectedValue(new Error("x"));
    await expect(readInactiveBackendPlugins()).resolves.toBeNull();

    vi.spyOn(extensionsApi, "backendState").mockResolvedValue({
      inactive: ["a", 3 as never, "b"],
    });
    await expect(readInactiveBackendPlugins()).resolves.toEqual(["a", "b"]);
  });
});

describe("reconcile against the backend state", () => {
  it("unloads a plugin the backend reports inactive after the first, failed, read — and loads it back", async () => {
    // Boot while the backend is still starting: nothing known, all loaded.
    const host = await composeOss();
    const all = captureOssComposition();
    expect(host.get("oss-backup")?.status).toBe("active");

    const changed = await reconcileOssPlugins(host, { inactive: ["oss-backup"] });

    expect(changed).toBe(true);
    expect(host.get("oss-backup")?.status).toBe("backend-disabled");
    expect(lists().sections).not.toContain("backup");
    expect(lists().sections).toContain("browser");

    // Switched back on: the section returns to its place.
    expect(await reconcileOssPlugins(host, { inactive: [] })).toBe(true);
    expect(host.get("oss-backup")?.status).toBe("active");
    expect(captureOssComposition()).toEqual(all);
  });

  it("changes nothing when there is nothing to change, or the state cannot be read", async () => {
    const host = await composeOss();
    const before = captureOssComposition();

    expect(await reconcileOssPlugins(host, { inactive: [] })).toBe(false);

    vi.spyOn(extensionsApi, "backendState").mockRejectedValue(
      new TypeError("Failed to fetch"),
    );
    expect(await reconcileOssPlugins(host)).toBe(false);
    expect(captureOssComposition()).toEqual(before);
  });

  it("never touches a required plugin", async () => {
    const host = await composeOss();

    await reconcileOssPlugins(host, { inactive: ["oss-core", "oss-agents"] });

    expect(host.get("oss-core")?.status).toBe("active");
    expect(host.get("oss-agents")?.status).toBe("active");
  });
});

describe("loadOssPlugins reports whether it knew the backend state", () => {
  it("is known when the caller gives it, or the backend answers", async () => {
    useRegistryStore.getState().clearLayers();
    const given = await loadOssPlugins(createPluginHost(), { inactive: [] });
    expect(given.stateKnown).toBe(true);

    vi.spyOn(extensionsApi, "backendState").mockResolvedValue({ inactive: [] });
    useRegistryStore.getState().clearLayers();
    const read = await loadOssPlugins(createPluginHost());
    expect(read.stateKnown).toBe(true);
  });

  it("is not known when the backend could not be asked — everything loaded, to be settled later", async () => {
    vi.spyOn(extensionsApi, "backendState").mockRejectedValue(
      new Error("无法连接到后端服务，请检查应用进程是否运行"),
    );
    useRegistryStore.getState().clearLayers();

    const result = await loadOssPlugins(createPluginHost());

    expect(result.stateKnown).toBe(false);
    expect(result.loaded).toHaveLength(ossPlugins.length);
  });
});

describe("settleOssPlugins (a backend that starts after the UI)", () => {
  it("keeps asking while the backend is unreachable, then applies the state it reports", async () => {
    const host = await composeOss();
    const backendState = vi
      .spyOn(extensionsApi, "backendState")
      .mockRejectedValueOnce(new Error("无法连接到后端服务"))
      .mockRejectedValueOnce(new Error("无法连接到后端服务"))
      .mockResolvedValue({ inactive: ["oss-backup", "oss-browser"] });

    const applied = await settleOssPlugins(host, { intervalMs: 1 });

    expect(applied).toBe(true);
    expect(backendState).toHaveBeenCalledTimes(3);
    expect(lists().sections).not.toContain("backup");
    expect(lists().sections).not.toContain("browser");
    expect(lists().sections).toContain("model");
    expect(host.get("oss-browser")?.status).toBe("backend-disabled");
  });

  it("stops at once when the backend answers but cannot say (an older backend)", async () => {
    const host = await composeOss();
    const before = captureOssComposition();
    const backendState = vi
      .spyOn(extensionsApi, "backendState")
      .mockRejectedValue(new ApiError("Not Found", 404));

    expect(await settleOssPlugins(host, { intervalMs: 1 })).toBe(false);

    expect(backendState).toHaveBeenCalledTimes(1);
    expect(captureOssComposition()).toEqual(before);
  });

  it("gives up after the timeout when the backend never answers, leaving everything loaded", async () => {
    const host = await composeOss();
    const before = captureOssComposition();
    vi.spyOn(extensionsApi, "backendState").mockRejectedValue(
      new Error("无法连接到后端服务"),
    );

    expect(await settleOssPlugins(host, { intervalMs: 1, timeoutMs: 20 })).toBe(
      false,
    );
    expect(captureOssComposition()).toEqual(before);
  });

  it("stops when aborted", async () => {
    const host = await composeOss();
    const backendState = vi.spyOn(extensionsApi, "backendState");
    const controller = new AbortController();
    controller.abort();

    expect(await settleOssPlugins(host, { signal: controller.signal })).toBe(
      false,
    );
    expect(backendState).not.toHaveBeenCalled();
  });
});
