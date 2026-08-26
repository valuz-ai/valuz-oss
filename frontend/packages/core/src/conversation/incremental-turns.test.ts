import { describe, expect, it } from "vitest";
import type { SessionEventDTO } from "../api/sessions-api";
import { buildTurns, createIncrementalTurns } from "./conversation-utils";

const evt = (
  seq: number,
  eventType: string,
  payload: Record<string, string>,
  timestamp?: number,
): SessionEventDTO => ({
  seq,
  event: { event_type: eventType, payload },
  timestamp,
});

// A dense, realistic multi-turn stream: streamed text + thinking, canonical
// seals, streamed tool input/output, tool completion, an interrupt, trailing
// runtime meta, and a second turn. Exercises every branch the incremental
// builder must keep equivalent to a full re-fold.
const STREAM: SessionEventDTO[] = [
  evt(1, "message.user", { text: "hi", message_id: "u1" }, 1000),
  evt(2, "message.assistant.thinking_delta", { text: "Let", message_id: "a1" }, 1010),
  evt(3, "message.assistant.thinking_delta", { text: " me", message_id: "a1" }, 1020),
  evt(4, "message.assistant.thinking", { text: "Let me think", message_id: "a1" }, 1030),
  evt(5, "message.assistant.text_delta", { text: "Hel", message_id: "a1" }, 1040),
  evt(6, "message.assistant.text_delta", { text: "lo", message_id: "a1" }, 1050),
  evt(7, "tool.call.input_delta", { tool_use_id: "t1", name: "Bash", text: '{"cmd":' }, 1060),
  evt(8, "tool.call.input_delta", { tool_use_id: "t1", text: '"ls"}' }, 1070),
  evt(9, "tool.call.started", { id: "t1", name: "Bash", input: '{"cmd":"ls"}' }, 1080),
  evt(10, "tool.call.output_delta", { tool_use_id: "t1", text: "file-a\n" }, 1090),
  evt(11, "tool.call.output_delta", { tool_use_id: "t1", text: "file-b\n" }, 1100),
  evt(12, "tool.call.completed", { id: "t1", name: "Bash", content: "file-a\nfile-b\n" }, 1110),
  evt(13, "message.assistant.text_delta", { text: " world", message_id: "a1" }, 1120),
  evt(14, "message.assistant.delta", { text: "Hello world done", message_id: "a1" }, 1130),
  evt(15, "runtime.engine.cost", { total_cost_usd: "0.01" }, 1140),
  // Second turn.
  evt(16, "message.user", { text: "again", message_id: "u2" }, 2000),
  evt(17, "message.assistant.text_delta", { text: "Sure", message_id: "a2" }, 2010),
  evt(18, "session.idle", { stop_reason: "user_interrupt" }, 2020),
];

describe("createIncrementalTurns — equivalence with buildTurns", () => {
  it("matches buildTurns at every streamed prefix (one event at a time)", () => {
    const inc = createIncrementalTurns();
    for (let k = 0; k <= STREAM.length; k += 1) {
      const prefix = STREAM.slice(0, k);
      // Feed the SAME growing array-prefix identity the UI uses: each step is
      // the previous slice plus one more element (append-only).
      const got = inc.update(prefix);
      expect(got).toEqual(buildTurns(prefix));
    }
  });

  it("matches buildTurns for arbitrary chunk splits", () => {
    for (const splits of [[3, 9, 14], [1, 2, 15, 16], [8], [12, 17]]) {
      const inc = createIncrementalTurns();
      const bounds = [...splits, STREAM.length];
      let last: ReturnType<typeof inc.update> = [];
      let start = 0;
      for (const end of bounds) {
        last = inc.update(STREAM.slice(0, end));
        start = end;
      }
      void start;
      expect(last).toEqual(buildTurns(STREAM));
    }
  });

  it("falls back to a full rebuild when the prefix changes (window replace)", () => {
    const inc = createIncrementalTurns();
    inc.update(STREAM.slice(0, 10));
    // A brand-new array with a different first event breaks append identity.
    const replaced: SessionEventDTO[] = [
      evt(50, "message.user", { text: "fresh", message_id: "z1" }, 5000),
      evt(51, "message.assistant.text_delta", { text: "Yo", message_id: "z2" }, 5010),
    ];
    expect(inc.update(replaced)).toEqual(buildTurns(replaced));
  });

  it("handles a shrink (fewer events than processed) via rebuild", () => {
    const inc = createIncrementalTurns();
    inc.update(STREAM);
    const shrunk = STREAM.slice(0, 6);
    expect(inc.update(shrunk)).toEqual(buildTurns(shrunk));
  });

  it("empty input yields no turns", () => {
    const inc = createIncrementalTurns();
    expect(inc.update([])).toEqual([]);
  });
});

describe("createIncrementalTurns — reference stability", () => {
  it("keeps a sealed earlier turn's reference stable while a later turn streams", () => {
    const inc = createIncrementalTurns();
    // Through the end of turn 1 + start of turn 2.
    inc.update(STREAM.slice(0, 17));
    const afterFirst = inc.update(STREAM.slice(0, 17));
    const turn1RefA = afterFirst[0];
    // Append more to turn 2 — turn 1 is sealed and must not get a new ref.
    const afterSecond = inc.update(STREAM.slice(0, 18));
    expect(afterSecond[0]).toBe(turn1RefA);
    // The streaming turn's reference DID change (content changed) so React
    // re-renders it.
    expect(afterSecond[1]).not.toBe(afterFirst[1]);
  });

  it("gives the growing tail turn a fresh reference on each new delta", () => {
    const inc = createIncrementalTurns();
    const a = inc.update(STREAM.slice(0, 5));
    const b = inc.update(STREAM.slice(0, 6));
    expect(b[0]).not.toBe(a[0]);
    expect(b[0]!.blocks).not.toBe(a[0]!.blocks);
  });
});
