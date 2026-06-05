import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeAll, describe, expect, it, vi } from "vitest";
import {
  ProjectDetailContextPanel,
  type TodoListItem,
} from "./ProjectContextPanel";

const todos: TodoListItem[] = [
  {
    content: "Plan migration",
    status: "in_progress",
    activeForm: "Planning migration",
  },
  { content: "Write code", status: "pending" },
  { content: "Ship feature", status: "completed" },
];

beforeAll(() => {
  if (!globalThis.ResizeObserver) {
    globalThis.ResizeObserver = class ResizeObserver {
      observe() {}
      unobserve() {}
      disconnect() {}
    };
  }
});

describe("ProjectDetailContextPanel — Todos section", () => {
  it("should render the agent's TODO list with the X/Y done counter when todos are provided", () => {
    render(<ProjectDetailContextPanel todos={todos} />);

    // Section header is present
    expect(screen.getByText("待办事项")).toBeTruthy();
    // 1 of 3 done
    expect(screen.getByText("1/3")).toBeTruthy();
    // in_progress row uses ``activeForm`` (the gerund), other rows use ``content``
    expect(screen.getByText("Planning migration")).toBeTruthy();
    expect(screen.getByText("Write code")).toBeTruthy();
    expect(screen.getByText("Ship feature")).toBeTruthy();
  });

  it("should render the Todos empty state when the agent has not produced any todos yet", () => {
    render(<ProjectDetailContextPanel todos={null} />);
    expect(screen.getByText("待办事项")).toBeTruthy();
    expect(screen.getByText("暂无待办")).toBeTruthy();
  });

  it("should render the Todos empty state when the todos array is empty", () => {
    // An empty array is the kernel's "all done — list cleared" signal;
    // panel UX keeps the section stable and shows an empty state.
    render(<ProjectDetailContextPanel todos={[]} />);
    expect(screen.getByText("待办事项")).toBeTruthy();
    expect(screen.getByText("暂无待办")).toBeTruthy();
  });

  it("should fall back to content when activeForm is omitted on an in_progress row", () => {
    // DeepAgents does not emit activeForm — make sure we don't render
    // ``undefined`` or blank text in that case.
    const noActiveForm: TodoListItem[] = [
      { content: "Run benchmarks", status: "in_progress" },
    ];
    render(<ProjectDetailContextPanel todos={noActiveForm} />);
    expect(screen.getByText("Run benchmarks")).toBeTruthy();
  });

  it("should render uploaded file type icons with the generated-file icon styling", () => {
    render(
      <ProjectDetailContextPanel
        uploadedFiles={[
          { id: "1", name: "notes.md" },
          { id: "2", name: "preview.png" },
          { id: "3", name: "index.html" },
        ]}
      />,
    );

    expect(screen.getByText("notes.md")).toBeTruthy();
    expect(screen.getByText("preview.png")).toBeTruthy();
    expect(screen.getByText("index.html")).toBeTruthy();
    expect(screen.getAllByTestId("uploaded-file-type-icon")).toHaveLength(3);
  });

  it("should render an empty state when the uploaded files section is empty", () => {
    render(<ProjectDetailContextPanel uploadedFiles={[]} />);

    expect(screen.getByText("暂无上传文件")).toBeTruthy();
    expect(screen.queryByText("release-notes.md")).toBeNull();
  });

  it("should keep files in the context accordion by default", () => {
    render(
      <ProjectDetailContextPanel
        fileTree={[
          {
            name: "src",
            type: "folder",
            path: "src",
            children: [
              {
                name: "main.ts",
                type: "file",
                path: "src/main.ts",
              },
            ],
          },
        ]}
        rootPath="/tmp/project"
      />,
    );

    expect(screen.queryByRole("tab", { name: "Session Context" })).toBeNull();
    expect(screen.queryByRole("tab", { name: "项目文件" })).toBeNull();
    expect(screen.getByText("/tmp/project")).toBeTruthy();
    expect(screen.getByText("项目文件")).toBeTruthy();
  });

  it("should move project files into a separate tab when enabled", async () => {
    render(
      <ProjectDetailContextPanel
        fileTreeInTab
        fileTree={[
          {
            name: "src",
            type: "folder",
            path: "src",
            children: [
              {
                name: "main.ts",
                type: "file",
                path: "src/main.ts",
              },
            ],
          },
        ]}
        rootPath="/tmp/project"
      />,
    );

    expect(screen.getByRole("tab", { name: "Session Context" })).toBeTruthy();
    const filesTab = screen.getByRole("tab", { name: "项目文件" });
    expect(filesTab).toBeTruthy();
    expect(screen.queryByText("/tmp/project")).toBeNull();

    await userEvent.click(filesTab);

    expect(screen.getByText("/tmp/project")).toBeTruthy();
    expect(screen.getByText("src")).toBeTruthy();
  });

  it("should hide project edit and skill management actions when requested", () => {
    render(
      <ProjectDetailContextPanel
        instructions=""
        onInstructionsChange={() => {}}
        skills={[]}
        onAddSkill={() => {}}
        onCreateProjectSkill={() => {}}
        onManageGlobalSkills={() => {}}
        hideProjectContextActions
      />,
    );

    expect(screen.queryByText("编写提示词")).toBeNull();
    expect(screen.queryByText("+ 从 User 添加")).toBeNull();
    expect(screen.queryByText("+ 创建项目 Skill")).toBeNull();
    expect(screen.queryByText("管理全局 Skill")).toBeNull();
  });

  it("should edit instructions from the section header icon", async () => {
    render(
      <ProjectDetailContextPanel
        instructions=""
        onInstructionsChange={() => {}}
      />,
    );

    expect(screen.queryByText("编写提示词")).toBeNull();

    await userEvent.click(screen.getByTitle("编写提示词"));

    expect(screen.getByRole("textbox")).toBeTruthy();
  });

  it("should hide footer management links in project context sections", () => {
    render(
      <ProjectDetailContextPanel
        skills={[]}
        onAddSkill={() => {}}
        onCreateProjectSkill={() => {}}
        onManageGlobalSkills={() => {}}
        scheduledTasks={[]}
        onAddScheduledTask={() => {}}
        onManageScheduledTasks={() => {}}
        docs={[]}
        onManageGlobalDocs={() => {}}
      />,
    );

    expect(screen.queryByText("+ 从 User 添加")).toBeNull();
    expect(screen.queryByText("+ 创建项目 Skill")).toBeNull();
    expect(screen.queryByText("管理全局 Skill")).toBeNull();
    expect(screen.queryByText("+ 添加定时任务")).toBeNull();
    expect(screen.queryByText("管理")).toBeNull();
    expect(screen.queryByText("管理全局知识库")).toBeNull();
  });

  it("should label the project skill section and release focus after opening the picker", async () => {
    const onAddSkill = vi.fn();
    render(<ProjectDetailContextPanel skills={[]} onAddSkill={onAddSkill} />);

    expect(screen.getByRole("button", { name: /技能/ })).toBeTruthy();

    await userEvent.click(screen.getByTitle("添加 Skill"));

    expect(onAddSkill).toHaveBeenCalledTimes(1);
    expect(document.activeElement).toBe(document.body);
  });

  it("should add scheduled tasks from the section header icon", async () => {
    const onAddScheduledTask = vi.fn();
    render(
      <ProjectDetailContextPanel
        scheduledTasks={[]}
        onAddScheduledTask={onAddScheduledTask}
      />,
    );

    await userEvent.click(screen.getByTitle("添加定时任务"));

    expect(onAddScheduledTask).toHaveBeenCalledTimes(1);
  });

  it("should keep only one context section open at a time", async () => {
    render(
      <ProjectDetailContextPanel
        instructions="Project prompt"
        onInstructionsChange={() => {}}
        scheduledTasks={[
          {
            id: "task-1",
            name: "每日复盘",
            cron: "0 9 * * *",
            humanReadable: "每天 09:00",
            status: "on",
            nextRun: "明天",
          },
        ]}
      />,
    );

    const instructionsButton = screen
      .getAllByRole("button", { name: /提示词/ })
      .find((button) => button.hasAttribute("aria-expanded"));
    const scheduledButton = screen.getByRole("button", { name: /定时任务/ });

    expect(instructionsButton).toBeTruthy();
    if (!instructionsButton) throw new Error("Missing instructions toggle");
    expect(instructionsButton.getAttribute("aria-expanded")).toBe("true");
    expect(scheduledButton.getAttribute("aria-expanded")).toBe("false");

    await userEvent.click(scheduledButton);

    expect(instructionsButton.getAttribute("aria-expanded")).toBe("false");
    expect(scheduledButton.getAttribute("aria-expanded")).toBe("true");
  });

  it("should expose scheduled task toggle and delete actions in a dropdown menu", async () => {
    const onToggleScheduledTask = vi.fn();
    const onDeleteScheduledTask = vi.fn();
    render(
      <ProjectDetailContextPanel
        scheduledTasks={[
          {
            id: "task-1",
            name: "每日复盘",
            cron: "0 9 * * *",
            humanReadable: "每天 09:00",
            status: "on",
            nextRun: "明天",
          },
        ]}
        onToggleScheduledTask={onToggleScheduledTask}
        onDeleteScheduledTask={onDeleteScheduledTask}
      />,
    );

    await userEvent.click(screen.getByTitle("定时任务操作"));
    await userEvent.click(screen.getByText("禁用"));
    expect(onToggleScheduledTask).toHaveBeenCalledWith("task-1", "off");

    await userEvent.click(screen.getByTitle("定时任务操作"));
    await userEvent.click(screen.getByText("删除"));
    expect(onDeleteScheduledTask).toHaveBeenCalledWith("task-1");
  });
});
