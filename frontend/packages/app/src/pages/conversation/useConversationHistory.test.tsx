/** @vitest-environment jsdom */
import { act, renderHook } from "@testing-library/react";
import type { Location } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { sessionsApi, type SessionDetail } from "@valuz/core";
import { useConversationHistory } from "./useConversationHistory";

type Params = Parameters<typeof useConversationHistory>[0];

const detail = (id: string): SessionDetail =>
  ({
    id,
    project_id: `project-${id}`,
    name: id,
    status: "idle",
    origin: "user",
    last_user_message_text: null,
    locked_model_id: null,
    locked_provider_id: null,
    runtime_provider: "claude",
    permission_mode: "default",
    updated_at: "2026-01-01T00:00:00Z",
    total_tokens: 0,
  }) as unknown as SessionDetail;

const location = {
  pathname: "/conversation/a",
  search: "",
  hash: "",
  state: null,
  key: "k",
} as Location;

const makeParams = (over: Partial<Params> = {}): Params => ({
  id: "a",
  location,
  searchParams: new URLSearchParams(),
  panelSetCollapsed: vi.fn(),
  selectedSessionIdRef: { current: "a" },
  handoffSessionIdRef: { current: null },
  currentClarifyingPendingRef: { current: null },
  historyCursorRef: { current: 0 },
  seenEventUidsRef: { current: new Set<string>() },
  minSeqRef: { current: Number.POSITIVE_INFINITY },
  hasMoreOlderRef: { current: false },
  streamReconnectAttemptsRef: { current: 0 },
  loadingOlderRef: { current: false },
  userScrolledRef: { current: false },
  scrollContainerRef: { current: null },
  pendingScrollAnchorRef: { current: null },
  isSendInFlightRef: { current: false },
  promotingSessionIdRef: { current: null },
  consumedPromoteSessionIdsRef: { current: new Set<string>() },
  historyHydrationRef: { current: Promise.resolve() },
  setPendingUserMessage: vi.fn(),
  setTurnStartAnchor: vi.fn(),
  setEvents: vi.fn(),
  setTodos: vi.fn(),
  setWorkflowStates: vi.fn(),
  setPendingApprovals: vi.fn(),
  setHasMoreOlder: vi.fn(),
  setLoadingOlder: vi.fn(),
  setError: vi.fn(),
  setLoading: vi.fn(),
  setDraftBootstrapSettled: vi.fn(),
  setProjects: vi.fn(),
  setSessionTriggerMode: vi.fn(),
  setSessionAgentSlug: vi.fn(),
  setSelectedProjectId: vi.fn(),
  setSessions: vi.fn(),
  setSelectedSessionId: vi.fn(),
  setSending: vi.fn(),
  ...over,
});

describe("useConversationHistory — refreshActiveSession", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("drops a detail that resolves after the page switched to another session", async () => {
    let resolveGet!: (value: SessionDetail) => void;
    vi.spyOn(sessionsApi, "get").mockImplementation(
      () =>
        new Promise<SessionDetail>((resolve) => {
          resolveGet = resolve;
        }),
    );
    const params = makeParams({ selectedSessionIdRef: { current: "b" } });
    const { result } = renderHook(() => useConversationHistory(params));

    let refresh!: Promise<void>;
    act(() => {
      refresh = result.current.refreshActiveSession("b");
    });
    // The user opened ``a`` while the GET was in flight.
    params.selectedSessionIdRef.current = "a";
    await act(async () => {
      resolveGet(detail("b"));
      await refresh;
    });

    expect(params.setSelectedSessionId).not.toHaveBeenCalled();
    expect(params.setSessions).not.toHaveBeenCalled();
  });

  it("does not clear the selection for a failed GET of a session the page has left", async () => {
    let rejectGet!: (reason: unknown) => void;
    vi.spyOn(sessionsApi, "get").mockImplementation(
      () =>
        new Promise<SessionDetail>((_resolve, reject) => {
          rejectGet = reject;
        }),
    );
    const params = makeParams({ selectedSessionIdRef: { current: "b" } });
    const { result } = renderHook(() => useConversationHistory(params));

    let refresh!: Promise<void>;
    act(() => {
      refresh = result.current.refreshActiveSession("b");
    });
    params.selectedSessionIdRef.current = "a";
    await act(async () => {
      rejectGet(new Error("gone"));
      await refresh;
    });

    expect(params.setSelectedSessionId).not.toHaveBeenCalled();
    expect(params.setSessions).not.toHaveBeenCalled();
  });

  it("applies the detail when the page still shows the session (control)", async () => {
    vi.spyOn(sessionsApi, "get").mockResolvedValue(detail("b"));
    const params = makeParams({ selectedSessionIdRef: { current: "b" } });
    const { result } = renderHook(() => useConversationHistory(params));

    await act(async () => {
      await result.current.refreshActiveSession("b");
    });

    expect(params.setSessions).toHaveBeenCalledWith([
      expect.objectContaining({ id: "b" }),
    ]);
    expect(params.setSelectedSessionId).toHaveBeenCalledWith("b");
  });
});

describe("useConversationHistory — refreshEvents releases the send-pending flag", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  const window = { session_id: "a", items: [], has_more: false };

  it("switching to a session releases the optimistic busy flag with the pending", async () => {
    vi.spyOn(sessionsApi, "listEventsWindow").mockResolvedValue(window);
    const params = makeParams();
    const { result } = renderHook(() => useConversationHistory(params));

    await act(async () => {
      await result.current.refreshEvents("a");
    });

    expect(params.setPendingUserMessage).toHaveBeenCalledWith(null);
    expect(params.setSending).toHaveBeenCalledWith(false);
  });

  it("a handed-over pending keeps its busy flag on landing", async () => {
    vi.spyOn(sessionsApi, "listEventsWindow").mockResolvedValue(window);
    const params = makeParams({ handoffSessionIdRef: { current: "a" } });
    const { result } = renderHook(() => useConversationHistory(params));

    await act(async () => {
      await result.current.refreshEvents("a");
    });

    expect(params.setPendingUserMessage).not.toHaveBeenCalled();
    expect(params.setSending).not.toHaveBeenCalled();
  });

  it("leaving every session (the draft route) releases it too", async () => {
    const params = makeParams({ handoffSessionIdRef: { current: "a" } });
    const { result } = renderHook(() => useConversationHistory(params));

    await act(async () => {
      await result.current.refreshEvents(null);
    });

    expect(params.setSending).toHaveBeenCalledWith(false);
  });
});
