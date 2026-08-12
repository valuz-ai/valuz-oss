import { fireEvent, render, screen } from "@testing-library/react";
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
        projectAddMenuItems={
          <DropdownMenuItem>组织内导入</DropdownMenuItem>
        }
      />,
    );

    fireEvent.pointerDown(screen.getByLabelText("添加项目"), {
      button: 0,
      ctrlKey: false,
    });

    expect(await screen.findByText("组织内导入")).toBeTruthy();
  });
});
