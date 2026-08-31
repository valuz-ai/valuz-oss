import { beforeAll, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { automationsApi } from "@valuz/core";
import { initI18n } from "@valuz/shared/i18n";

import { CreateAutomationDialog } from "./CreateAutomationDialog";
import { CreatePlaybookDialog } from "./CreatePlaybookDialog";

beforeAll(() => initI18n({ locale: "zh-CN", fallbackLocale: "en-US" }));

describe("template-prefilled create dialogs", () => {
  it("opens a Playbook template as an editable create form", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(
      <CreatePlaybookDialog
        open
        onOpenChange={vi.fn()}
        onSubmit={onSubmit}
        prefill={{
          name: "每日市场晨报",
          content: "检索并汇总隔夜市场变化。",
          status: "draft",
          default_agent_slug: "valurion",
        }}
        targets={[
          { id: "chat-default", name: "Chat", kind: "chat", project_id: null },
        ]}
        agents={[{ slug: "valurion", name: "Valurion" }]}
      />,
    );

    expect(screen.getByDisplayValue("每日市场晨报")).toBeTruthy();
    expect(screen.getByDisplayValue("检索并汇总隔夜市场变化。")).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() =>
      expect(onSubmit).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "每日市场晨报",
          content: "检索并汇总隔夜市场变化。",
          project_id: null,
          default_executor: { agent_slug: "valurion" },
        }),
      ),
    );
  });

  it("keeps Automation template trigger and task defaults until confirmation", async () => {
    vi.spyOn(automationsApi, "listPlaybooks").mockResolvedValue([]);
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(
      <CreateAutomationDialog
        open
        onOpenChange={vi.fn()}
        onSubmit={onSubmit}
        prefill={{
          name: "每周代码复盘",
          prompt_template: "汇总本周代码变更与风险。",
          agent_slug: "engineering-lead",
          trigger: { kind: "interval", seconds: 7200 },
          action_kind: "task",
          worktree: true,
        }}
        agents={[{ slug: "engineering-lead", name: "Engineering Lead" }]}
        targets={[
          { id: "project-1", name: "Valuz", kind: "project", project_id: "project-1" },
        ]}
        selectedTargetId="project-1"
        onSelectTarget={vi.fn()}
      />,
    );

    expect(screen.getByDisplayValue("每周代码复盘")).toBeTruthy();
    expect(screen.getByDisplayValue("汇总本周代码变更与风险。")).toBeTruthy();
    expect(screen.getByRole("tab", { name: "间隔" }).getAttribute("data-state")).toBe(
      "active",
    );
    expect(
      screen.getByRole("button", { name: /Task 任务/ }).className,
    ).toContain("border-brand");

    await userEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() =>
      expect(onSubmit).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "每周代码复盘",
          trigger: { kind: "interval", seconds: 7200 },
          action_kind: "task",
          worktree: true,
        }),
      ),
    );
  });
});
