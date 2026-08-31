import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { AutomationItem, PlaybookDefinition } from "@valuz/core";

import { AutomationDefinitionTable } from "./AutomationDefinitionTable";
import { PlaybookDefinitionTable } from "./PlaybookDefinitionTable";

const automation: AutomationItem = {
  automation_id: "automation-1",
  project_id: "project-1",
  project_name: "Research",
  project_kind: "project",
  name: "Morning review",
  agent_kind: "project_member",
  agent_slug: "valurion",
  agent_name: "Valurion",
  action_kind: "chat",
  worktree: false,
  playbook_definition_id: null,
  playbook_version: null,
  trigger: {
    kind: "cron",
    cron_expr: "0 9 * * *",
    timezone: "Asia/Shanghai",
  },
  trigger_human_readable: "每天 09:00",
  status: "enabled",
  next_run_at: null,
  last_run_at: null,
  last_run_status: null,
};

const playbook: PlaybookDefinition = {
  id: "playbook-1",
  project_id: "project-1",
  name: "季度复盘",
  status: "active",
  origin: "user",
  source_definition_id: null,
  current_version: 3,
  revision: 2,
  created_at: 1,
  updated_at: 2,
};

describe("canonical resource definition tables", () => {
  it("uses the global Automation columns and enabled status", () => {
    render(
      <AutomationDefinitionTable
        automations={[automation]}
        onOpen={vi.fn()}
        onToggle={vi.fn()}
        onRunNow={vi.fn()}
        onDelete={vi.fn()}
      />,
    );

    expect(screen.getByText("任务")).toBeTruthy();
    expect(screen.getByText("触发规则")).toBeTruthy();
    expect(screen.getByText("时区")).toBeTruthy();
    expect(screen.getByText("上次执行")).toBeTruthy();
    expect(screen.getAllByText("启用").length).toBeGreaterThan(0);
  });

  it("uses the global Playbook columns, status, and action menu", async () => {
    const onRun = vi.fn();
    const onOpen = vi.fn();
    const onEdit = vi.fn();
    render(
      <PlaybookDefinitionTable
        definitions={[playbook]}
        runningId={null}
        onOpen={onOpen}
        onEdit={onEdit}
        onRun={onRun}
        onStatusChange={vi.fn()}
        onDelete={vi.fn()}
      />,
    );

    expect(screen.getByText("执行手册")).toBeTruthy();
    expect(screen.getByText("版本")).toBeTruthy();
    expect(screen.getByText("状态")).toBeTruthy();
    expect(screen.getAllByText("已启用").length).toBeGreaterThan(0);

    await userEvent.click(screen.getAllByRole("button", { name: "季度复盘" })[0]!);
    expect(onOpen).toHaveBeenCalledWith(playbook);

    await userEvent.click(screen.getAllByRole("button", { name: "操作" })[0]!);
    await userEvent.click(screen.getByRole("menuitem", { name: "运行" }));
    expect(onRun).toHaveBeenCalledWith(playbook);

    await userEvent.click(screen.getAllByRole("button", { name: "操作" })[0]!);
    await userEvent.click(screen.getByRole("menuitem", { name: "编辑" }));
    expect(onEdit).toHaveBeenCalledWith(playbook);
  });
});
