import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";

const { listEvents, subscribeEvents, sendMessage } = vi.hoisted(() => ({
  listEvents: vi.fn(),
  subscribeEvents: vi.fn(),
  sendMessage: vi.fn(),
}));

vi.mock("@valuz/core", async (orig) => {
  const actual = await orig<typeof import("@valuz/core")>();
  return {
    ...actual,
    sessionsApi: { listEvents, subscribeEvents, sendMessage },
  };
});

import { useLeadFollowUpChat } from "./use-lead-follow-up-chat";

const evt = (seq: number, ts: number, userText: string) => ({
  seq,
  timestamp: ts,
  event: { event_type: "message.user", payload: { text: userText } },
});

// Lead assistant message (e.g. the finish-turn closing summary that lands a
// beat after task_completed). ``message_id`` keeps buildTurns from de-duping.
const asst = (seq: number, ts: number, text: string) => ({
  seq,
  timestamp: ts,
  event: {
    event_type: "message.assistant.delta",
    payload: { text, message_id: `m${seq}` },
  },
});

beforeEach(() => {
  listEvents.mockReset();
  subscribeEvents.mockReset();
  sendMessage.mockReset();
});

describe("useLeadFollowUpChat", () => {
  it("only keeps events after sinceTs", async () => {
    listEvents.mockResolvedValue({
      session_id: "s1",
      items: [
        evt(1, 50, "orchestration noise"),
        evt(2, 150, "follow-up question"),
      ],
    });
    subscribeEvents.mockResolvedValue(undefined);
    const { result } = renderHook(() =>
      useLeadFollowUpChat({ leadSessionId: "s1", sinceTs: 100 }),
    );
    await waitFor(() => expect(result.current.turns.length).toBe(1));
    expect(result.current.turns[0].userText).toBe("follow-up question");
  });

  it("drops the lead's leaked closing summary that lands after task_completed", async () => {
    // The finish turn emits its wrap-up assistant_message a beat AFTER
    // task_completed (ts 200 > sinceTs 100), then the user opens the follow-up
    // (ts 300). A raw ``timestamp > sinceTs`` filter would surface the summary
    // at the top; anchoring on the first user message must drop it.
    listEvents.mockResolvedValue({
      session_id: "s1",
      items: [
        evt(1, 50, "original task goal"),
        asst(2, 200, "交付完成。✅ leaked closing summary"),
        evt(3, 300, "please tweak the headline"),
      ],
    });
    subscribeEvents.mockResolvedValue(undefined);
    const { result } = renderHook(() =>
      useLeadFollowUpChat({ leadSessionId: "s1", sinceTs: 100 }),
    );
    await waitFor(() => expect(result.current.turns.length).toBe(1));
    expect(result.current.turns[0].userText).toBe("please tweak the headline");
    const allText = JSON.stringify(result.current.turns);
    expect(allText).not.toContain("leaked closing summary");
    expect(allText).not.toContain("original task goal");
  });

  it("stays empty until the user sends the first follow-up message", async () => {
    // Post-completion the lead's summary exists but the user hasn't replied
    // yet — the follow-up surface must be a clean slate, not a phantom turn.
    listEvents.mockResolvedValue({
      session_id: "s1",
      items: [
        evt(1, 50, "original task goal"),
        asst(2, 200, "交付完成。✅ closing summary"),
      ],
    });
    subscribeEvents.mockResolvedValue(undefined);
    const { result } = renderHook(() =>
      useLeadFollowUpChat({ leadSessionId: "s1", sinceTs: 100 }),
    );
    await waitFor(() => expect(listEvents).toHaveBeenCalled());
    expect(result.current.turns).toEqual([]);
  });

  it("keeps turns empty when sinceTs is null", async () => {
    listEvents.mockResolvedValue({
      session_id: "s1",
      items: [evt(1, 50, "noise"), evt(2, 150, "more noise")],
    });
    subscribeEvents.mockResolvedValue(undefined);
    const { result } = renderHook(() =>
      useLeadFollowUpChat({ leadSessionId: "s1", sinceTs: null }),
    );
    await waitFor(() => expect(listEvents).toHaveBeenCalled());
    expect(result.current.turns).toEqual([]);
  });

  it("send() forwards to sessionsApi.sendMessage and holds the in-flight flag", async () => {
    // ``sending`` deliberately does NOT drop when the HTTP call resolves: the
    // backend schedules the run and replies immediately, so releasing the flag
    // there blanked the in-flight indicator for the entire runtime-startup
    // window. It now stays up until the run reaches a terminal event.
    listEvents.mockResolvedValue({ session_id: "s1", items: [] });
    subscribeEvents.mockResolvedValue(undefined);
    let resolveSend: () => void = () => {};
    sendMessage.mockImplementation(
      () =>
        new Promise<void>((r) => {
          resolveSend = () => r();
        }),
    );
    const { result } = renderHook(() =>
      useLeadFollowUpChat({ leadSessionId: "s1", sinceTs: 0 }),
    );
    await waitFor(() => expect(listEvents).toHaveBeenCalled());
    act(() => {
      void result.current.send("hello");
    });
    await waitFor(() => expect(result.current.sending).toBe(true));
    expect(sendMessage).toHaveBeenCalledWith("s1", "hello");
    act(() => resolveSend());
    await waitFor(() => expect(result.current.awaitingRuntime).toBe(true));
    expect(result.current.sending).toBe(true);
  });

  it("send() ignores whitespace-only input", async () => {
    listEvents.mockResolvedValue({ session_id: "s1", items: [] });
    subscribeEvents.mockResolvedValue(undefined);
    const { result } = renderHook(() =>
      useLeadFollowUpChat({ leadSessionId: "s1", sinceTs: 0 }),
    );
    await waitFor(() => expect(listEvents).toHaveBeenCalled());
    await act(async () => {
      await result.current.send("   ");
    });
    expect(sendMessage).not.toHaveBeenCalled();
  });

  it("no-ops when leadSessionId is null", () => {
    renderHook(() => useLeadFollowUpChat({ leadSessionId: null, sinceTs: 0 }));
    expect(listEvents).not.toHaveBeenCalled();
  });

  // ── Runtime-startup window ────────────────────────────────────────────
  //
  // POST /messages returns as soon as the run is scheduled, well before the
  // kernel writes ``message.user``. Without an optimistic turn the user's own
  // message is invisible for that whole window — milliseconds locally, tens of
  // seconds while a sandbox boots.

  /** Open a stream whose ``onEvent`` callback the test drives by hand. */
  const openLiveStream = () => {
    let emit: (e: unknown) => void = () => {};
    subscribeEvents.mockImplementation(
      (_id: string, cb: (e: unknown) => void) => {
        emit = cb;
        return new Promise<void>(() => {}); // a live stream never resolves
      },
    );
    return (e: unknown) => act(() => emit(e));
  };

  it("shows the sent message while the runtime is still starting", async () => {
    listEvents.mockResolvedValue({ session_id: "s1", items: [] });
    openLiveStream();
    sendMessage.mockResolvedValue(undefined);
    const { result } = renderHook(() =>
      useLeadFollowUpChat({ leadSessionId: "s1", sinceTs: 0 }),
    );
    await waitFor(() => expect(listEvents).toHaveBeenCalled());

    await act(async () => {
      await result.current.send("please tweak the headline");
    });

    // HTTP has already resolved, but no echo has arrived yet.
    expect(result.current.awaitingRuntime).toBe(true);
    expect(result.current.turns.map((t) => t.userText)).toEqual([
      "please tweak the headline",
    ]);
    // ``sending`` must stay true across the gap or the turn loses its
    // in-flight indicator between the HTTP reply and the echo.
    expect(result.current.sending).toBe(true);
  });

  it("hands the startup window's stamp to the echoed turn", async () => {
    listEvents.mockResolvedValue({ session_id: "s1", items: [] });
    const emit = openLiveStream();
    sendMessage.mockResolvedValue(undefined);
    const { result } = renderHook(() =>
      useLeadFollowUpChat({ leadSessionId: "s1", sinceTs: 0 }),
    );
    await waitFor(() => expect(listEvents).toHaveBeenCalled());

    const before = Date.now();
    await act(async () => {
      await result.current.send("please tweak the headline");
    });
    const after = Date.now();

    // The kernel finally stamps the turn — far later than the send.
    emit(evt(1, after + 20_000, "please tweak the headline"));

    await waitFor(() => expect(result.current.awaitingRuntime).toBe(false));
    // Exactly one turn: the optimistic one gave way rather than doubling up.
    expect(result.current.turns.length).toBe(1);
    const sentAt = result.current.turns[0].clientSentAtMs;
    expect(sentAt).toBeGreaterThanOrEqual(before);
    expect(sentAt).toBeLessThanOrEqual(after);

    // …and the in-flight flag still releases at the end of the run, so
    // holding it through startup can't leave the composer stuck disabled.
    emit({
      seq: 2,
      timestamp: after + 25_000,
      event: { event_type: "session.idle", payload: {} },
    });
    await waitFor(() => expect(result.current.sending).toBe(false));
  });

  it("drops the optimistic turn when the send fails", async () => {
    listEvents.mockResolvedValue({ session_id: "s1", items: [] });
    openLiveStream();
    sendMessage.mockRejectedValue(new Error("boom"));
    const { result } = renderHook(() =>
      useLeadFollowUpChat({ leadSessionId: "s1", sinceTs: 0 }),
    );
    await waitFor(() => expect(listEvents).toHaveBeenCalled());

    // No echo is coming, so a lingering turn would sit on "正在启动…" forever.
    await act(async () => {
      await expect(result.current.send("please tweak")).rejects.toThrow("boom");
    });

    expect(result.current.turns).toEqual([]);
    expect(result.current.awaitingRuntime).toBe(false);
  });
});
