import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { createPluginHost, definePlugin, useRegistryStore } from "@valuz/core";

import { composeOss } from "../plugins/testing/compose-oss";
import {
  getResolvedDesktopRoutes,
  registerRouteComponent,
  resolveRoutes,
} from "./route-registry";

const Noop = () => null;
const Other = () => null;

const route = (id: string) => ({
  id,
  path: `/${id}`,
  label: id,
  description: id,
  layout: "project" as const,
  showInNav: false,
  edition: "personal" as const,
});

beforeAll(async () => {
  await composeOss();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("route registry", () => {
  it("resolves the shared hidden component Gallery route", () => {
    const resolved = getResolvedDesktopRoutes().find(
      ({ id }) => id === "component-gallery",
    );

    expect(resolved).toMatchObject({
      path: "/developer/components",
      showInNav: false,
    });
    expect(resolved?.Component).toBeDefined();
  });

  it("resolves every route the OSS plugins registered to a component", () => {
    const registered = useRegistryStore.getState().desktopRoutes;

    expect(getResolvedDesktopRoutes().map((r) => r.id)).toEqual(
      registered.map((r) => r.id),
    );
  });

  it("skips a route nothing supplies a component for, with a warning", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});

    expect(resolveRoutes([route("no-page-for-this")])).toEqual([]);
    expect(warn).toHaveBeenCalledWith(
      expect.stringContaining('No component registered for "no-page-for-this"'),
    );
  });

  it("prefers a route's own component over a registered one, and restores the registered one", () => {
    const dispose = registerRouteComponent("dual", Noop);

    expect(resolveRoutes([route("dual")])[0].Component).toBe(Noop);
    expect(
      resolveRoutes([{ ...route("dual"), component: Other }])[0].Component,
    ).toBe(Other);

    vi.spyOn(console, "warn").mockImplementation(() => {});
    dispose();
    expect(resolveRoutes([route("dual")])).toEqual([]);
  });

  it("drops a plugin's page when the plugin unloads", async () => {
    const host = createPluginHost();
    await host.load(
      definePlugin({
        id: "page-plugin",
        apply(ctx) {
          ctx.effect(() => registerRouteComponent("page-plugin", Noop));
          ctx.registry.route(route("page-plugin"));
        },
      }),
    );
    expect(getResolvedDesktopRoutes().map((r) => r.id)).toContain(
      "page-plugin",
    );

    await host.unload("page-plugin");
    expect(getResolvedDesktopRoutes().map((r) => r.id)).not.toContain(
      "page-plugin",
    );
  });
});
