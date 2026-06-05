import { describe, expect, it } from "vitest";
import {
  parseTodosUpdate,
  SESSION_TODOS_UPDATE_EVENT,
  type SessionEventDTO,
} from "./sessions-api";

function makeFrame(
  eventType: string,
  payload: Record<string, string>,
): SessionEventDTO {
  return { seq: 1, event: { event_type: eventType, payload } };
}

describe("parseTodosUpdate", () => {
  it("should JSON-parse the kernel todo snapshot when the event is session.todos.update", () => {
    const todos = [
      {
        content: "Plan migration",
        status: "in_progress",
        activeForm: "Planning migration",
      },
      { content: "Write code", status: "pending" },
    ];
    const frame = makeFrame(SESSION_TODOS_UPDATE_EVENT, {
      todos: JSON.stringify(todos),
    });

    const parsed = parseTodosUpdate(frame);

    expect(parsed).toEqual(todos);
  });

  it("should return null when the frame is not a todos update", () => {
    const frame = makeFrame("message.assistant.delta", { text: "hi" });
    expect(parseTodosUpdate(frame)).toBeNull();
  });

  it("should return null when the todos payload is malformed JSON", () => {
    // The host stringifies arrays — a malformed payload would mean either a
    // bug or a deliberate sentinel; either way, the panel should keep its
    // prior snapshot rather than blanking out.
    const frame = makeFrame(SESSION_TODOS_UPDATE_EVENT, { todos: "{not json" });
    expect(parseTodosUpdate(frame)).toBeNull();
  });

  it("should drop entries that are missing required content/status fields", () => {
    const frame = makeFrame(SESSION_TODOS_UPDATE_EVENT, {
      todos: JSON.stringify([
        { content: "valid", status: "pending" },
        { content: "" }, // missing status, empty content → dropped
        "not an object", // dropped
        { status: "completed" }, // empty content → dropped
      ]),
    });

    const parsed = parseTodosUpdate(frame);

    expect(parsed).toEqual([
      { content: "valid", status: "pending", activeForm: undefined },
    ]);
  });

  it("should preserve an empty list when the kernel signals all-done", () => {
    // Empty array is meaningful — it's the kernel's "list cleared" signal.
    const frame = makeFrame(SESSION_TODOS_UPDATE_EVENT, { todos: "[]" });
    expect(parseTodosUpdate(frame)).toEqual([]);
  });
});
