import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ExecutionLog } from "./ExecutionLog";

describe("ExecutionLog", () => {
  it("opens the session when any recent execution row area is clicked", () => {
    const onSessionClick = vi.fn();

    render(
      <ExecutionLog
        onSessionClick={onSessionClick}
        rows={[
          {
            id: "run-1",
            time: "2026-05-15 09:00",
            status: "ok",
            duration: "2s",
            output: "Finished",
            taskName: "Daily AI News",
            sessionId: "session-1",
          },
        ]}
      />,
    );

    expect(screen.getByText("执行成功")).not.toBeNull();
    expect(screen.getByText("Finished")).not.toBeNull();

    fireEvent.click(screen.getByText("Finished"));

    expect(onSessionClick).toHaveBeenCalledWith("session-1");

    expect(screen.queryByText(/执行指令/)).toBeNull();
    expect(screen.queryByText(/Instruction/)).toBeNull();
  });

  it("does not render placeholder output dashes", () => {
    render(
      <ExecutionLog
        rows={[
          {
            id: "run-1",
            time: "2026-05-15 09:00",
            status: "ok",
            duration: "2s",
            output: "—",
            taskName: "Daily AI News",
            sessionId: "session-1",
          },
        ]}
      />,
    );

    expect(screen.queryByText("—")).toBeNull();
  });
});
