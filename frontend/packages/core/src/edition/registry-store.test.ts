import { beforeEach, describe, expect, it } from "vitest";
import { personalProfile } from "./personal-profile";
import { useRegistryStore } from "./registry-store";
import { registerPlugin } from "./plugin";

describe("registry store", () => {
  beforeEach(() => {
    useRegistryStore.getState().hydrate(personalProfile);
  });

  it("starts seeded from the personal profile", () => {
    const state = useRegistryStore.getState();
    expect(state.edition).toBe("personal");
    expect(state.desktopRoutes.map((route) => route.id)).toContain("settings");
    expect(state.services.map((service) => service.name)).toEqual([
      "agent-server",
    ]);
  });

  it("registers a runtime route and lets callers dispose it", () => {
    const dispose = useRegistryStore.getState().registerRoute({
      id: "plugin-route",
      path: "/plugin",
      label: "Plugin",
      description: "Runtime-added route",
      layout: "workspace",
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

  // 原 'edition hot-swap' 测试已删除：Slice 3 把 enterpriseProfile 从公共骨架移除，
  // 当前 setEdition 永远 reseed 为 personalProfile。未来如果真引入 enterprise overlay，
  // 由 overlay 直接 hydrate() 即可，不再走 setEdition('enterprise')。
});

describe("registerPlugin", () => {
  beforeEach(() => {
    useRegistryStore.getState().hydrate(personalProfile);
  });

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
          layout: "workspace",
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
