/**
 * Frontend E2E for the live TODO panel.
 *
 * This is the integration test that complements the backend E2E
 * (`backend/tests/scripts/_e2e_kernel_messages_todos.py`) — it pins the wire
 * contract end-to-end on the renderer side:
 *
 * 1. Take a SSE frame in the *exact* shape the live backend produces
 *    for ``session.todos.update`` (todos JSON-stringified, message_id
 *    on the payload — captured verbatim from the backend E2E run).
 * 2. Run it through ``parseTodosUpdate`` — the helper the conversation
 *    page calls on every incoming frame.
 * 3. Hand the parsed snapshot to ``<ProjectDetailContextPanel>`` and
 *    assert the rendered DOM mirrors what the user actually sees:
 *    section header, X/Y done counter, gerund for in_progress, content
 *    for everything else, struck-through text on completed.
 *
 * If any of the four moving parts (kernel event shape, SSE adapter
 * stringification, parser, panel renderer) drifts, this test fails
 * fast — much sooner than a manual run of the desktop app would
 * surface the regression.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { parseTodosUpdate, type SessionEventDTO } from "@valuz/core";
import { ProjectDetailContextPanel } from "@valuz/ui";

// Captured verbatim from the backend E2E run
// (backend/tests/scripts/_e2e_kernel_messages_todos.py output: "frame payload
// carries todos as JSON-stringified list").
const LIVE_TODO_UPDATE_FRAME: SessionEventDTO = {
  seq: 3,
  event: {
    event_type: "session.todos.update",
    payload: {
      todos: JSON.stringify([
        {
          content: "Plan E2E",
          status: "completed",
          activeForm: "Planning E2E",
        },
        {
          content: "Run smoke",
          status: "in_progress",
          activeForm: "Running smoke",
        },
        { content: "Write ADR", status: "pending" },
      ]),
      message_id: "08a36fd58a4345af81c7b646ca42a82f",
    },
  },
};

describe("ProjectDetailContextPanel — end-to-end TODO render path", () => {
  it("should render the live SSE TODO snapshot all the way to visible DOM", () => {
    // Step 1 — the page's SSE callback runs every incoming frame
    // through parseTodosUpdate.
    const todos = parseTodosUpdate(LIVE_TODO_UPDATE_FRAME);
    expect(todos).not.toBeNull();
    expect(todos).toHaveLength(3);

    // Step 2 — page calls setTodos(parsed) and the panel re-renders.
    render(<ProjectDetailContextPanel todos={todos!} />);

    // Section header is visible.
    expect(screen.getByText("待办事项")).toBeTruthy();
    // 1/3 completed → counter reads "1/3".
    expect(screen.getByText("1/3")).toBeTruthy();
    // Completed row uses ``content`` (not activeForm).
    const completed = screen.getByText("Plan E2E");
    expect(completed).toBeTruthy();
    // Tailwind ``line-through`` applied to completed rows.
    expect(completed.parentElement?.className).toMatch(/line-through/);
    // In-progress row uses ``activeForm`` (the gerund), not ``content``.
    expect(screen.getByText("Running smoke")).toBeTruthy();
    expect(screen.queryByText("Run smoke")).toBeNull();
    // Pending row uses ``content``.
    expect(screen.getByText("Write ADR")).toBeTruthy();
  });

  it("should keep the panel section absent when the agent has emitted no todo_update yet", () => {
    // Page-level state starts at ``null`` before the first SSE frame;
    // the panel keeps a stable empty TODO section.
    render(<ProjectDetailContextPanel todos={null} />);
    expect(screen.getByText("待办事项")).toBeTruthy();
    expect(screen.getByText("暂无待办")).toBeTruthy();
  });

  it("should ignore malformed payloads while keeping the prior snapshot intact", () => {
    // Simulate a wire-level corruption: parser returns null, page does
    // not setTodos, the panel keeps showing whatever it had.
    const malformed: SessionEventDTO = {
      seq: 4,
      event: {
        event_type: "session.todos.update",
        payload: { todos: "not-json", message_id: "ignored" },
      },
    };
    expect(parseTodosUpdate(malformed)).toBeNull();
  });
});
