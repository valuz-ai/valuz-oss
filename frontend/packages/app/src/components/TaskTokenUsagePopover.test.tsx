import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { TaskTokenUsagePopover } from "./TaskTokenUsagePopover";

describe("TaskTokenUsagePopover", () => {
  it("shows task totals and per-Agent run usage", async () => {
    render(
      <TaskTokenUsagePopover
        usage={{
          input_tokens: 754,
          output_tokens: 116,
          cache_read_tokens: 59_900,
          cache_write_tokens: 2,
          total_tokens: 60_772,
          runs: [
            {
              session_id: "lead-session",
              agent_slug: "planner",
              kind: "lead",
              sequence: 0,
              label: null,
              input_tokens: 400,
              output_tokens: 80,
              cache_read_tokens: 30_000,
              cache_write_tokens: 0,
              total_tokens: 30_480,
            },
            {
              session_id: "writer-session",
              agent_slug: "writer",
              kind: "subtask",
              sequence: 1,
              label: "撰写报告",
              input_tokens: 354,
              output_tokens: 36,
              cache_read_tokens: 29_900,
              cache_write_tokens: 2,
              total_tokens: 30_292,
            },
          ],
        }}
      />,
    );

    const trigger = screen.getByRole("button", {
      name: /60[,.]?772 Tokens/,
    });
    expect(trigger.textContent).toMatch(/60[,.]?8K Tokens/i);

    fireEvent.click(trigger);

    expect(await screen.findByText("任务总 Tokens")).not.toBeNull();
    expect(screen.getByText("按 Agent / Run")).not.toBeNull();
    expect(screen.getByText("撰写报告")).not.toBeNull();
    expect(screen.getByText("30,292")).not.toBeNull();
    expect(screen.getByText("98.8%")).not.toBeNull();
  });
});
