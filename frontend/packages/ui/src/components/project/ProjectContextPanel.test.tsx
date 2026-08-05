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

    expect(screen.getByRole("tab", { name: "项目" })).toBeTruthy();
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
      .getAllByRole("button", { name: /项目说明/ })
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
    await userEvent.click(screen.getByText("暂停"));
    expect(onToggleScheduledTask).toHaveBeenCalledWith("task-1", "off");

    await userEvent.click(screen.getByTitle("定时任务操作"));
    await userEvent.click(screen.getByText("删除"));
    expect(onDeleteScheduledTask).toHaveBeenCalledWith("task-1");
  });
});

describe("ProjectDetailContextPanel — Generated files section", () => {
  it("should render agent-delivered artifacts under the 生成文件 section", () => {
    render(
      <ProjectDetailContextPanel
        generatedFiles={[
          {
            id: "a1",
            name: "报告.html",
            size: "76.8 KB",
            path: "/d/报告.html",
          },
          { id: "a2", name: "报告.md", size: "19.8 KB", path: "/d/报告.md" },
        ]}
      />,
    );

    expect(screen.getByText("生成文件")).toBeTruthy();
    expect(screen.getByText("报告.html")).toBeTruthy();
    expect(screen.getByText("报告.md")).toBeTruthy();
  });

  it("should render the empty state when no artifacts have been delivered", () => {
    render(<ProjectDetailContextPanel generatedFiles={[]} />);
    expect(screen.getByText("生成文件")).toBeTruthy();
    expect(screen.getByText("暂无生成文件")).toBeTruthy();
  });

  it("should open the file's absolute path when a row is clicked", async () => {
    const onOpenGeneratedFile = vi.fn();
    render(
      <ProjectDetailContextPanel
        generatedFiles={[
          { id: "a1", name: "报告.html", path: "/deliverables/报告.html" },
        ]}
        onOpenGeneratedFile={onOpenGeneratedFile}
      />,
    );

    await userEvent.click(screen.getByText("报告.html"));
    expect(onOpenGeneratedFile).toHaveBeenCalledWith("/deliverables/报告.html");
  });

  it("should hide the section entirely when generatedFiles is undefined", () => {
    render(<ProjectDetailContextPanel todos={[]} />);
    expect(screen.queryByText("生成文件")).toBeNull();
  });
});

