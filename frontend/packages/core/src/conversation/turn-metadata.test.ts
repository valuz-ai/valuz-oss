import { describe, expect, it } from "vitest";
import type { SessionEventDTO } from "../api/sessions-api";
import { buildTurns, createIncrementalTurns } from "./conversation-utils";
import { parseTurnMetadata } from "./turn-metadata";

const metadata = {
  background_input: {
    input_id: "input-1",
    source: "background",
    presentation: { kind: "example-event", status: "completed" },
  },
};
const event = (raw: unknown): SessionEventDTO => ({
  seq: 1,
  event: {
    event_type: "message.user",
    payload: {
      text: "evidence",
      message_id: "message-1",
      metadata: raw,
    } as never,
  },
});

describe("host queue provenance", () => {
  it("preserves validated metadata on a replayed flat event", () => {
    const [turn] = buildTurns([event(JSON.stringify(metadata))]);
    expect(turn.metadata).toEqual(metadata);
    expect(turn.userText).toBe("evidence");
  });
  it("preserves the same metadata when a live object enters the incremental renderer", () => {
    const builder = createIncrementalTurns();
    expect(builder.update([event(metadata)])[0].metadata).toEqual(metadata);
  });
  it.each([
    "not-json",
    null,
    [],
    { background_input: { source: "user", input_id: "input-1" } },
    { background_input: { source: "background", input_id: "" } },
    {
      background_input: {
        source: "background",
        input_id: "input-1",
        presentation: "fake",
      },
    },
  ])("rejects malformed or non-background provenance: %j", (raw) => {
    expect(parseTurnMetadata(raw)).toBeUndefined();
  });
  it("does not infer provenance from a human's text", () => {
    const [turn] = buildTurns([
      {
        seq: 1,
        event: {
          event_type: "message.user",
          payload: {
            text: "A managed work result is available. {managed_work_result}",
            message_id: "human",
          },
        },
      },
    ]);
    expect(turn.metadata).toBeUndefined();
    expect(turn.userText).toContain("managed_work_result");
  });
});
