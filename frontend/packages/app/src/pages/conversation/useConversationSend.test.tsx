/** @vitest-environment jsdom */
import { act, renderHook } from "@testing-library/react";
import { toast } from "sonner";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  projectsApi,
  sessionsApi,
  type ProjectDetail,
  type SessionDetail,
} from "@valuz/core";
import { NEW_SESSION_ID } from "./session-events";
import { useConversationSend } from "./useConversationSend";

// ``refreshRunningRuns`` pokes the shared runs poller (a network round-trip
// with its own lifecycle) — irrelevant here, and noisy under jsdom.
vi.mock("@valuz/core", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@valuz/core")>()),
  refreshRunningRuns: vi.fn(),
}));

type Params = Parameters<typeof useConversationSend>[0];

const detail = (id: string, status = "created"): SessionDetail =>
  ({
    id,
    project_id: `project-${id}`,
    name: id,
    status,
    origin: "user",
    last_user_message_text: null,
    locked_model_id: null,
    locked_provider_id: null,
    runtime_provider: "claude",
    permission_mode: "default",
    updated_at: "2026-01-01T00:00:00Z",
    total_tokens: 0,
  }) as unknown as SessionDetail;

const makeParams = (over: Partial<Params> = {}): Params => ({
  id: NEW_SESSION_ID,
  selectedSession: null,
  selectedSessionId: null,
  selectedProjectId: "chat-default",
  activeProject: null,
  isSkillCreatorMode: false,
  skillKindParam: null,
  skillProjectParam: null,
  selectedAgentSlug: null,
  composerTouched: false,
  selectedProviderId: null,
  selectedModelId: null,
  selectedRuntimeId: null,
  selectedEffort: null,
  selectedPermissionMode: "default",
  selectedSessionMode: "default",
  selectedMcpSlugs: [],
  selectedComposerSkill: null,
  hostRef: null,
  draft: "hello",
  isBusy: false,
  turns: [],
  effectiveTurns: [],
  sessionAttachments: [],
  sidebarSessions: [],
  resolveExecTarget: () => undefined,
  attachKbDocs: vi.fn() as unknown as Params["attachKbDocs"],
  attachLocalFiles: vi.fn() as unknown as Params["attachLocalFiles"],
  removeSessionAttachmentRow: vi.fn(async () => {}),
  claimStagedAttachments: () => [],
  restageAttachments: vi.fn(),
  settleAttachments: vi.fn(),
  refreshBoundAttachments: vi.fn(async () => {}),
  refreshEvents: vi.fn(async () => {}),
  refreshActiveSession: vi.fn(async () => {}),
  fetchSidebarSessions: vi.fn(async () => {}),
  setSidebarSessions: vi.fn(),
  upsertProject: vi.fn(),
  panelSetCollapsed: vi.fn(),
  selectedSessionIdRef: { current: null },
  skipNextSessionStateResetRef: { current: false },
  projectSendHandoffRef: { current: null },
  handoffSessionIdRef: { current: null },
  promotingSessionIdRef: { current: null },
  routeIdRef: { current: NEW_SESSION_ID },
  routeEpochRef: { current: 0 },
  isSendInFlightRef: { current: false },
  historyCursorRef: { current: 0 },
  revealPanelOnSessionChangeRef: { current: false },
  pinNextTurnToTopRef: { current: false },
  keepCurrentTurnAtTopRef: { current: false },
  interruptRef: { current: () => {} },
  setSelectedSessionId: vi.fn(),
  setSessionAgentSlug: vi.fn(),
  setSelectedProjectId: vi.fn(),
  setSessions: vi.fn(),
  setProjects: vi.fn(),
  setEvents: vi.fn(),
  setPendingUserMessage: vi.fn(),
  setTurnStartAnchor: vi.fn(),
  setSending: vi.fn(),
  setError: vi.fn(),
  setDraft: vi.fn(),
  setSelectedComposerSkill: vi.fn(),
  setRetryCounts: vi.fn(),
  setKbPickerOpen: vi.fn(),
  handleSend: vi.fn(),
  onSessionPromoted: vi.fn(),
  ...over,
});

/** Let every pending microtask (the mocked round-trips) settle. */
const flush = () => new Promise<void>((resolve) => setTimeout(resolve, 0));

/** The routing hook's reaction to a promotion, once the navigation commits. */
const promoteRoute = (params: Params) =>
  vi.fn((newId: string) => {
    params.routeIdRef.current = newId;
    params.routeEpochRef.current += 1;
  });

/** What a sidebar click does to the page: the route effect flips the refs
 *  synchronously and the bootstrap that follows selects the other session. */
const switchTo = (params: Params, sessionId: string) => {
  params.routeIdRef.current = sessionId;
  params.routeEpochRef.current += 1;
  params.selectedSessionIdRef.current = sessionId;
};

