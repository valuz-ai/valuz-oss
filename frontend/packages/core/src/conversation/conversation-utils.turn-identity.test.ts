import { describe, expect, it } from "vitest";
import type { SessionEventDTO } from "../api/sessions-api";
import { buildTurns, createIncrementalTurns } from "./conversation-utils";

/**
 * A turn is identified by its ``user_message`` EVENT, not by the Message it
 * may or may not belong to.
 *
 * Regression for "the second turn's bubble and its error card render inside
 * the first turn's answer". A turn that failed before the kernel accepted it
 * (sandbox allocation, credentials) never got a Message; the recovery write
 * could only anchor its ``user_message`` onto the session's latest message —
 * the PREVIOUS turn's — so two turns arrived sharing one ``message_id``. The
 * turn key was derived from ``message_id``, the virtualizer keys its size
 * cache on that, and both turns ended up in one slot: the second painted
 * straight into the middle of the first.
 *
 * Production event rows, verbatim (durable seq → id):
 *   7647 user_message   message_id=1d534a77   ← turn 1 opens
 *   …
 *   7689 user_message   message_id=1d534a77   ← turn 2, borrowed anchor
 *   7690 session_error  message_id=1d534a77
 */

const evt = (
  seq: number,
  eventType: string,
  payload: Record<string, string>,
  extra: { event_uid?: string; timestamp?: number } = {},
): SessionEventDTO =>
  ({
    seq,
    event: { event_type: eventType, payload },
    ...extra,
  }) as SessionEventDTO;

const M = "1d534a77";

const TWO_TURNS_ONE_MESSAGE_ID: SessionEventDTO[] = [
  evt(
    7647,
    "message.user",
    { message_id: M, text: "看看 今天 弘信电子" },
    { event_uid: "b86b679e" },
  ),
  evt(
    7682,
    "message.assistant.delta",
    { message_id: M, text: "尾盘复盘…" },
    { event_uid: "ed13d0f2" },
  ),
  evt(7687, "session.idle", { message_id: M }, { event_uid: "f174a5d2" }),
  evt(
    7689,
    "message.user",
    { message_id: M, text: "几个问题哈" },
    { event_uid: "180d2ee8" },
  ),
  evt(
    7690,
    "run.failed",
    { message_id: M, message: "502: Valuz workload identity unreachable" },
    { event_uid: "f99fad0d" },
  ),
];

describe("turn identity — keyed by the user_message event, not its Message", () => {
  it("gives two turns that share a message_id two distinct ids", () => {
    const turns = buildTurns(TWO_TURNS_ONE_MESSAGE_ID);

    expect(turns).toHaveLength(2);
    expect(turns[0]!.id).not.toBe(turns[1]!.id);
  });

  it("still folds the failure into the second turn, not the first", () => {
    const [first, second] = buildTurns(TWO_TURNS_ONE_MESSAGE_ID);

    expect(first!.failedMessage).toBeNull();
    expect(first!.blocks).toHaveLength(1);
    expect(second!.failedMessage).toBe(
      "502: Valuz workload identity unreachable",
    );
    expect(second!.blocks).toHaveLength(0);
  });

  it("carries the Message separately, for callers that address one", () => {
    const [first, second] = buildTurns(TWO_TURNS_ONE_MESSAGE_ID);

    expect(first!.messageId).toBe(M);
    // The recovery write anchored the second turn on the first's Message;
    // the fold reports what the event carried, and the fork affordance is
    // what decides whether that is trustworthy (it is not, for a turn that
    // failed before producing anything).
    expect(second!.messageId).toBe(M);
  });

  it("keeps the same id for the live frame and its persisted copy", () => {
    // Live broadcast: seq 0, same event_uid as the row that lands later.
    const live = evt(
      0,
      "message.user",
      { message_id: "m9", text: "hi" },
      { event_uid: "u-9" },
    );
    const persisted = evt(
      42,
      "message.user",
      { message_id: "m9", text: "hi" },
      { event_uid: "u-9" },
    );

    expect(buildTurns([live])[0]!.id).toBe(buildTurns([persisted])[0]!.id);
  });

  it("falls back to message_id, then seq, when the event carries no uid", () => {
    const [withMessage] = buildTurns([
      evt(3, "message.user", { message_id: "m3", text: "a" }),
    ]);
    const [bare] = buildTurns([evt(4, "message.user", { text: "b" })]);

    expect(withMessage!.id).toBe("turn-m3");
    expect(bare!.id).toBe("turn-4");
    expect(bare!.messageId).toBeNull();
  });

  it("matches the incremental builder at every prefix", () => {
    const inc = createIncrementalTurns();
    for (let i = 1; i <= TWO_TURNS_ONE_MESSAGE_ID.length; i += 1) {
      const prefix = TWO_TURNS_ONE_MESSAGE_ID.slice(0, i);
      expect(inc.update(prefix)).toEqual(buildTurns(prefix));
    }
  });
});
