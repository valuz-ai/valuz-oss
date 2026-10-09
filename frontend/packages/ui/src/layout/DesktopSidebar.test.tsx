import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { DropdownMenuItem } from "../components/ui/dropdown-menu";
import { DesktopSidebar } from "./DesktopSidebar";

describe("DesktopSidebar", () => {
  it("nests a project's chats/tasks under it and renders the Chats group", () => {
    render(
      <DesktopSidebar
        // Active route is the project itself; the host resolves it to the owning
        // project id, which pins that project's accordion open.
        activePath="/projects/p1"
        activeProjectId="p1"
        projectGroups={[
          {
            id: "p1",
            label: "英伟达 2025 深度研究",
            href: "/projects/p1",
            items: [
              {
                id: "s1",
                title: "营收拆解",
                href: "/conversation/s1",
                kind: "chat",
              },
            ],
          },
        ]}
        chats={[
          {
            id: "c1",
            title: "临时问答",
            href: "/conversation/c1",
            kind: "chat",
          },
        ]}
        bottomItems={[
          {
            id: "knowledge",
            label: "知识库",
            href: "/knowledge",
            icon: "knowledge",
            group: "library",
          },
          {
            id: "skills",
            label: "技能库",
            href: "/skills",
            icon: "skills",
            group: "library",
          },
          {
            id: "scheduled",
            label: "定时任务",
            href: "/scheduled",
            icon: "scheduled",
            group: "main",
          },
          {
            id: "settings",
            label: "设置",
            href: "/settings",
            icon: "settings",
            group: "settings",
          },
        ]}
      />,
    );

    expect(screen.getByText("新对话")).toBeTruthy();
    expect(screen.getByText("项目")).toBeTruthy();
    expect(screen.getByText("英伟达 2025 深度研究")).toBeTruthy();
    // The active project auto-expands, so its nested chat is visible.
    expect(screen.getByText("营收拆解")).toBeTruthy();
    // Loose chats render in the "对话 / Chats" group.
    expect(screen.getByText("临时问答")).toBeTruthy();
    expect(screen.getByText("知识库")).toBeTruthy();
  });

  it("should hide section labels when collapsed", () => {
    render(
      <DesktopSidebar
        activePath="/projects"
        projectGroups={[
          {
            id: "p1",
            label: "Project",
            href: "/projects/p1",
          },
        ]}
        bottomItems={[
          {
            id: "knowledge",
            label: "知识库",
            href: "/knowledge",
            icon: "knowledge",
            group: "library",
          },
        ]}
        collapsed
      />,
    );

    expect(screen.queryByText("项目")).toBeNull();
    expect(screen.getAllByRole("link").length).toBeGreaterThan(0);
  });

  it("renders extension items in the project add dropdown", async () => {
    render(
      <DesktopSidebar
        activePath="/projects"
        projectGroups={[]}
        bottomItems={[]}
        onAddProject={() => {}}
        onImportProject={() => {}}
        projectAddMenuItems={<DropdownMenuItem>组织内导入</DropdownMenuItem>}
      />,
    );

    await userEvent.click(screen.getByLabelText("添加项目"));

    expect(await screen.findByText("组织内导入")).toBeTruthy();
  });

  const manyProjects = Array.from({ length: 12 }, (_, i) => ({
    id: `p${i + 1}`,
    label: `工作区 ${i + 1}`,
    href: `/projects/p${i + 1}`,
  }));

  it("caps the project list at five behind a show-more toggle", async () => {
    render(
      <DesktopSidebar
        activePath="/projects"
        projectGroups={manyProjects}
        bottomItems={[]}
      />,
    );

    expect(screen.getByText("工作区 5")).toBeTruthy();
    expect(screen.queryByText("工作区 6")).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "展开" }));

    expect(screen.getByText("工作区 12")).toBeTruthy();
    expect(screen.getByRole("button", { name: "收起" })).toBeTruthy();
  });

  it("holds the project list open when the active project sits past the cap", () => {
    render(
      <DesktopSidebar
        activePath="/projects/p6"
        activeProjectId="p6"
        projectGroups={manyProjects}
        bottomItems={[]}
      />,
    );

    expect(screen.getByText("工作区 6")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "展开" })).toBeNull();
    expect(screen.queryByRole("button", { name: "收起" })).toBeNull();
  });
});

