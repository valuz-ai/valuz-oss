import { fireEvent, render, screen } from "@testing-library/react";
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

  it("should show task actions in an overflow menu", () => {
    render(
      <ScheduledTaskTable
        tasks={tasks}
        onRunNow={() => {}}
        onToggle={() => {}}
        onDelete={() => {}}
      />,
    );

    fireEvent.pointerDown(screen.getAllByRole("button", { name: "操作" })[0]);

    expect(screen.getByText("运行测试")).not.toBeNull();
    expect(screen.getByText("执行")).not.toBeNull();
    expect(screen.getByText("删除")).not.toBeNull();
  });
});
