/** @vitest-environment jsdom */
import { act, renderHook } from "@testing-library/react";
import type { Dispatch, SetStateAction } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  sessionsApi,
  type SessionEventDTO,
  type SessionListItem,
  type TodoItem,
  type WorkflowState,
} from "@valuz/core";
import type { PendingApprovalEntry } from "./useConversationHistory";
import { useSessionSubscription } from "./useSessionSubscription";

const SESSION_ID = "session-citation-terminal";

const event = (
  seq: number,
  eventType: string,
  payload: Record<string, string> = {},
): SessionEventDTO => ({
  seq,
  event_uid: `event-${seq}`,
  event: { event_type: eventType, payload },
});

describe("useSessionSubscription terminal reconciliation", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("reconciles the persisted turn immediately after session.idle", async () => {
    vi.useFakeTimers();
    let onEvent: ((event: SessionEventDTO) => void) | undefined;
    vi.spyOn(sessionsApi, "subscribeEvents").mockImplementation(
      async (_sessionId, callback, _afterSeq, signal) => {
        onEvent = callback;
        await new Promise<void>((resolve) => {
          signal?.addEventListener("abort", () => resolve(), { once: true });
        });
      },
    );
    const sidecar = event(10, "message.assistant.sidecar", {
      assistant_segment_index: "0",
      citation_bundle: JSON.stringify({ version: 1, citations: [] }),
      message_id: "message-1",
    });
    const listEventsWindow = vi
      .spyOn(sessionsApi, "listEventsWindow")
      .mockResolvedValue({
        session_id: SESSION_ID,
        items: [sidecar],
        has_more: false,
      });

    const abortRef = { current: null as AbortController | null };
    let events: SessionEventDTO[] = [];
    const setEvents: Dispatch<SetStateAction<SessionEventDTO[]>> = vi.fn(
      (next: SetStateAction<SessionEventDTO[]>) => {
        events = typeof next === "function" ? next(events) : next;
      },
    );
    const { result, unmount } = renderHook(() =>
      useSessionSubscription({
        abortRef,
        selectedSessionIdRef: { current: SESSION_ID },
        seenEventUidsRef: { current: new Set<string>() },
        historyCursorRef: { current: 0 },
        streamReconnectAttemptsRef: { current: 0 },
        historyHydrationRef: { current: Promise.resolve() },
        handoffSessionIdRef: { current: null },
        currentClarifyingPendingRef: { current: null },
        ruleIdToPreviewRef: { current: new Map<string, string>() },
        refreshQueueRef: { current: async () => {} },
        pendingApprovals: [] as PendingApprovalEntry[],
        setEvents,
        setPendingUserMessage: vi.fn(),
        setTodos: vi.fn() as Dispatch<SetStateAction<TodoItem[] | null>>,
        setWorkflowStates: vi.fn() as Dispatch<
          SetStateAction<Map<string, WorkflowState>>
        >,
        setPendingApprovals: vi.fn() as Dispatch<
          SetStateAction<PendingApprovalEntry[]>
        >,
        setAutoApprovedNotices: vi.fn(),
        setSending: vi.fn(),
        setSessions: vi.fn() as Dispatch<SetStateAction<SessionListItem[]>>,
      }),
    );

    act(() => result.current.subscribeToSession(SESSION_ID, 0));
    await act(async () => {
      await Promise.resolve();
    });
    expect(onEvent).toBeDefined();

    act(() => onEvent!(event(11, "session.idle", { message_id: "message-1" })));
    await act(async () => {
      await Promise.resolve();
    });

    expect(listEventsWindow).toHaveBeenCalledWith(SESSION_ID, {
      turnLimit: 1,
    });
    expect(events.map((item) => item.event.event_type)).toEqual([
      "message.assistant.sidecar",
      "session.idle",
    ]);

    abortRef.current?.abort();
    unmount();
  });
});
