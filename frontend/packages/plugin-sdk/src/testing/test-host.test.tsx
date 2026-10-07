import { describe, expect, it } from "vitest";

import { definePlugin, pageRoute, settingsPage } from "../define";
import { useTranslation, useValuz } from "../scope";
import { createTestHost, mockValuz } from "./index";

const Tab = ({ projectId }: { projectId: string }) => {
  const { t } = useTranslation();
  return <section>{`${t("title")}:${projectId}`}</section>;
};
const Page = () => <h1>{useTranslation().t("title")}</h1>;

const plugin = definePlugin({
  id: "acme.dashboard",
  apply(ctx) {
    ctx.registry.slot("project.detail.tabs", {
      id: "positions",
      key: "positions",
      label: "title",
      component: Tab,
    });
    pageRoute(ctx, { id: "home", path: "/x/acme.dashboard", title: "title" }, Page, { nav: true });
    settingsPage(ctx, { id: "prefs", title: "title" }, Page);
  },
});

describe("createTestHost", () => {
  it("captures the registrations in the composition-snapshot format", async () => {
    const host = createTestHost({ locales: { "en-US": { title: "Positions" } } });
    await host.load(plugin);

    const capture = host.capture();
    expect(capture.slots).toEqual({ "project.detail.tabs": ["x:acme.dashboard:positions"] });
    expect(capture.routes).toEqual([
      {
        id: "x:acme.dashboard:home",
        path: "/x/acme.dashboard",
        label: "appPlugin.acme.dashboard.title",
        description: "appPlugin.acme.dashboard.title",
        layout: "project",
        showInNav: false,
        component: "[fn]",
        edition: "personal",
      },
    ]);
    expect(capture.settings[0]).toMatchObject({ id: "x:acme.dashboard:prefs", group: { id: "plugins" } });
    expect(capture.nav[0]).toMatchObject({ id: "x:acme.dashboard:home", href: "/x/acme.dashboard", icon: "plugins" });
  });

  it("renders slots, pages and settings to static markup", async () => {
    const host = createTestHost({
      locale: "zh-CN",
      locales: { "zh-CN": { title: "持仓" }, "en-US": { title: "Positions" } },
    });
    await host.load(plugin);

    expect(host.renderSlot("project.detail.tabs", { projectId: "p1", navigate: () => {} })).toBe(
      '<div data-valuz-app-plugin="acme.dashboard" style="display:contents"><section>持仓:p1</section></div>',
    );
    expect(host.renderRoute("home")).toContain("<h1>持仓</h1>");
    expect(host.renderRoute("/x/acme.dashboard")).toContain("<h1>持仓</h1>");
    expect(host.renderSettings("prefs")).toContain("<h1>持仓</h1>");
    expect(() => host.renderRoute("nope")).toThrow(/no page registered/);
  });

  it("uses the mocked client for ctx.valuz", async () => {
    const valuz = mockValuz({
      connectors: { "acme-data": { get_positions: () => ({ positions: [{ id: 1 }] }) } },
      automations: { "risk-summary": () => ({ status: "success", output: { risk: 3 } }) },
      writeTools: ["set_limit"],
      storage: { filter: "all" },
    });
    const host = createTestHost({ valuz });
    let seen: unknown;
    await host.load(
      definePlugin({
        id: "acme.dashboard",
        async apply(ctx) {
          const result = await ctx.valuz.connectors.callTool("acme-data", "get_positions");
          const { runId } = await ctx.valuz.automations.run("risk-summary", {});
          seen = {
            positions: result.structuredContent,
            run: await ctx.valuz.automations.waitRun(runId),
            latest: (await ctx.valuz.automations.latestRun("risk-summary"))?.runId,
            filter: await ctx.valuz.storage.get("filter"),
          };
        },
      }),
    );
    expect(seen).toMatchObject({
      positions: { positions: [{ id: 1 }] },
      run: { status: "success", output: { risk: 3 }, done: true },
      filter: "all",
    });
    expect(valuz.calls.map((call) => call.method)).toEqual([
      "connectors.callTool",
      "automations.run",
      "automations.waitRun",
      "automations.latestRun",
      "storage.get",
    ]);
  });

  it("mockValuz refuses a write tool without { write: true }", async () => {
    const valuz = mockValuz({ connectors: { c: { set_limit: () => ({}) } }, writeTools: ["set_limit"] });
    await expect(valuz.connectors.callTool("c", "set_limit")).rejects.toMatchObject({
      code: "write_tool_requires_confirmation",
    });
    await expect(valuz.connectors.callTool("c", "set_limit", {}, { write: true })).resolves.toBeTruthy();
    expect((await valuz.connectors.listTools("c"))[0]).toMatchObject({ readOnly: false });
  });

  it("a component that uses useValuz sees the mock", async () => {
    const valuz = mockValuz({ projects: [{ id: "p1", name: "One", kind: null, rootPath: null, icon: null }] });
    const host = createTestHost({ valuz });
    const Probe = () => <i>{typeof useValuz().projects.list}</i>;
    await host.load(
      definePlugin({
        id: "acme.dashboard",
        apply(ctx) {
          ctx.registry.slot("shell.topbar.actions", { id: "p", component: Probe });
        },
      }),
    );
    expect(host.renderSlot("shell.topbar.actions", {})).toContain("<i>function</i>");
  });
});
