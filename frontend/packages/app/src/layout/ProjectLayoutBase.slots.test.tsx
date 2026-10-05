import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { initI18n } from "@valuz/shared/i18n";
import { DropdownMenuItem } from "@valuz/ui";
import {
  useProjectStore,
  useRegistryStore,
  useSessionStore,
  useTaskStore,
} from "@valuz/core";

vi.mock("@valuz/core", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@valuz/core")>();
  return {
    ...actual,
    runsApi: {
      ...actual.runsApi,
      list: vi.fn().mockResolvedValue({
        runs: [
          {
            session_id: "s1",
            title: "第一个会话",
            source_kind: "chat",
            origin: "user",
            project_id: null,
            runtime: "claude_agent",
            updated_at: 10,
            status: "finished",
          },
        ],
      }),
    },
    projectsApi: {
      ...actual.projectsApi,
      list: vi.fn().mockResolvedValue({
        projects: [
          {
            id: "p1",
            name: "Alpha",
            kind: "project",
            root_path: "/tmp/alpha",
          },
        ],
      }),
    },
    subscribeUserStream: vi.fn(() => () => {}),
  };
});

import type { ReactNode } from "react";
import { ProjectLayoutBase } from "./ProjectLayoutBase";

type Registration = Parameters<
  ReturnType<typeof useRegistryStore.getState>["registerSlot"]
>[1];
const register = (name: string, registration: Registration) =>
  useRegistryStore.getState().registerSlot(name, registration);

const renderLayout = (
  props: { sidebarFooter?: ReactNode; sidebarHeader?: ReactNode } = {},
  path = "/conversation/new",
) =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route element={<ProjectLayoutBase logoSrc="/logo.png" {...props} />}>
          <Route path="*" element={<div>页面内容</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );

const before = (a: Node, b: Node) =>
  Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);

const menuLabels = () =>
  screen.getAllByRole("menuitem").map((item) => item.textContent);

const openBrandMenu = async () => {
  await userEvent.click(screen.getByRole("button", { name: /menu$/ }));
  await screen.findAllByRole("menuitem");
};

const openRowMenu = async (rowText: string) => {
  const row = (await screen.findByText(rowText)).closest("a") as HTMLElement;
  await userEvent.click(within(row).getByLabelText("更多操作"));
  await screen.findAllByRole("menuitem");
};

beforeAll(() => initI18n({ locale: "zh-CN", fallbackLocale: "zh-CN" }));
beforeEach(() => {
  useRegistryStore.setState({ slots: {} });
  useProjectStore.setState({ projects: [] });
  // The sidebar rails fetch over HTTP on mount; there is no backend here.
  useSessionStore.setState({ fetchSessions: vi.fn().mockResolvedValue(undefined) });
  useTaskStore.setState({ fetchAllTasks: vi.fn().mockResolvedValue(undefined) });
});

describe("ProjectLayoutBase with every slot empty", () => {
  it("renders the shell, the plain logo and the project rail", async () => {
    renderLayout();
    expect(await screen.findByText("页面内容")).toBeTruthy();
    expect(screen.getByAltText("Valuz").getAttribute("src")).toBe("/logo.png");
    expect(await screen.findByText("Alpha")).toBeTruthy();
    expect(await screen.findByText("第一个会话")).toBeTruthy();
  });

  it("keeps the brand menu as it was: one separator, no extra items", async () => {
    renderLayout();
    await openBrandMenu();
    expect(menuLabels()).toEqual(["首页", "新对话", "设置", "帮助"]);
    expect(screen.getAllByRole("separator")).toHaveLength(1);
  });

  it("keeps the project row menu as it was", async () => {
    renderLayout();
    await openRowMenu("Alpha");
    expect(menuLabels()).toEqual([
      "重命名",
      "导出项目",
      "在目录中打开",
      "移除",
    ]);
  });

  it("keeps the chat row menu as it was", async () => {
    renderLayout();
    await openRowMenu("第一个会话");
    expect(menuLabels()).toEqual(["重命名", "Fork 会话", "删除"]);
  });
});