describe("ProjectDetailContextPanel — Artifact version history", () => {
  const v2 = {
    id: "r2",
    name: "报告.md",
    path: "/d/.artifact/A1/v2/报告.md",
    versionNo: 2,
    isCurrent: true,
    artifactId: "A1",
  };

  it("should offer history on a superseded row, whose versions it cannot see", async () => {
    // The session delivered v1; another session then made v2. This row shows v1
    // and the versions worth reaching are exactly the ones it has no record of.
    const onLoadArtifactVersions = vi.fn().mockResolvedValue([
      { id: "rev1", versionNo: 1, path: "/d/v1", when: "旧", openable: true },
      { id: "rev2", versionNo: 2, path: "/d/v2", when: "新", openable: true },
    ]);
    render(
      <ProjectDetailContextPanel
        generatedFiles={[
          {
            id: "r1",
            name: "报告.md",
            path: "/d/.artifact/A1/v1/报告.md",
            versionNo: 1,
            isCurrent: false,
            artifactId: "A1",
          },
        ]}
        onLoadArtifactVersions={onLoadArtifactVersions}
      />,
    );

    await userEvent.click(screen.getByTitle("查看历史版本"));

    expect(onLoadArtifactVersions).toHaveBeenCalledWith("A1");
    expect(await screen.findByText("新")).toBeTruthy();
  });

  it("should not offer history for a deliverable still on its first version", () => {
    // Nothing behind it to show — an expander would open onto one row.
    render(
      <ProjectDetailContextPanel
        generatedFiles={[
          {
            id: "r1",
            name: "报告.md",
            path: "/d/报告.md",
            versionNo: 1,
            artifactId: "A1",
          },
        ]}
        onLoadArtifactVersions={vi.fn()}
      />,
    );

    expect(screen.queryByTitle("查看历史版本")).toBeNull();
  });

  it("should not offer history when no loader is provided", () => {
    render(<ProjectDetailContextPanel generatedFiles={[v2]} />);

    expect(screen.getByText("v2")).toBeTruthy();
    expect(screen.queryByTitle("查看历史版本")).toBeNull();
  });

  it("should load a deliverable's versions on demand and list them newest first", async () => {
    const onLoadArtifactVersions = vi.fn().mockResolvedValue([
      {
        id: "rev1",
        versionNo: 1,
        path: "/d/.artifact/A1/v1/报告.md",
        size: "5.1 KB",
        when: "08-04 19:24",
        openable: true,
      },
      {
        id: "rev2",
        versionNo: 2,
        path: "/d/.artifact/A1/v2/报告.md",
        size: "6.5 KB",
        when: "08-04 19:29",
        openable: true,
      },
    ]);
    render(
      <ProjectDetailContextPanel
        generatedFiles={[v2]}
        onLoadArtifactVersions={onLoadArtifactVersions}
      />,
    );

    // Nothing fetched until the badge is opened — most histories are never read.
    expect(onLoadArtifactVersions).not.toHaveBeenCalled();

    await userEvent.click(screen.getByTitle("查看历史版本"));

    expect(onLoadArtifactVersions).toHaveBeenCalledWith("A1");
    expect(await screen.findByText("08-04 19:24")).toBeTruthy();
    expect(screen.getByText("08-04 19:29")).toBeTruthy();
  });

  it("should open the clicked version's own snapshot, not the current one", async () => {
    const onOpenGeneratedFile = vi.fn();
    render(
      <ProjectDetailContextPanel
        generatedFiles={[v2]}
        onOpenGeneratedFile={onOpenGeneratedFile}
        onLoadArtifactVersions={vi.fn().mockResolvedValue([
          {
            id: "rev1",
            versionNo: 1,
            path: "/d/.artifact/A1/v1/报告.md",
            when: "旧",
            openable: true,
          },
          {
            id: "rev2",
            versionNo: 2,
            path: "/d/.artifact/A1/v2/报告.md",
            when: "新",
            openable: true,
          },
        ])}
      />,
    );

    await userEvent.click(screen.getByTitle("查看历史版本"));
    await userEvent.click(await screen.findByText("旧"));

    expect(onOpenGeneratedFile).toHaveBeenCalledWith(
      "/d/.artifact/A1/v1/报告.md",
    );
  });

  it("should fetch a history only once however often it is toggled", async () => {
    const onLoadArtifactVersions = vi
      .fn()
      .mockResolvedValue([
        { id: "rev1", versionNo: 1, path: "/d/v1", when: "旧", openable: true },
      ]);
    render(
      <ProjectDetailContextPanel
        generatedFiles={[v2]}
        onLoadArtifactVersions={onLoadArtifactVersions}
      />,
    );

    const badge = screen.getByTitle("查看历史版本");
    await userEvent.click(badge);
    await screen.findByText("旧");
    await userEvent.click(badge);
    await userEvent.click(badge);

    expect(onLoadArtifactVersions).toHaveBeenCalledTimes(1);
  });

  it("should show a version whose bytes are gone, without offering to open it", async () => {
    // The generation happened; hiding it would misrepresent the history.
    const onOpenGeneratedFile = vi.fn();
    render(
      <ProjectDetailContextPanel
        generatedFiles={[v2]}
        onOpenGeneratedFile={onOpenGeneratedFile}
        onLoadArtifactVersions={vi.fn().mockResolvedValue([
          { id: "rev1", versionNo: 1, path: "", openable: false },
          {
            id: "rev2",
            versionNo: 2,
            path: "/d/v2",
            when: "新",
            openable: true,
          },
        ])}
      />,
    );

    await userEvent.click(screen.getByTitle("查看历史版本"));
    const gone = await screen.findByText("该版本的文件已不存在");
    await userEvent.click(gone);

    expect(onOpenGeneratedFile).not.toHaveBeenCalled();
  });

  it("should say so when the history cannot be loaded", async () => {
    render(
      <ProjectDetailContextPanel
        generatedFiles={[v2]}
        onLoadArtifactVersions={vi.fn().mockRejectedValue(new Error("boom"))}
      />,
    );

    await userEvent.click(screen.getByTitle("查看历史版本"));

    expect(await screen.findByText("历史版本加载失败")).toBeTruthy();
  });
});

describe("ProjectDetailContextPanel — Project deliverables section", () => {
  it("should list what the project holds, separately from one session's output", () => {
    // A conversation that delivered nothing shows an empty 生成文件 list; the
    // workspace section is the only place those deliverables appear.
    render(
      <ProjectDetailContextPanel
        generatedFiles={[]}
        projectArtifacts={[
          {
            id: "A1",
            name: "季度报告.pdf",
            path: "/d/.artifact/A1/v3/季度报告.pdf",
            versionNo: 3,
            isCurrent: true,
            artifactId: "A1",
          },
        ]}
      />,
    );

    expect(screen.getByText("交付物")).toBeTruthy();
    expect(screen.getByText("季度报告.pdf")).toBeTruthy();
    expect(screen.getByText("暂无生成文件")).toBeTruthy();
  });

  it("should expand a project deliverable's history the same way a session row does", async () => {
    const onLoadArtifactVersions = vi.fn().mockResolvedValue([
      { id: "r1", versionNo: 1, path: "/d/v1", when: "旧", openable: true },
      { id: "r2", versionNo: 2, path: "/d/v2", when: "新", openable: true },
    ]);
    render(
      <ProjectDetailContextPanel
        projectArtifacts={[
          {
            id: "A1",
            name: "报告.md",
            path: "/d/.artifact/A1/v2/报告.md",
            versionNo: 2,
            isCurrent: true,
            artifactId: "A1",
          },
        ]}
        onLoadArtifactVersions={onLoadArtifactVersions}
      />,
    );

    await userEvent.click(screen.getByTitle("查看历史版本"));

    expect(onLoadArtifactVersions).toHaveBeenCalledWith("A1");
    expect(await screen.findByText("旧")).toBeTruthy();
  });

  it("should show an empty state rather than vanishing when the project has none", () => {
    render(<ProjectDetailContextPanel projectArtifacts={[]} />);
    expect(screen.getByText("交付物")).toBeTruthy();
    expect(screen.getByText("暂无交付物")).toBeTruthy();
  });

  it("should hide the section entirely when projectArtifacts is undefined", () => {
    render(<ProjectDetailContextPanel todos={[]} />);
    expect(screen.queryByText("交付物")).toBeNull();
  });
});
