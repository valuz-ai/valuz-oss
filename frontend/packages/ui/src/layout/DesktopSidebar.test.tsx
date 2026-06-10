import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DesktopSidebar } from "./DesktopSidebar";

describe("DesktopSidebar", () => {
  it("should render the project rows when projectGroups are provided", () => {
    render(
      <DesktopSidebar
        activePath="/knowledge"
        projectGroups={[
          {
            id: "p1",
            label: "英伟达 2025 深度研究",
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
            group: "project",
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

    expect(screen.getByText("+ 新对话")).toBeTruthy();
    expect(screen.getByText("项目")).toBeTruthy();
    expect(screen.getByText("英伟达 2025 深度研究")).toBeTruthy();
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
});