describe("DesktopSidebar extension points", () => {
  const project = { id: "p1", label: "Alpha", href: "/projects/p1" };
  const chat = {
    id: "c1",
    title: "临时问答",
    href: "/conversation/c1",
    kind: "chat" as const,
  };
  const task = {
    id: "t1",
    title: "周报任务",
    href: "/tasks/t1",
    kind: "task" as const,
  };

  describe("projectMenuItems", () => {
    it("adds no '...' trigger to a project row with no actions at all", () => {
      render(
        <DesktopSidebar
          activePath="/"
          projectGroups={[project]}
          bottomItems={[]}
        />,
      );
      expect(screen.queryByLabelText("更多操作")).toBeNull();
    });

    it("counts toward the row's actions, so a row with only the slot gets the '...'", async () => {
      render(
        <DesktopSidebar
          activePath="/"
          projectGroups={[project]}
          bottomItems={[]}
          projectMenuItems={(id) => (
            <DropdownMenuItem>{`扩展项 ${id}`}</DropdownMenuItem>
          )}
        />,
      );
      await userEvent.click(screen.getByLabelText("更多操作"));
      expect(await screen.findByText("扩展项 p1")).toBeTruthy();
    });

    it("sits after Open-in-Finder and before Remove", async () => {
      render(
        <DesktopSidebar
          activePath="/"
          projectGroups={[project]}
          bottomItems={[]}
          onProjectOpenInFinder={() => {}}
          onProjectRemove={() => {}}
          projectMenuItems={() => <DropdownMenuItem>扩展项</DropdownMenuItem>}
        />,
      );
      await userEvent.click(screen.getByLabelText("更多操作"));
      await screen.findByText("扩展项");
      expect(
        screen.getAllByRole("menuitem").map((item) => item.textContent),
      ).toEqual(["在目录中打开", "扩展项", "移除"]);
    });

    it("leaves the menu exactly as before when the prop is omitted", async () => {
      render(
        <DesktopSidebar
          activePath="/"
          projectGroups={[project]}
          bottomItems={[]}
          onProjectOpenInFinder={() => {}}
          onProjectRemove={() => {}}
        />,
      );
      await userEvent.click(screen.getByLabelText("更多操作"));
      await screen.findByText("在目录中打开");
      expect(
        screen.getAllByRole("menuitem").map((item) => item.textContent),
      ).toEqual(["在目录中打开", "移除"]);
    });
  });

  describe("recentMenuItems", () => {
    it("adds no '...' trigger to chat rows when nothing provides an action", () => {
      render(
        <DesktopSidebar
          activePath="/"
          projectGroups={[]}
          bottomItems={[]}
          chats={[chat]}
        />,
      );
      expect(screen.queryByLabelText("更多操作")).toBeNull();
    });

    it("gives a chat row the '...' menu and passes the row to the render prop", async () => {
      const seen: string[] = [];
      render(
        <DesktopSidebar
          activePath="/"
          projectGroups={[]}
          bottomItems={[]}
          chats={[chat]}
          recentMenuItems={(item) => {
            seen.push(`${item.id}:${item.kind}:${item.href}`);
            return <DropdownMenuItem>{`会话扩展 ${item.id}`}</DropdownMenuItem>;
          }}
        />,
      );
      await userEvent.click(screen.getByLabelText("更多操作"));
      expect(await screen.findByText("会话扩展 c1")).toBeTruthy();
      expect(seen).toContain("c1:chat:/conversation/c1");
    });

    it("keeps the chats-only rule: task rows never get the menu", () => {
      render(
        <DesktopSidebar
          activePath="/"
          projectGroups={[]}
          bottomItems={[]}
          chats={[task]}
          recentMenuItems={() => <DropdownMenuItem>会话扩展</DropdownMenuItem>}
        />,
      );
      expect(screen.getByText("周报任务")).toBeTruthy();
      expect(screen.queryByLabelText("更多操作")).toBeNull();
    });

    it("sits after Fork and before Delete", async () => {
      render(
        <DesktopSidebar
          activePath="/"
          projectGroups={[]}
          bottomItems={[]}
          chats={[{ ...chat, canFork: true }]}
          onRecentRename={() => {}}
          onRecentFork={() => {}}
          onRecentDelete={() => {}}
          recentMenuItems={() => <DropdownMenuItem>会话扩展</DropdownMenuItem>}
        />,
      );
      await userEvent.click(screen.getByLabelText("更多操作"));
      await screen.findByText("会话扩展");
      expect(
        screen.getAllByRole("menuitem").map((item) => item.textContent),
      ).toEqual(["重命名", "Fork 会话", "会话扩展", "删除"]);
    });
  });

  describe("chatsActions", () => {
    it("renders beside the Chats label", () => {
      render(
        <DesktopSidebar
          activePath="/"
          projectGroups={[]}
          bottomItems={[]}
          chatsActions={<button type="button">聊天扩展动作</button>}
        />,
      );
      expect(screen.getByText("聊天扩展动作")).toBeTruthy();
    });

    it("adds nothing to the Chats label when omitted", () => {
      render(
        <DesktopSidebar
          activePath="/"
          projectGroups={[]}
          bottomItems={[]}
        />,
      );
      // The label row holds the toggle button only.
      const label = screen.getByText("对话").closest("div");
      expect(label?.children).toHaveLength(1);
    });
  });

  describe("sidebarSections", () => {
    it("renders after the Chats group in the expanded sidebar", () => {
      render(
        <DesktopSidebar
          activePath="/"
          projectGroups={[]}
          bottomItems={[]}
          chats={[chat]}
          sidebarSections={<section>扩展分区</section>}
        />,
      );
      const chats = screen.getByText("临时问答");
      const extra = screen.getByText("扩展分区");
      expect(
        chats.compareDocumentPosition(extra) &
          Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
    });

    it("is not rendered in the collapsed rail", () => {
      render(
        <DesktopSidebar
          activePath="/"
          projectGroups={[]}
          bottomItems={[]}
          collapsed
          sidebarSections={<section>扩展分区</section>}
        />,
      );
      expect(screen.queryByText("扩展分区")).toBeNull();
    });
  });
});
