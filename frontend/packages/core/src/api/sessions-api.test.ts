import { afterEach, describe, expect, it, vi } from "vitest";
import {
  parseTodosUpdate,
  sessionsApi,
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

describe("sessionsApi.subscribeEvents — SSE wire parsing (two seq spaces)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  const sseResponse = (chunks: string[]): Response =>
    new Response(
      new ReadableStream<Uint8Array>({
        start(controller) {
          const encoder = new TextEncoder();
          for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
          controller.close();
        },
      }),
      { status: 200, headers: { "Content-Type": "text/event-stream" } },
    );

  it("should carry event_uid through frames and surface heartbeat seqs as the history cursor", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      sseResponse([
        // History backfill frame — durable seq + uid.
        'data: {"seq": 900, "event_type": "message.user", "payload": {"text": "hi"}, "timestamp": 1720000000000, "event_uid": "aabbccddeeff00112233445566778899"}\n\n',
        // Heartbeat: no event_type; its seq is the HISTORY cursor.
        'data: {"seq": 901}\n\n',
        // Live frame — kernel-local seq, uid explicitly null (delta-style).
        'data: {"seq": 5, "event_type": "message.assistant.text_delta", "payload": {"text": "yo"}, "event_uid": null}\n\n',
      ]),
    );

    const events: SessionEventDTO[] = [];
    const cursors: number[] = [];
    await sessionsApi.subscribeEvents(
      "s1",
      (event) => events.push(event),
      undefined,
      undefined,
      (seq) => cursors.push(seq),
    );

    expect(events).toHaveLength(2);
    expect(events[0]!.seq).toBe(900);
    expect(events[0]!.event_uid).toBe("aabbccddeeff00112233445566778899");
    expect(events[0]!.timestamp).toBe(1720000000000);
    expect(events[1]!.seq).toBe(5);
    expect(events[1]!.event_uid).toBeNull();
    // Only the heartbeat advanced the history cursor — event frames (which
    // may be live/kernel-space) never did.
    expect(cursors).toEqual([901]);
  });

  it("should normalize a missing event_uid to null (legacy wire frames)", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      sseResponse([
        'data: {"seq": 3, "event_type": "message.user", "payload": {"text": "old"}}\n\n',
      ]),
    );

    const events: SessionEventDTO[] = [];
    await sessionsApi.subscribeEvents("s1", (event) => events.push(event));

    expect(events).toHaveLength(1);
    expect(events[0]!.event_uid).toBeNull();
  });
});
