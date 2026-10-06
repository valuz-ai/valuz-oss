import { describe, expect, it } from "vitest";

import { definePlugin, pageRoute, settingsPage, sidebarItem, slotComponent } from "../define";
import { createTestHost, PluginContractError } from "../testing";
import type { PluginDefinition } from "../types";
import { adaptThirdPartyPlugin, isPublicSlot } from "./adapt";
import type { HostServices } from "../services";

const Noop = () => <span>noop</span>;

const plugin = (
  apply: PluginDefinition["apply"],
  id = "acme.dashboard",
): PluginDefinition => definePlugin({ id, apply });

describe("public slots", () => {
  it("accepts the 12 public slots, with the two families by prefix", () => {
    for (const name of [
      "conversation.title.actions",
      "conversation.header.badges",
      "conversation.turn.actions",
      "conversation.tool-card.get_quote",
      "conversation.composer.plus.menu-items",
      "conversation.empty.extra",
      "context-panel.tabs",
      "project.detail.tabs",
      "project.detail.header.actions",
      "resource.skill.actions",
      "resource.connector.actions",
      "shell.topbar.actions",
      "task.detail.sections",
    ]) {
      expect(isPublicSlot(name), name).toBe(true);
    }
  });

  it("refuses everything else", () => {
    for (const name of [
      "shell.brand.mark",
      "conversation.tool-call",
      "conversation.message.user",
      "conversation.message.assistant",
      "conversation.approval.card",
      "conversation.tool-card.",
      "resource.skill.list.actions",
      "resource.skill.detail.actions",
      "settings.header",
      "onboarding.header.actions",
      "sidebar.footer",
    ]) {
      expect(isPublicSlot(name), name).toBe(false);
    }
  });
});

