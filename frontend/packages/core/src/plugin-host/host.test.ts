import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { personalProfile } from "../edition/personal-profile";
import { useRegistryStore } from "../edition/registry-store";
import { useCategoryRegistry } from "../hooks/use-resource-categories";
import { createPluginHost, definePlugin } from "./host";
import type { PluginHost } from "./types";

const Noop = () => null;
const slotIds = (name: string) =>
  (useRegistryStore.getState().slots[name] ?? []).map((reg) => reg.id);

let host: PluginHost;

beforeEach(() => {
  useRegistryStore.getState().hydrate(personalProfile);
  useRegistryStore.setState({ slots: {}, suppressed: {} });
  useCategoryRegistry.setState({ injected: {}, contributions: {} });
  host = createPluginHost();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("plugin host", () => {
  it("applies a plugin's registrations and withdraws them on unload", async () => {
    const settings = useRegistryStore
      .getState()
      .desktopRoutes.find((r) => r.id === "settings")!;
    const plugin = definePlugin({
      id: "demo",
      apply(ctx) {
        ctx.registry.slot("demo.slot", { id: "demo-cell", component: Noop });
        ctx.registry.route({ ...settings, path: "/demo-settings" });
        ctx.registry.categories("agent", [
          { id: "demo", label: "demo", order: 0, filter: () => true },
        ]);
        ctx.registry.suppress("conversation.composer");
        ctx.registry.capabilities({ configureModelChannel: false });
      },
    });

    const record = await host.load(plugin);
    const state = () => useRegistryStore.getState();

    expect(record.status).toBe("active");
    expect(slotIds("demo.slot")).toEqual(["demo-cell"]);
    expect(state().desktopRoutes.find((r) => r.id === "settings")?.path).toBe(
      "/demo-settings",
    );
    expect(
      useCategoryRegistry.getState().injected.agent?.map((c) => c.id),
    ).toEqual(["demo"]);
    expect(state().suppressed["conversation.composer"]).toBe(true);
    expect(state().capabilities.configureModelChannel).toBe(false);

    await host.unload("demo");

    expect(host.get("demo")?.status).toBe("disposed");
    expect(slotIds("demo.slot")).toEqual([]);
    expect(state().desktopRoutes.find((r) => r.id === "settings")).toBe(
      settings,
    );
    expect(useCategoryRegistry.getState().injected.agent).toBeUndefined();
    expect(state().suppressed["conversation.composer"]).toBe(false);
    expect(state().capabilities.configureModelChannel).toBe(true);
  });

  it("isolates a failing plugin: it alone is disabled and rolled back", async () => {
    const logged = vi.spyOn(console, "error").mockImplementation(() => {});
    const before = definePlugin({
      id: "before",
      apply(ctx) {
        ctx.registry.slot("shared.slot", {
          id: "before-cell",
          component: Noop,
        });
      },
    });
    const broken = definePlugin({
      id: "broken",
      apply(ctx) {
        ctx.registry.slot("shared.slot", {
          id: "broken-cell",
          component: Noop,
        });
        throw new Error("plugin exploded");
      },
    });
    const after = definePlugin({
      id: "after",
      apply(ctx) {
        ctx.registry.slot("shared.slot", { id: "after-cell", component: Noop });
      },
    });

    const records = await host.loadAll([before, broken, after]);

    expect(records.map((r) => [r.id, r.status])).toEqual([
      ["before", "active"],
      ["broken", "failed"],
      ["after", "active"],
    ]);
    expect(String(host.get("broken")?.error)).toContain("plugin exploded");
    // The registration it made before throwing is rolled back.
    expect(slotIds("shared.slot")).toEqual(["before-cell", "after-cell"]);
    expect(
      logged.mock.calls.some((call) => String(call[0]).includes('"broken"')),
    ).toBe(true);
  });

  it("loads plugins in order so contributions keep a deterministic order", async () => {
    const plugins = ["a", "b", "c"].map((id) =>
      definePlugin({
        id,
        async apply(ctx) {
          // An async apply must still finish before the next plugin starts.
          await Promise.resolve();
          ctx.registry.slot("ordered.slot", {
            id: `${id}-cell`,
            component: Noop,
          });
        },
      }),
    );
    await host.loadAll(plugins);
    expect(slotIds("ordered.slot")).toEqual(["a-cell", "b-cell", "c-cell"]);
  });

  it("records legacy installs and warns that unloading cannot undo them", async () => {
    const warned = vi.spyOn(console, "warn").mockImplementation(() => {});
    const install = vi.fn();
    await host.load(
      definePlugin({
        id: "legacy",
        apply(ctx) {
          ctx.legacy("installGlobalThing", install);
        },
      }),
    );
    expect(install).toHaveBeenCalledOnce();
    expect(host.get("legacy")?.legacy).toEqual(["installGlobalThing"]);

    await host.unload("legacy");
    expect(
      warned.mock.calls.some((call) =>
        String(call[0]).includes("installGlobalThing"),
      ),
    ).toBe(true);
  });

  it("runs effect disposers on unload", async () => {
    const disposed = vi.fn();
    await host.load(
      definePlugin({
        id: "effects",
        apply(ctx) {
          ctx.effect(() => disposed);
        },
      }),
    );
    expect(disposed).not.toHaveBeenCalled();
    await host.unload("effects");
    expect(disposed).toHaveBeenCalledOnce();
  });

  it("binds a child plugin's lifetime to its parent", async () => {
    await host.load(
      definePlugin({
        id: "parent",
        async apply(ctx) {
          await ctx.plugin(
            definePlugin({
              id: "child",
              apply(child) {
                child.registry.slot("family.slot", {
                  id: "child-cell",
                  component: Noop,
                });
              },
            }),
          );
        },
      }),
    );
    expect(slotIds("family.slot")).toEqual(["child-cell"]);
    expect(host.get("child")?.status).toBe("active");

    await host.unload("parent");
    expect(slotIds("family.slot")).toEqual([]);
    expect(host.get("child")?.status).toBe("disposed");
  });

  it("does not apply the same plugin id twice", async () => {
    const apply = vi.fn();
    const warned = vi.spyOn(console, "warn").mockImplementation(() => {});
    await host.load(definePlugin({ id: "once", apply }));
    const second = await host.load(definePlugin({ id: "once", apply }));
    expect(apply).toHaveBeenCalledOnce();
    expect(second.status).toBe("active");
    expect(warned).toHaveBeenCalled();
  });

  it("notifies subscribers as plugin states change", async () => {
    const seen: string[] = [];
    const unsubscribe = host.subscribe(() => {
      seen.push(
        host
          .list()
          .map((r) => `${r.id}:${r.status}`)
          .join(","),
      );
    });
    await host.load(definePlugin({ id: "watched", apply() {} }));
    await host.unload("watched");
    unsubscribe();
    expect(seen).toEqual([
      "watched:loading",
      "watched:active",
      "watched:disposed",
    ]);
  });
});