describe("useConversationSend — a send that outlives a session switch", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("switch during the POST: the turn goes out, the page is not flipped onto it", async () => {
    const created = detail("b");
    vi.spyOn(sessionsApi, "create").mockResolvedValue(created);
    vi.spyOn(projectsApi, "get").mockResolvedValue({
      id: created.project_id,
    } as unknown as ProjectDetail);
    let resolveSend!: (value: SessionDetail) => void;
    const sendMessage = vi.spyOn(sessionsApi, "sendMessage").mockImplementation(
      () =>
        new Promise<SessionDetail>((resolve) => {
          resolveSend = resolve;
        }),
    );
    const params = makeParams();
    params.onSessionPromoted = promoteRoute(params);
    const { result } = renderHook(() => useConversationSend(params));

    let send!: Promise<void>;
    await act(async () => {
      send = result.current.performSend();
      await flush();
    });
    // The draft was still open when the create resolved: adopted + promoted.
    expect(params.onSessionPromoted).toHaveBeenCalledWith("b", {
      skillCreator: false,
    });
    expect(params.setSelectedSessionId).toHaveBeenCalledWith("b");
    expect(params.handoffSessionIdRef.current).toBe("b");
    expect(sendMessage).toHaveBeenCalledWith(
      "b",
      "hello",
      null,
      null,
      null,
      [],
    );
    vi.mocked(params.setSelectedSessionId).mockClear();
    vi.mocked(params.setSessions).mockClear();

    // The user opens another conversation while the POST is in flight.
    switchTo(params, "a");
    await act(async () => {
      resolveSend(detail("b", "running"));
      await send;
    });

    // The sidebar still learns that b is running…
    expect(params.setSidebarSessions).toHaveBeenLastCalledWith([
      expect.objectContaining({ id: "b", status: "running" }),
    ]);
    // …but nothing page-scoped follows it.
    expect(params.setSelectedSessionId).not.toHaveBeenCalled();
    expect(params.setSessions).not.toHaveBeenCalled();
    expect(params.refreshBoundAttachments).not.toHaveBeenCalled();
    expect(params.settleAttachments).toHaveBeenCalledTimes(1);
    expect(params.isSendInFlightRef.current).toBe(false);
  });

  it("switch during the create: the session is minted and sent to, never selected or navigated to", async () => {
    const created = detail("b");
    let resolveCreate!: (value: SessionDetail) => void;
    vi.spyOn(sessionsApi, "create").mockImplementation(
      () =>
        new Promise<SessionDetail>((resolve) => {
          resolveCreate = resolve;
        }),
    );
    const projectGet = vi.spyOn(projectsApi, "get").mockResolvedValue({
      id: created.project_id,
    } as unknown as ProjectDetail);
    const sendMessage = vi
      .spyOn(sessionsApi, "sendMessage")
      .mockResolvedValue(detail("b", "running"));
    const params = makeParams();
    params.onSessionPromoted = promoteRoute(params);
    const { result } = renderHook(() => useConversationSend(params));

    let send!: Promise<void>;
    await act(async () => {
      send = result.current.performSend();
      await flush();
    });
    switchTo(params, "a");
    await act(async () => {
      resolveCreate(created);
      await send;
    });

    expect(sendMessage).toHaveBeenCalledWith(
      "b",
      "hello",
      null,
      null,
      null,
      [],
    );
    // Reachable from the sidebar: the row and its managed project.
    expect(params.setSidebarSessions).toHaveBeenCalledWith([
      expect.objectContaining({ id: "b" }),
    ]);
    expect(projectGet).toHaveBeenCalledWith(created.project_id);
    expect(params.upsertProject).toHaveBeenCalledWith(
      expect.objectContaining({ id: created.project_id }),
    );
    expect(params.fetchSidebarSessions).toHaveBeenCalled();
    // The page stayed on the conversation the user opened.
    expect(params.onSessionPromoted).not.toHaveBeenCalled();
    expect(params.setSelectedSessionId).not.toHaveBeenCalled();
    expect(params.setSessions).not.toHaveBeenCalled();
    expect(params.setSelectedProjectId).not.toHaveBeenCalled();
    expect(params.selectedSessionIdRef.current).toBe("a");
    expect(params.handoffSessionIdRef.current).toBeNull();
    expect(params.promotingSessionIdRef.current).toBeNull();
    expect(params.skipNextSessionStateResetRef.current).toBe(false);
  });

  it("no switch: the draft is promoted and the page follows the session (control)", async () => {
    const created = detail("b");
    vi.spyOn(sessionsApi, "create").mockResolvedValue(created);
    vi.spyOn(projectsApi, "get").mockResolvedValue({
      id: created.project_id,
    } as unknown as ProjectDetail);
    vi.spyOn(sessionsApi, "sendMessage").mockResolvedValue(
      detail("b", "running"),
    );
    const params = makeParams();
    params.onSessionPromoted = promoteRoute(params);
    const { result } = renderHook(() => useConversationSend(params));

    await act(async () => {
      await result.current.performSend();
    });

    expect(params.onSessionPromoted).toHaveBeenCalledWith("b", {
      skillCreator: false,
    });
    expect(params.setSelectedSessionId).toHaveBeenLastCalledWith("b");
    expect(params.setSessions).toHaveBeenCalled();
    expect(params.refreshBoundAttachments).toHaveBeenCalledWith("b");
    expect(params.selectedSessionIdRef.current).toBe("b");
    expect(params.handoffSessionIdRef.current).toBe("b");
  });

  it("a follow-up on an open session: switching away during the POST leaves the new page alone", async () => {
    const open = detail("s", "idle");
    let resolveSend!: (value: SessionDetail) => void;
    vi.spyOn(sessionsApi, "sendMessage").mockImplementation(
      () =>
        new Promise<SessionDetail>((resolve) => {
          resolveSend = resolve;
        }),
    );
    const params = makeParams({
      id: "s",
      selectedSession: { ...open, status: "idle" },
      selectedSessionId: "s",
      selectedSessionIdRef: { current: "s" },
      routeIdRef: { current: "s" },
      routeEpochRef: { current: 4 },
    });
    const { result } = renderHook(() => useConversationSend(params));

    let send!: Promise<void>;
    await act(async () => {
      send = result.current.performSend();
      await flush();
    });
    vi.mocked(params.setSessions).mockClear();
    switchTo(params, "a");
    await act(async () => {
      resolveSend(detail("s", "running"));
      await send;
    });

    expect(params.setSelectedSessionId).not.toHaveBeenCalled();
    expect(params.setSessions).not.toHaveBeenCalled();
    expect(params.setSidebarSessions).toHaveBeenLastCalledWith([
      expect.objectContaining({ id: "s", status: "running" }),
    ]);
  });

  it("a failed send after a switch keeps its cleanup off the page the user is on", async () => {
    const created = detail("b");
    vi.spyOn(sessionsApi, "create").mockResolvedValue(created);
    vi.spyOn(projectsApi, "get").mockResolvedValue({
      id: created.project_id,
    } as unknown as ProjectDetail);
    let rejectSend!: (reason: unknown) => void;
    vi.spyOn(sessionsApi, "sendMessage").mockImplementation(
      () =>
        new Promise<SessionDetail>((_resolve, reject) => {
          rejectSend = reject;
        }),
    );
    const toastError = vi.spyOn(toast, "error").mockReturnValue("t" as never);
    const params = makeParams();
    params.onSessionPromoted = promoteRoute(params);
    const { result } = renderHook(() => useConversationSend(params));

    let send!: Promise<void>;
    await act(async () => {
      send = result.current.performSend();
      await flush();
    });
    vi.mocked(params.setSending).mockClear();
    vi.mocked(params.setPendingUserMessage).mockClear();
    switchTo(params, "a");
    await act(async () => {
      rejectSend(new Error("boom"));
      await send;
    });

    // The failure is still surfaced…
    expect(toastError).toHaveBeenCalledWith("boom");
    expect(params.restageAttachments).toHaveBeenCalled();
    // …but the page-scoped reset would now hit the OTHER conversation.
    expect(params.setError).not.toHaveBeenCalledWith("boom");
    expect(params.setSending).not.toHaveBeenCalled();
    expect(params.setPendingUserMessage).not.toHaveBeenCalled();
    expect(params.refreshActiveSession).not.toHaveBeenCalled();
    expect(params.isSendInFlightRef.current).toBe(false);
  });

  it("a failed send with no switch resets the page as before (control)", async () => {
    vi.spyOn(sessionsApi, "create").mockResolvedValue(detail("b"));
    vi.spyOn(projectsApi, "get").mockResolvedValue({
      id: "project-b",
    } as unknown as ProjectDetail);
    vi.spyOn(sessionsApi, "sendMessage").mockRejectedValue(new Error("boom"));
    vi.spyOn(toast, "error").mockReturnValue("t" as never);
    const params = makeParams();
    params.onSessionPromoted = promoteRoute(params);
    const { result } = renderHook(() => useConversationSend(params));

    await act(async () => {
      await result.current.performSend();
    });

    expect(params.setError).toHaveBeenCalledWith("boom");
    expect(params.setSending).toHaveBeenLastCalledWith(false);
    expect(params.setPendingUserMessage).toHaveBeenLastCalledWith(null);
  });
});
