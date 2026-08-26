import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ScheduledTaskTable } from "./ScheduledTaskTable";

const tasks = [
  {
    id: "task-1",
    name: "Tesla News",
    prompt: "At 09:00",
    trigger: "0 9 * * *",
    triggerTimezone: "LA",
    last: "3d ago",
    status: "off" as const,
  },
  {
    id: "task-2",
    name: "Daily AI News",
    prompt: "At 09:00",
    trigger: "0 9 * * *",
    last: "—",
    status: "on" as const,
  },
];

describe("ScheduledTaskTable", () => {
  it("should render a project header with task count and last run", () => {
    render(
      <ScheduledTaskTable
        tasks={tasks}
        title="Chat"
        taskCountLabel="2 任务"
        lastRunLabel="上次执行 3d ago"
      />,
    );

    expect(
      screen.getByRole("button", { name: /Chat\s*·\s*2 任务/ }),
    ).not.toBeNull();
    expect(screen.queryByText("上次执行 3d ago")).toBeNull();
    expect(screen.getAllByText("Tesla News").length).toBeGreaterThan(0);
  });

  it("should hide all tasks when the project is collapsed", () => {
    const onToggleCollapse = vi.fn();
    render(
      <ScheduledTaskTable
        tasks={tasks}
        title="Chat"
        taskCountLabel="2 任务"
        collapsed
        onToggleCollapse={onToggleCollapse}
      />,
    );

    expect(screen.getByText("Chat")).not.toBeNull();
    expect(screen.queryByText("Tesla News")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /Chat/ }));
    expect(onToggleCollapse).toHaveBeenCalledTimes(1);
  });

  it("should show task actions in an overflow menu", async () => {
    render(
      <ScheduledTaskTable
        tasks={tasks}
        onRunNow={() => {}}
        onToggle={() => {}}
        onDelete={() => {}}
      />,
    );

    // Radix opens on a real pointer sequence; a bare synthetic `pointerDown`
    // carries no pointerType/isPrimary and its trigger ignores it.
    await userEvent.click(screen.getAllByRole("button", { name: "操作" })[0]);

    expect(screen.getByText("立即运行")).not.toBeNull();
    expect(screen.getByRole("menuitem", { name: "启用" })).not.toBeNull();
    expect(screen.getByText("删除")).not.toBeNull();
  });
});