describe("the restricted context", () => {
  it("registers a public slot under a namespaced id", async () => {
    const host = createTestHost();
    await host.load(
      plugin((ctx) => {
        ctx.registry.slot("conversation.title.actions", {
          id: "count",
          component: slotComponent(Noop),
        });
        ctx.registry.slot("conversation.tool-card.get_quote", {
          id: "quote",
          component: Noop,
        });
      }),
    );

    expect(host.capture().slots).toEqual({
      "conversation.title.actions": ["x:acme.dashboard:count"],
      "conversation.tool-card.get_quote": ["x:acme.dashboard:quote"],
    });
  });

  it("throws, as a contract violation, for a slot that is not public", async () => {
    const host = createTestHost();
    await expect(
      host.load(
        plugin((ctx) => {
          ctx.registry.slot("shell.brand.mark" as "shell.topbar.actions", {
            id: "x",
            component: Noop,
          });
        }),
      ),
    ).rejects.toBeInstanceOf(PluginContractError);
    expect(host.capture().slots).toEqual({});
  });

  it("rolls back what a failing plugin had registered before it threw", async () => {
    const host = createTestHost();
    await expect(
      host.load(
        plugin((ctx) => {
          ctx.registry.slot("shell.topbar.actions", { id: "ok", component: Noop });
          pageRoute(ctx, { id: "home", path: "/x/acme.dashboard", title: "Home" }, Noop);
          throw new Error("boom");
        }),
      ),
    ).rejects.toThrow("boom");

    expect(host.capture()).toEqual({ slots: {}, routes: [], settings: [], nav: [] });
  });

  it("requires a key for the keyed slots and namespaces it", async () => {
    const host = createTestHost({
      locales: { "en-US": { positions: "Positions" } },
    });
    await expect(
      host.load(
        plugin((ctx) => {
          ctx.registry.slot("project.detail.tabs", { id: "tab", component: Noop });
        }),
      ),
    ).rejects.toThrow(/key is required/);

    await host.load(
      plugin((ctx) => {
        ctx.registry.slot("project.detail.tabs", {
          id: "tab",
          key: "positions",
          label: "positions",
          component: Noop,
        });
      }),
    );
    expect(host.capture().slots["project.detail.tabs"]).toEqual([
      "x:acme.dashboard:tab",
    ]);
  });

  it("only allows pages under /x/<plugin id>/", async () => {
    for (const path of [
      "/settings",
      "/x/other.plugin",
      "/x/acme.dashboardx",
      "/x/acme.dashboard/../settings",
      "/x/acme.dashboard?x=1",
      "/",
    ]) {
      const host = createTestHost();
      await expect(
        host.load(
          plugin((ctx) => {
            pageRoute(ctx, { id: "home", path, title: "Home" }, Noop);
          }),
        ),
        path,
      ).rejects.toBeInstanceOf(PluginContractError);
    }

    const host = createTestHost();
    await host.load(
      plugin((ctx) => {
        pageRoute(ctx, { id: "home", path: "/x/acme.dashboard", title: "Home" }, Noop, { nav: true });
        pageRoute(ctx, { id: "detail", path: "/x/acme.dashboard/detail", title: "Detail" }, Noop);
      }),
    );
    const capture = host.capture();
    expect(capture.routes.map((route) => route.id)).toEqual([
      "x:acme.dashboard:home",
      "x:acme.dashboard:detail",
    ]);
    expect(capture.routes[0]).toMatchObject({
      path: "/x/acme.dashboard",
      layout: "project",
      showInNav: false,
      component: "[fn]",
    });
    expect(capture.nav).toEqual([
      expect.objectContaining({ id: "x:acme.dashboard:home", href: "/x/acme.dashboard" }),
    ]);
  });

  it("puts settings sections in the Extensions group", async () => {
    const host = createTestHost();
    await host.load(
      plugin((ctx) => {
        settingsPage(ctx, { id: "prefs", title: "Prefs" }, Noop);
      }),
    );
    expect(host.capture().settings).toEqual([
      expect.objectContaining({
        id: "x:acme.dashboard:prefs",
        group: { id: "extensions", label: "extensions.title" },
      }),
    ]);
  });

  it("only lets a nav item point at the plugin's own pages", async () => {
    const host = createTestHost();
    await expect(
      host.load(
        plugin((ctx) => {
          sidebarItem(ctx, { id: "out", label: "Out", icon: "star", path: "/settings" });
        }),
      ),
    ).rejects.toBeInstanceOf(PluginContractError);
  });

  it("refuses the first-party-only registry methods and ctx.legacy", async () => {
    for (const method of [
      "removeRoute",
      "removeSettingsSection",
      "removeNavItem",
      "projectPanel",
      "navGroup",
      "service",
      "categories",
      "suppress",
      "capabilities",
    ]) {
      const host = createTestHost();
      await expect(
        host.load(
          plugin((ctx) => {
            (ctx.registry as unknown as Record<string, (...a: unknown[]) => void>)[
              method
            ]!("x");
          }),
        ),
        method,
      ).rejects.toThrow(/not available to third-party plugins/);
    }
    const host = createTestHost();
    await expect(
      host.load(
        plugin((ctx) => {
          (ctx as unknown as { legacy: (...a: unknown[]) => void }).legacy("x", () => {});
        }),
      ),
    ).rejects.toBeInstanceOf(PluginContractError);
  });

  it("checks the definition against the manifest id", async () => {
    const host = createTestHost();
    await expect(
      host.load(plugin(() => {}, "acme.dashboard"), { id: "acme.other" }),
    ).rejects.toThrow(/does not match the manifest id/);
    await expect(
      host.load({ id: "acme.dashboard" } as unknown as PluginDefinition),
    ).rejects.toThrow(/default-export/);
  });

  it("holds resources with effect() and releases them on unload", async () => {
    const host = createTestHost();
    const events: string[] = [];
    await host.load(
      plugin((ctx) => {
        ctx.effect(() => {
          events.push("up");
          return () => events.push("down");
        });
        ctx.registry.slot("shell.topbar.actions", { id: "a", component: Noop });
      }),
    );
    expect(events).toEqual(["up"]);
    await host.unload();
    expect(events).toEqual(["up", "down"]);
    expect(host.capture().slots).toEqual({});
  });

  it("applies the same restrictions to a child plugin", async () => {
    const host = createTestHost();
    await host.load(
      plugin(async (ctx) => {
        await ctx.plugin(
          definePlugin({
            id: "child",
            apply(child) {
              child.registry.slot("shell.topbar.actions", { id: "c", component: Noop });
            },
          }),
        );
      }),
    );
    expect(host.capture().slots["shell.topbar.actions"]).toEqual(["x:acme.dashboard:c"]);

    const other = createTestHost();
    await expect(
      other.load(
        plugin(async (ctx) => {
          await ctx.plugin(
            definePlugin({
              id: "child",
              apply(child) {
                child.registry.slot("shell.brand.mark" as "shell.topbar.actions", {
                  id: "c",
                  component: Noop,
                });
              },
            }),
          );
        }),
      ),
    ).rejects.toBeInstanceOf(PluginContractError);
  });

  it("exposes the plugin id, config and log on ctx", async () => {
    const host = createTestHost({ config: { region: "cn" } });
    let seen: unknown;
    await host.load(
      plugin((ctx) => {
        seen = { id: ctx.pluginId, config: ctx.config, hasLog: typeof ctx.log.info };
      }),
    );
    expect(seen).toEqual({
      id: "acme.dashboard",
      config: { region: "cn" },
      hasLog: "function",
    });
  });
});

describe("adaptThirdPartyPlugin", () => {
  const services = {
    request: async () => ({}),
    registerLocales: () => {},
    getHostContext: () => ({
      locale: "en-US",
      theme: "light",
      edition: "personal",
      deployment: "local",
      orgId: null,
    }),
    getLocale: () => "en-US",
    subscribeLocale: () => () => {},
  } as unknown as HostServices;

  it("builds a first-party plugin with the manifest id", () => {
    const adapted = adaptThirdPartyPlugin(
      plugin(() => {}),
      { id: "acme.dashboard" },
      services,
    );
    expect(adapted.id).toBe("acme.dashboard");
    expect(typeof adapted.apply).toBe("function");
    expect(adapted.scope.pluginId).toBe("acme.dashboard");
  });
});
