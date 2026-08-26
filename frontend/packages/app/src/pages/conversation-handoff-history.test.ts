/**
 * Clearing a consumed handoff must not re-navigate.
 *
 * The state has to leave the history entry or a reload replays the send. Doing
 * that with ``navigate(..., { state: null })`` looked equivalent and was not:
 * it mints a fresh ``location.key`` even under ``replace``, ConversationPage's
 * bootstrap effect keys on that, and its ``/conversation/new`` branch awaits
 * ``refreshEvents(null)`` — which nulls the optimistic pending the handoff had
 * just created. The send stayed in flight, so the page showed no bubble, no
 * runtime-startup header, and a live Stop button.
 *
 * This pins the two properties that make the scrub safe: the user state is
 * gone, and React Router's own bookkeeping survives (it is what the router
 * uses to keep its history model in sync — blowing it away would break
 * back/forward).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { dropHandoffFromHistory } from "./conversation-handoff-history";

describe("dropHandoffFromHistory", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it("blanks the user state while preserving the router's bookkeeping", () => {
    const replaceState = vi.fn();
    vi.stubGlobal("window", {
      history: {
        state: { usr: { projectSend: { text: "你好" } }, key: "abc", idx: 3 },
        replaceState,
      },
    });

    dropHandoffFromHistory();

    expect(replaceState).toHaveBeenCalledTimes(1);
    const [next] = replaceState.mock.calls[0] as [Record<string, unknown>];
    expect(next.usr).toBeNull();
    // Router bookkeeping must survive, or back/forward desyncs.
    expect(next.key).toBe("abc");
    expect(next.idx).toBe(3);
  });

  it("tolerates a history entry with no state at all", () => {
    const replaceState = vi.fn();
    vi.stubGlobal("window", {
      history: { state: null, replaceState },
    });

    expect(() => dropHandoffFromHistory()).not.toThrow();
    const [next] = replaceState.mock.calls[0] as [Record<string, unknown>];
    expect(next.usr).toBeNull();
  });
});
