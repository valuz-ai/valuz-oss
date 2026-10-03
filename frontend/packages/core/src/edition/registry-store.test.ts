import { beforeEach, describe, expect, it } from "vitest";
import { personalProfile } from "./personal-profile";
import { fixtureProfile } from "./test-profile";
import type {
  DesktopRouteModule,
  EditionProfile,
  NavItemModule,
  SettingsSectionModule,
} from "./profile";
import { useRegistryStore } from "./registry-store";
import { registerPlugin } from "./plugin";

const reset = () => {
  useRegistryStore.getState().clearLayers();
  useRegistryStore.getState().hydrate(fixtureProfile);
};

const route = (id: string, path = `/${id}`): DesktopRouteModule => ({
  id,
  path,
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

describe("registry store", () => {
  beforeEach(reset);

  it("starts seeded from the personal profile: identity and the boot service, no pages", () => {
    useRegistryStore.getState().clearLayers();
    useRegistryStore.getState().setEdition("personal");
    const state = useRegistryStore.getState();
    expect(state.edition).toBe("personal");
    // Pages are registered by the OSS plugins, not declared by the profile.
    expect(state.desktopRoutes).toEqual([]);
    expect(state.settingsSections).toEqual([]);
    expect(state.navItems).toEqual([]);
    expect(state.projectPanels).toEqual([]);
    expect(state.services.map((service) => service.name)).toEqual([
      "agent-server",
    ]);
  });

  it("seeds capabilities to all-on by default", () => {
    const state = useRegistryStore.getState();
    expect(state.capabilities.configureModelChannel).toBe(true);
  });

  it("flips a single capability via setCapabilities without touching others", () => {
    useRegistryStore
      .getState()
      .setCapabilities({ configureModelChannel: false });
    expect(useRegistryStore.getState().capabilities.configureModelChannel).toBe(
      false,
    );
  });

  it("registers a runtime route and lets callers dispose it", () => {
    const dispose = useRegistryStore.getState().registerRoute({
      id: "plugin-route",
      path: "/plugin",
      label: "Plugin",
      description: "Runtime-added route",
      layout: "project",
      showInNav: true,
      edition: "personal",
    });

    expect(
      useRegistryStore
        .getState()
        .desktopRoutes.some((r) => r.id === "plugin-route"),
    ).toBe(true);

    dispose();

    expect(
      useRegistryStore
        .getState()
        .desktopRoutes.some((r) => r.id === "plugin-route"),
    ).toBe(false);
  });

  it("restores the base route, in place, when a replacing registration is disposed", () => {
    const store = useRegistryStore.getState();
    const before = store.desktopRoutes;
    const settings = before.find((r) => r.id === "settings")!;
    const dispose = store.registerRoute({
      ...settings,
      path: "/plugin-settings",
    });

    expect(
      useRegistryStore.getState().desktopRoutes.find((r) => r.id === "settings")
        ?.path,
    ).toBe("/plugin-settings");

    dispose();

    // Used to delete the route outright: a plugin that replaced a host page
    // and then unloaded took the host page down with it.
    expect(useRegistryStore.getState().desktopRoutes.map((r) => r.id)).toEqual(
      before.map((r) => r.id),
    );
    expect(
      useRegistryStore
        .getState()
        .desktopRoutes.find((r) => r.id === "settings"),
    ).toBe(settings);
  });

  it("leaves a newer replacement in place when an older one is disposed", () => {
    const store = useRegistryStore.getState();
    const settings = store.desktopRoutes.find((r) => r.id === "settings")!;
    const disposeOlder = store.registerRoute({ ...settings, path: "/older" });
    useRegistryStore.getState().registerRoute({ ...settings, path: "/newer" });

    disposeOlder();

    expect(
      useRegistryStore.getState().desktopRoutes.find((r) => r.id === "settings")
        ?.path,
    ).toBe("/newer");
  });

  it("restores a replaced settings section when its replacement is disposed", () => {
    const store = useRegistryStore.getState();
    const [first] = store.settingsSections;
    const dispose = store.registerSettingsSection({ ...first, icon: "radio" });
    dispose();
    expect(useRegistryStore.getState().settingsSections[0]).toBe(first);
  });

  it("orders slot registrations by priority, then by registration order", () => {
    const store = useRegistryStore.getState();
    const Noop = () => null;
    store.registerSlot("test.ordering", { id: "late", component: Noop });
    store.registerSlot("test.ordering", {
      id: "early",
      component: Noop,
      priority: -10,
    });
    store.registerSlot("test.ordering", { id: "default", component: Noop });
    store.registerSlot("test.ordering", {
      id: "last",
      component: Noop,
      priority: 10,
    });

    expect(
      useRegistryStore.getState().slots["test.ordering"]?.map((r) => r.id),
    ).toEqual(["early", "late", "default", "last"]);
  });

  // 原 'edition hot-swap' 测试已删除：Slice 3 把 enterpriseProfile 从公共骨架移除，
  // 当前 setEdition 永远 reseed 为 personalProfile。未来如果真引入 enterprise overlay，
  // 由 overlay 直接 hydrate() 即可，不再走 setEdition('enterprise')。
});

describe("registerPlugin", () => {
  beforeEach(reset);

  it("registers contributions and unloads them cleanly", async () => {
    const plugin = await registerPlugin({
      id: "test-plugin",
      version: "0.0.1",
      routes: [
        {
          id: "test-plugin-route",
          path: "/test-plugin",
          label: "Test Plugin",
          description: "Runtime-registered route from a plugin",
          layout: "project",
          showInNav: true,
          edition: "personal",
        },
      ],
      services: [
        {
          name: "plugin-sidecar",
          defaultPort: 20000,
          requiredForBoot: false,
        },
      ],
    });

    const afterRegister = useRegistryStore.getState();
    expect(
      afterRegister.desktopRoutes.some((r) => r.id === "test-plugin-route"),
    ).toBe(true);
    expect(
      afterRegister.services.some((s) => s.name === "plugin-sidecar"),
    ).toBe(true);

    await plugin.unload();

    const afterUnload = useRegistryStore.getState();
    expect(
      afterUnload.desktopRoutes.some((r) => r.id === "test-plugin-route"),
    ).toBe(false);
    expect(afterUnload.services.some((s) => s.name === "plugin-sidecar")).toBe(
      false,
    );
  });
});

describe("registry layering", () => {
  beforeEach(reset);

  /** A profile that differs from the personal one, to rebase onto. */
  const otherProfile = (): EditionProfile => ({
    ...personalProfile,
    edition: "commercial",
    desktopRoutes: [route("base-a"), route("base-b"), route("base-c")],
    settingsSections: [section("base-a"), section("base-b")],
    navItems: [nav("base-a"), nav("base-b")],
    navGroups: [{ id: "base-group", label: "Base group" }],
    projectPanels: [{ id: "base-panel", label: "Panel", edition: "personal" }],
    capabilities: { ...personalProfile.capabilities },
  });

  describe("hydrate keeps live registrations", () => {
    it("keeps routes, sections, panels, nav, services and groups registered before hydrate", () => {
      const store = state();
      store.registerRoute(route("live-route"));
      store.registerSettingsSection(section("live-section"));
      store.registerProjectPanel({
        id: "live-panel",
        label: "Live",
        edition: "personal",
      });
      store.registerNavItem(nav("live-nav"));
      store.registerNavGroup({ id: "live-group", label: "Live group" });
      store.registerService({
        name: "live-service",
        defaultPort: 1,
        requiredForBoot: false,
      });

      state().hydrate(otherProfile());

      expect(ids(state().desktopRoutes)).toEqual([
        "base-a",
        "base-b",
        "base-c",
        "live-route",
      ]);
      expect(ids(state().settingsSections)).toEqual([
        "base-a",
        "base-b",
        "live-section",
      ]);
      expect(ids(state().projectPanels)).toEqual(["base-panel", "live-panel"]);
      expect(ids(state().navItems)).toEqual(["base-a", "base-b", "live-nav"]);
      expect(ids(state().navGroups)).toEqual(["base-group", "live-group"]);
      expect(state().services.map((s) => s.name)).toEqual([
        "agent-server",
        "live-service",
      ]);
      expect(state().edition).toBe("commercial");
    });

    it("keeps a replacement in the replaced entry's position across hydrate, and restores the NEW base entry on dispose", () => {
      const dispose = state().registerRoute(route("base-b", "/replaced"));
      const next = otherProfile();
      state().hydrate(next);

      expect(ids(state().desktopRoutes)).toEqual([
        "base-a",
        "base-b",
        "base-c",
      ]);
      expect(
        state().desktopRoutes.find((r) => r.id === "base-b")?.path,
      ).toBe("/replaced");

      dispose();
      expect(state().desktopRoutes[1]).toBe(next.desktopRoutes[1]);
    });

    it("withdraws a live registration after hydrate", () => {
      const dispose = state().registerNavItem(nav("live-nav"));
      state().hydrate(otherProfile());
      dispose();
      expect(ids(state().navItems)).toEqual(["base-a", "base-b"]);
    });

    it("rebases onto the personal profile through setEdition without dropping layers", () => {
      state().hydrate(otherProfile());
      state().registerRoute(route("live-route"));
      state().setEdition("personal");
      expect(state().edition).toBe("personal");
      expect(state().desktopRoutes.map((r) => r.id)).toEqual(["live-route"]);
      expect(state().desktopRoutes.map((r) => r.id)).not.toContain("base-a");
    });

    it("clearLayers drops every live registration and leaves the base", () => {
      state().hydrate(otherProfile());
      state().registerRoute(route("live-route"));
      state().registerCapabilities({ configureModelChannel: false });
      state().clearLayers();
      expect(ids(state().desktopRoutes)).toEqual([
        "base-a",
        "base-b",
        "base-c",
      ]);
      expect(state().capabilities.configureModelChannel).toBe(true);
    });
  });

  describe("placement", () => {
    beforeEach(() => state().hydrate(otherProfile()));

    it("inserts a new route before or after an anchor", () => {
      state().registerRoute(route("after-a"), { after: "base-a" });
      state().registerRoute(route("before-c"), { before: "base-c" });
      expect(ids(state().desktopRoutes)).toEqual([
        "base-a",
        "after-a",
        "base-b",
        "before-c",
        "base-c",
      ]);
    });

    it("places settings sections and nav items the same way", () => {
      state().registerSettingsSection(section("s-first"), {
        before: "base-a",
      });
      state().registerSettingsSection(section("s-mid"), { after: "base-a" });
      state().registerNavItem(nav("n-after"), { after: "base-b" });
      expect(ids(state().settingsSections)).toEqual([
        "s-first",
        "base-a",
        "s-mid",
        "base-b",
      ]);
      expect(ids(state().navItems)).toEqual(["base-a", "base-b", "n-after"]);
    });

    it("chains entries registered after one another", () => {
      state().registerRoute(route("one"), { after: "base-a" });
      state().registerRoute(route("two"), { after: "one" });
      state().registerRoute(route("three"), { after: "two" });
      expect(ids(state().desktopRoutes)).toEqual([
        "base-a",
        "one",
        "two",
        "three",
        "base-b",
        "base-c",
      ]);
    });

    it("keeps a replacement where the id already is, ignoring placement", () => {
      state().registerRoute(route("base-c", "/moved"), { after: "base-a" });
      expect(ids(state().desktopRoutes)).toEqual([
        "base-a",
        "base-b",
        "base-c",
      ]);
    });

    it("waits for an anchor registered later, then lands next to it", () => {
      state().registerRoute(route("follower"), { after: "late-anchor" });
      // Anchor unknown so far: the entry is parked at the end.
      expect(ids(state().desktopRoutes)).toEqual([
        "base-a",
        "base-b",
        "base-c",
        "follower",
      ]);

      state().registerRoute(route("late-anchor"), { after: "base-a" });
      expect(ids(state().desktopRoutes)).toEqual([
        "base-a",
        "late-anchor",
        "follower",
        "base-b",
        "base-c",
      ]);
    });

    it("resolves a chain whose links were registered in reverse", () => {
      state().registerRoute(route("c"), { after: "b" });
      state().registerRoute(route("b"), { after: "a" });
      state().registerRoute(route("a"), { after: "base-a" });
      expect(ids(state().desktopRoutes)).toEqual([
        "base-a",
        "a",
        "b",
        "c",
        "base-b",
        "base-c",
      ]);
    });

    it("appends an entry whose anchor never appears, in registration order", () => {
      state().registerRoute(route("orphan-1"), { after: "nowhere" });
      state().registerRoute(route("orphan-2"), { before: "nowhere" });
      expect(ids(state().desktopRoutes)).toEqual([
        "base-a",
        "base-b",
        "base-c",
        "orphan-1",
        "orphan-2",
      ]);
    });

    it("re-derives the order when a placed entry is disposed", () => {
      const dispose = state().registerRoute(route("after-a"), {
        after: "base-a",
      });
      dispose();
      expect(ids(state().desktopRoutes)).toEqual([
        "base-a",
        "base-b",
        "base-c",
      ]);
    });

    it("keeps a placement valid after the base is rebased", () => {
      state().registerRoute(route("after-a"), { after: "base-a" });
      state().hydrate({
        ...otherProfile(),
        desktopRoutes: [route("base-z"), route("base-a")],
      });
      expect(ids(state().desktopRoutes)).toEqual([
        "base-z",
        "base-a",
        "after-a",
      ]);
    });
  });

  describe("removal", () => {
    beforeEach(() => state().hydrate(otherProfile()));

    it("hides a base entry while registered and restores it in place on dispose", () => {
      const dispose = state().registerRouteRemoval("base-b");
      expect(ids(state().desktopRoutes)).toEqual(["base-a", "base-c"]);
      dispose();
      expect(ids(state().desktopRoutes)).toEqual([
        "base-a",
        "base-b",
        "base-c",
      ]);
    });

    it("applies to settings sections and nav items", () => {
      const disposeSection = state().registerSettingsSectionRemoval("base-a");
      const disposeNav = state().registerNavItemRemoval("base-b");
      expect(ids(state().settingsSections)).toEqual(["base-b"]);
      expect(ids(state().navItems)).toEqual(["base-a"]);
      disposeSection();
      disposeNav();
      expect(ids(state().settingsSections)).toEqual(["base-a", "base-b"]);
      expect(ids(state().navItems)).toEqual(["base-a", "base-b"]);
    });

    it("survives hydrate: the new base's entry stays hidden", () => {
      state().registerRouteRemoval("base-b");
      state().hydrate({
        ...otherProfile(),
        desktopRoutes: [route("base-b"), route("base-x")],
      });
      expect(ids(state().desktopRoutes)).toEqual(["base-x"]);
    });

    it("hides an entry another plugin registered earlier", () => {
      state().registerRoute(route("plugin-route"));
      state().registerRouteRemoval("plugin-route");
      expect(ids(state().desktopRoutes)).not.toContain("plugin-route");
    });

    it("brings the entry back at its placement when it is registered again afterwards", () => {
      state().registerRouteRemoval("base-a");
      state().registerRoute(route("base-a"), { after: "base-b" });
      expect(ids(state().desktopRoutes)).toEqual([
        "base-b",
        "base-a",
        "base-c",
      ]);
    });

    it("hiding an id that does not exist is harmless", () => {
      const dispose = state().registerRouteRemoval("ghost");
      expect(ids(state().desktopRoutes)).toEqual([
        "base-a",
        "base-b",
        "base-c",
      ]);
      dispose();
    });

    it("keeps two removals of one id independent", () => {
      const first = state().registerRouteRemoval("base-a");
      const second = state().registerRouteRemoval("base-a");
      first();
      expect(ids(state().desktopRoutes)).not.toContain("base-a");
      second();
      expect(ids(state().desktopRoutes)).toContain("base-a");
    });
  });

  describe("imperative unregister", () => {
    beforeEach(() => state().hydrate(otherProfile()));

    it("drops a live registration", () => {
      state().registerRoute(route("live-route"));
      state().unregisterRoute("live-route");
      expect(ids(state().desktopRoutes)).toEqual([
        "base-a",
        "base-b",
        "base-c",
      ]);
    });

    it("hides a base entry, and keeps it hidden across hydrate of the same entry", () => {
      state().unregisterSettingsSection("base-a");
      expect(ids(state().settingsSections)).toEqual(["base-b"]);
      state().hydrate(otherProfile());
      expect(ids(state().settingsSections)).toEqual(["base-b"]);
    });

    it("re-registering after an unregister moves the entry to the end", () => {
      state().unregisterNavItem("base-a");
      state().registerNavItem(nav("base-a"));
      expect(ids(state().navItems)).toEqual(["base-b", "base-a"]);
    });

    it("does not grow the layer stack across repeated unregister/register cycles", () => {
      for (let i = 0; i < 5; i += 1) {
        state().unregisterNavItem("base-a");
        state().registerNavItem(nav("base-a"));
      }
      // One removal + one put, however many times it was toggled.
      expect(state().layers.navItems).toHaveLength(2);
      expect(ids(state().navItems)).toEqual(["base-b", "base-a"]);
    });

    it("is a no-op for an id that is not there", () => {
      const before = state().desktopRoutes;
      state().unregisterRoute("ghost");
      expect(state().desktopRoutes).toBe(before);
      expect(state().layers.desktopRoutes).toHaveLength(0);
    });

    it("unregisterService works for a base service", () => {
      state().unregisterService("agent-server");
      expect(state().services).toEqual([]);
    });
  });

  describe("capabilities", () => {
    it("keeps a layered override across hydrate", () => {
      state().registerCapabilities({ configureModelChannel: false });
      state().hydrate(otherProfile());
      expect(state().capabilities.configureModelChannel).toBe(false);
    });

    it("withdraws only its own override on dispose, in any order", () => {
      const first = state().registerCapabilities({
        configureModelChannel: false,
        managedRuntimeSetup: true,
      });
      const second = state().registerCapabilities({
        configureModelChannel: true,
      });
      expect(state().capabilities.configureModelChannel).toBe(true);
      expect(state().capabilities.managedRuntimeSetup).toBe(true);

      first();
      expect(state().capabilities.configureModelChannel).toBe(true);
      expect(state().capabilities.managedRuntimeSetup).toBe(false);

      second();
      expect(state().capabilities).toEqual(personalProfile.capabilities);
    });

    it("lets a layer win over setCapabilities while loaded, and shows the base once it unloads", () => {
      const dispose = state().registerCapabilities({
        configureModelChannel: false,
      });
      state().setCapabilities({ configureModelChannel: true });
      expect(state().capabilities.configureModelChannel).toBe(false);
      dispose();
      expect(state().capabilities.configureModelChannel).toBe(true);
    });

    it("resets setCapabilities on hydrate, as before", () => {
      state().setCapabilities({ viewTurnTokenUsage: false });
      state().hydrate(otherProfile());
      expect(state().capabilities.viewTurnTokenUsage).toBe(true);
    });

    it("hands out a new capabilities object only when something changed", () => {
      const before = state().capabilities;
      state().registerRoute(route("unrelated"));
      expect(state().capabilities).toBe(before);
    });
  });

  it("only re-derives the list a registration touched", () => {
    const { desktopRoutes, navItems } = state();
    state().registerSettingsSection(section("another"));
    expect(state().desktopRoutes).toBe(desktopRoutes);
    expect(state().navItems).toBe(navItems);
  });
});