describe("ProjectLayoutBase slots", () => {
  it("shell.brand.mark replaces the logo image and gets appName / logoSrc", async () => {
    let seen: { appName?: string; logoSrc?: string } = {};
    register("shell.brand.mark", {
      id: "test-mark",
      component: (props: { appName?: string; logoSrc?: string }) => {
        seen = props;
        return <span>品牌标记</span>;
      },
    });
    renderLayout();
    expect(await screen.findByText("品牌标记")).toBeTruthy();
    expect(screen.queryByAltText("Valuz")).toBeNull();
    expect(seen.logoSrc).toBe("/logo.png");
    expect(typeof seen.appName).toBe("string");
  });

  it("shell.brand.mark can keep the stock frame and swap only the image", async () => {
    register("shell.brand.mark", {
      id: "test-mark",
      component: ({ renderDefault }: { renderDefault?: (o: Record<string, unknown>) => ReactNode }) =>
        renderDefault?.({ logoSrc: "/plugin-logo.png" }),
    });
    renderLayout();
    // Same <img> the host draws (alt, classes), with the plugin's source.
    const img = await screen.findByAltText("Valuz");
    expect(img.getAttribute("src")).toBe("/plugin-logo.png");
    expect(img.className).toContain("object-contain");
  });

  it("shell.brand.menu-items adds a separator and items after Help, with navigate / platform", async () => {
    let seen: { navigate?: unknown; platform?: { isElectron?: boolean } } = {};
    register("shell.brand.menu-items", {
      id: "test-brand-item",
      component: (props: typeof seen) => {
        seen = props;
        return <DropdownMenuItem>品牌菜单扩展</DropdownMenuItem>;
      },
    });
    renderLayout();
    await openBrandMenu();
    expect(menuLabels()).toEqual([
      "首页",
      "新对话",
      "设置",
      "帮助",
      "品牌菜单扩展",
    ]);
    expect(screen.getAllByRole("separator")).toHaveLength(2);
    expect(typeof seen.navigate).toBe("function");
    expect(seen.platform?.isElectron).toBe(false);
  });

  it("shell.topbar.leading renders in the top bar with pathname / platform", async () => {
    let seen: { pathname?: string; platform?: unknown } = {};
    register("shell.topbar.leading", {
      id: "test-leading",
      component: (props: typeof seen) => {
        seen = props;
        return <span>顶栏左侧扩展</span>;
      },
    });
    renderLayout();
    const leading = await screen.findByText("顶栏左侧扩展");
    // Same cluster as the back / forward buttons.
    expect(leading.parentElement).toBe(
      screen.getByLabelText("后退").parentElement?.parentElement,
    );
    expect(seen.pathname).toBe("/conversation/new");
    expect(seen.platform).toBeTruthy();
  });

  it("shell.topbar.actions renders before the panel toggles with pathname / activeProjectId / rightPanelCollapsed", async () => {
    let seen: {
      pathname?: string;
      activeProjectId?: string | null;
      rightPanelCollapsed?: boolean;
    } = {};
    register("shell.topbar.actions", {
      id: "test-actions",
      component: (props: typeof seen) => {
        seen = props;
        return <span>顶栏右侧扩展</span>;
      },
    });
    renderLayout();
    expect(await screen.findByText("顶栏右侧扩展")).toBeTruthy();
    expect(seen.pathname).toBe("/conversation/new");
    expect(seen.activeProjectId).toBeNull();
    expect(typeof seen.rightPanelCollapsed).toBe("boolean");
  });

  it("shell.notice renders at the top of the main card with pathname / activeProjectId", async () => {
    let seen: { pathname?: string; activeProjectId?: string | null } = {};
    register("shell.notice", {
      id: "test-notice",
      component: (props: typeof seen) => {
        seen = props;
        return <div>配额提示</div>;
      },
    });
    const { container } = renderLayout();
    const notice = await screen.findByText("配额提示");
    expect(container.querySelector("main")?.firstElementChild).toBe(notice);
    expect(seen.pathname).toBe("/conversation/new");
    expect(seen.activeProjectId).toBeNull();
  });

  it("shell.overlay renders after the toaster with pathname / navigate", async () => {
    let seen: { pathname?: string; navigate?: unknown } = {};
    register("shell.overlay", {
      id: "test-overlay",
      component: (props: typeof seen) => {
        seen = props;
        return <div>全局浮层</div>;
      },
    });
    renderLayout();
    expect(await screen.findByText("全局浮层")).toBeTruthy();
    expect(seen.pathname).toBe("/conversation/new");
    expect(typeof seen.navigate).toBe("function");
  });

  it("sidebar.header renders after the host's own header with collapsed / activePath", async () => {
    let seen: { collapsed?: boolean; activePath?: string } = {};
    register("sidebar.header", {
      id: "test-sb-header",
      component: (props: typeof seen) => {
        seen = props;
        return <div>侧栏头部扩展</div>;
      },
    });
    renderLayout({ sidebarHeader: <div>原有头部</div> });
    const ext = await screen.findByText("侧栏头部扩展");
    expect(before(screen.getByText("原有头部"), ext)).toBe(true);
    expect(seen).toEqual({ collapsed: false, activePath: "/conversation/new" });
  });

  it("sidebar.nav.items renders after the nav rows with collapsed / activePath", async () => {
    let seen: { collapsed?: boolean; activePath?: string } = {};
    register("sidebar.nav.items", {
      id: "test-sb-nav",
      component: (props: typeof seen) => {
        seen = props;
        return <div>侧栏导航扩展</div>;
      },
    });
    renderLayout();
    const ext = await screen.findByText("侧栏导航扩展");
    expect(before(screen.getByText("新对话", { selector: "span" }), ext)).toBe(
      true,
    );
    expect(seen).toEqual({ collapsed: false, activePath: "/conversation/new" });
  });

  it("sidebar.footer renders after the host's own footer, passing collapsed", async () => {
    let seen: { collapsed?: boolean } = {};
    register("sidebar.footer", {
      id: "test-sb-footer",
      component: (props: typeof seen) => {
        seen = props;
        return <div>侧栏页脚扩展</div>;
      },
    });
    renderLayout({ sidebarFooter: <div>原有页脚</div> });
    const ext = await screen.findByText("侧栏页脚扩展");
    expect(before(screen.getByText("原有页脚"), ext)).toBe(true);
    expect(seen).toEqual({ collapsed: false });
  });

  it("keeps the host's own sidebar header and footer when their slots are empty", async () => {
    renderLayout({
      sidebarHeader: <div>原有头部</div>,
      sidebarFooter: <div>原有页脚</div>,
    });
    expect(await screen.findByText("原有头部")).toBeTruthy();
    expect(screen.getByText("原有页脚")).toBeTruthy();
  });

  it("sidebar.chats.actions renders on the Chats label", async () => {
    register("sidebar.chats.actions", {
      id: "test-chats-actions",
      component: () => <button type="button">对话动作</button>,
    });
    renderLayout();
    const action = await screen.findByRole("button", { name: "对话动作" });
    expect(action.parentElement).toBe(
      screen.getByText("对话", { selector: "span" }).closest("div"),
    );
  });

  it("sidebar.sections renders after the Chats group with activePath", async () => {
    let seen: { activePath?: string } = {};
    register("sidebar.sections", {
      id: "test-sections",
      component: (props: typeof seen) => {
        seen = props;
        return <div>侧栏分区扩展</div>;
      },
    });
    renderLayout();
    const ext = await screen.findByText("侧栏分区扩展");
    expect(before(await screen.findByText("第一个会话"), ext)).toBe(true);
    expect(seen).toEqual({ activePath: "/conversation/new" });
  });

  it("sidebar.project.menu-items sits between Open-in-Finder and Remove with projectId / project / navigate", async () => {
    let seen: {
      projectId?: string;
      project?: { id: string; name: string };
      navigate?: unknown;
    } = {};
    register("sidebar.project.menu-items", {
      id: "test-project-item",
      component: (props: typeof seen) => {
        seen = props;
        return <DropdownMenuItem>项目菜单扩展</DropdownMenuItem>;
      },
    });
    renderLayout();
    await openRowMenu("Alpha");
    expect(menuLabels()).toEqual([
      "重命名",
      "导出项目",
      "在目录中打开",
      "项目菜单扩展",
      "移除",
    ]);
    expect(seen.projectId).toBe("p1");
    expect(seen.project?.name).toBe("Alpha");
    expect(typeof seen.navigate).toBe("function");
  });

  it("sidebar.session.menu-items sits between Fork and Delete with sessionId / kind / href / isRunning", async () => {
    let seen: {
      sessionId?: string;
      kind?: string;
      href?: string;
      isRunning?: boolean;
    } = {};
    register("sidebar.session.menu-items", {
      id: "test-session-item",
      component: (props: typeof seen) => {
        seen = props;
        return <DropdownMenuItem>会话菜单扩展</DropdownMenuItem>;
      },
    });
    renderLayout();
    await openRowMenu("第一个会话");
    expect(menuLabels()).toEqual([
      "重命名",
      "Fork 会话",
      "会话菜单扩展",
      "删除",
    ]);
    expect(seen).toEqual({
      sessionId: "s1",
      kind: "chat",
      href: "/conversation/s1",
      isRunning: false,
    });
  });
});
