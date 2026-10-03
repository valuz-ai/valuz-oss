import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { personalProfile } from "../edition/personal-profile";
import type {
  DesktopRouteModule,
  NavItemModule,
  SettingsSectionModule,
} from "../edition/profile";
import { useRegistryStore } from "../edition/registry-store";
import { useCategoryRegistry } from "../hooks/use-resource-categories";
import { createPluginHost, definePlugin } from "./host";
import type { PluginHost } from "./types";

const Noop = () => null;
const slotIds = (name: string) =>
  (useRegistryStore.getState().slots[name] ?? []).map((reg) => reg.id);

let host: PluginHost;

beforeEach(() => {
  useRegistryStore.getState().clearLayers();
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
  it("can load a plugin again after it failed", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    let shouldFail = true;
    const flaky = definePlugin({
      id: "flaky",
      apply(ctx) {
        if (shouldFail) throw new Error("not yet");
        ctx.registry.slot("retry.slot", { id: "flaky-cell", component: Noop });
      },
    });
    expect((await host.load(flaky)).status).toBe("failed");
    shouldFail = false;
    expect((await host.load(flaky)).status).toBe("active");
    expect(slotIds("retry.slot")).toEqual(["flaky-cell"]);
  });
});

describe("plugin host: page contributions over a hydrated base", () => {
  const route = (id: string): DesktopRouteModule => ({
    id,
    path: `/${id}`,
    label: id,
    description: id,
    layout: "project",
    showInNav: false,
    edition: "personal",
  });
  const section = (id: string): SettingsSectionModule => ({
    id,
    label: id,
    description: id,
    edition: "personal",
  });
  const nav = (id: string): NavItemModule => ({
    id,
    label: id,
    href: `/${id}`,
    position: "top",
    edition: "personal",
  });
  const ids = (list: Array<{ id: string }>) => list.map((entry) => entry.id);
  const state = () => useRegistryStore.getState();

  it("keeps contributions made BEFORE the host hydrates the registry", async () => {
    await host.load(
      definePlugin({
        id: "early",
        apply(ctx) {
          ctx.registry.route(route("early-route"));
          ctx.registry.settingsSection(section("early-section"));
          ctx.registry.navItem(nav("early-nav"));
          ctx.registry.navGroup({ id: "early-group", label: "Early" });
          ctx.registry.projectPanel({
            id: "early-panel",
            label: "Early",
            edition: "personal",
          });
          ctx.registry.service({
            name: "early-service",
            defaultPort: 1,
            requiredForBoot: false,
          });
          ctx.registry.capabilities({ managedRuntimeSetup: true });
        },
      }),
    );

    // What hydrateOverlayIfPresent does once the overlay module has loaded.
    state().hydrate(personalProfile);

    expect(ids(state().desktopRoutes)).toContain("early-route");
    expect(ids(state().settingsSections)).toContain("early-section");
    expect(ids(state().navItems)).toContain("early-nav");
    expect(ids(state().navGroups)).toContain("early-group");
    expect(ids(state().projectPanels)).toContain("early-panel");
    expect(state().services.map((s) => s.name)).toContain("early-service");
    expect(state().capabilities.managedRuntimeSetup).toBe(true);

    await host.unload("early");
    expect(ids(state().desktopRoutes)).not.toContain("early-route");
    expect(ids(state().navGroups)).not.toContain("early-group");
    expect(state().capabilities.managedRuntimeSetup).toBe(false);
  });

  it("places entries with before / after and puts them back on unload", async () => {
    const sections = ids(state().settingsSections);
    const anchor = sections[1];
    await host.load(
      definePlugin({
        id: "placed",
        apply(ctx) {
          ctx.registry.settingsSection(section("placed-section"), {
            after: anchor,
          });
          ctx.registry.route(route("placed-route"), {
            before: ids(state().desktopRoutes)[0],
          });
          ctx.registry.navItem(nav("placed-nav"), {
            after: ids(state().navItems)[0],
          });
        },
      }),
    );

    expect(ids(state().settingsSections)[2]).toBe("placed-section");
    expect(ids(state().desktopRoutes)[0]).toBe("placed-route");
    expect(ids(state().navItems)[1]).toBe("placed-nav");

    await host.unload("placed");
    expect(ids(state().settingsSections)).toEqual(sections);
  });

  it("hides base entries while loaded and restores them in place on unload", async () => {
    const routesBefore = ids(state().desktopRoutes);
    const sectionsBefore = ids(state().settingsSections);
    const navBefore = ids(state().navItems);
    await host.load(
      definePlugin({
        id: "remover",
        apply(ctx) {
          ctx.registry.removeRoute(routesBefore[1]);
          ctx.registry.removeSettingsSection(sectionsBefore[0]);
          ctx.registry.removeNavItem(navBefore[0]);
        },
      }),
    );

    expect(ids(state().desktopRoutes)).not.toContain(routesBefore[1]);
    expect(ids(state().settingsSections)).not.toContain(sectionsBefore[0]);
    expect(ids(state().navItems)).not.toContain(navBefore[0]);

    // The base can be swapped underneath it and the entry stays hidden.
    state().hydrate(personalProfile);
    expect(ids(state().desktopRoutes)).not.toContain(routesBefore[1]);

    await host.unload("remover");
    expect(ids(state().desktopRoutes)).toEqual(routesBefore);
    expect(ids(state().settingsSections)).toEqual(sectionsBefore);
    expect(ids(state().navItems)).toEqual(navBefore);
  });

  it("rolls back every layer entry of a plugin that throws, removals and placements included", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const routesBefore = ids(state().desktopRoutes);
    const sectionsBefore = ids(state().settingsSections);
    const record = await host.load(
      definePlugin({
        id: "half-done",
        apply(ctx) {
          ctx.registry.removeRoute(routesBefore[0]);
          ctx.registry.route(route("half-route"), { after: routesBefore[2] });
          ctx.registry.settingsSection(section("half-section"));
          ctx.registry.capabilities({ configureModelChannel: false });
          throw new Error("boom");
        },
      }),
    );

    expect(record.status).toBe("failed");
    expect(ids(state().desktopRoutes)).toEqual(routesBefore);
    expect(ids(state().settingsSections)).toEqual(sectionsBefore);
    expect(state().capabilities.configureModelChannel).toBe(true);
  });

  it("orders contributions of several plugins by registration, whatever the anchors", async () => {
    const [first] = ids(state().settingsSections);
    await host.loadAll([
      definePlugin({
        id: "p1",
        apply(ctx) {
          ctx.registry.settingsSection(section("p1-section"), {
            after: "p2-section", // registered by a plugin that loads later
          });
        },
      }),
      definePlugin({
        id: "p2",
        apply(ctx) {
          ctx.registry.settingsSection(section("p2-section"), {
            after: first,
          });
        },
      }),
    ]);
    expect(ids(state().settingsSections).slice(0, 3)).toEqual([
      first,
      "p2-section",
      "p1-section",
    ]);

    // Unloading the anchor's plugin lets the follower fall back to the end.
    await host.unload("p2");
    const after = ids(state().settingsSections);
    expect(after[after.length - 1]).toBe("p1-section");
  });
});
