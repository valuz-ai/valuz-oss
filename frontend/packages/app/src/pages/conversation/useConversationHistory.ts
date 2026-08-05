import { useCallback, useRef } from "react";
import type { Dispatch, SetStateAction } from "react";
import type { Location } from "react-router-dom";
import {
  getEntityOrigin,
  mergeEventWindow,
  parseActionResolved,
  parseRequiresAction,
  parseTodosUpdate,
  projectsApi,
  recordEntityOrigin,
  sessionsApi,
  SESSION_ACTION_RESOLVED_EVENT,
  SESSION_REQUIRES_ACTION_EVENT,
  type ProjectListItem,
  type SessionEventDTO,
  type SessionListItem,
  type TodoItem,
  type WorkflowState,
} from "@valuz/core";
import type { ApprovalCardSubject, ApprovalResolvedDecision } from "@valuz/ui";
import { t as _t } from "@valuz/shared/i18n";
import type { I18nKey } from "@valuz/shared";
import { shouldRefreshConversationHistory } from "../conversation-loading";
import { NEW_SESSION_ID, sessionDetailToListItem } from "./session-events";

// Initial load fetches the latest ``TURN_PAGE_SIZE`` turns through the
// turn-aligned window endpoint instead of pulling every event. Earlier
// turns are loaded on demand by ``loadOlderTurns`` when the user
// scrolls toward the top. The old "fetch all events with no cursor"
// flow silently truncated long sessions because the backend caps the
// legacy linear endpoint at 500 rows ASC.
export const TURN_PAGE_SIZE = 20;

// Structural copy of ``ConversationPage``'s component-local
// ``PendingApprovalEntry`` interface (declared inside the component body,
// so it cannot be imported here). TypeScript's structural typing keeps the
// page's ``setPendingApprovals`` assignable to the param typed with this
// copy. Keep the two shapes in sync.
export interface PendingApprovalEntry {
  pendingId: string;
  subject: ApprovalCardSubject;
  payload: Record<string, unknown>;
  // V5+d008b53 (v2 approval contract):
  availableDecisions: string[];
  sessionRulePreviewDisplay: string | null;
  originalInput: Record<string, unknown> | null;
  receivedAt?: number; // Unix epoch ms (UTC)
  submitting?: boolean;
  // Resolution state. Populated when the matching
  // ``action_resolved`` lands. Drives the swap from the full
  // ``ApprovalCard`` to the compact ``ApprovalResolvedStrip``.
  answered?: boolean;
  decision?: ApprovalResolvedDecision;
  rejectMessage?: string | null;
}

type ConversationHistoryParams = {
  /** Route param (``/conversation/{id}``), defaulted to ``NEW_SESSION_ID``. */
  id: string;
  location: Location;
  searchParams: URLSearchParams;
  panelSetCollapsed: (collapsed: boolean) => void;
  selectedSessionIdRef: { current: string | null };
  handoffSessionIdRef: { current: string | null };
  currentClarifyingPendingRef: { current: string | null };
  historyCursorRef: { current: number };
  seenEventUidsRef: { current: Set<string> };
  minSeqRef: { current: number };
  hasMoreOlderRef: { current: boolean };
  streamReconnectAttemptsRef: { current: number };
  loadingOlderRef: { current: boolean };
  userScrolledRef: { current: boolean };
  scrollContainerRef: { current: HTMLDivElement | null };
  pendingScrollAnchorRef: {
    current: { oldScrollHeight: number; oldScrollTop: number } | null;
  };
  isSendInFlightRef: { current: boolean };
  promotingSessionIdRef: { current: string | null };
  consumedPromoteSessionIdsRef: { current: Set<string> };
  /** Stays declared in the page — ``subscribeToSession`` reads it too. */
  historyHydrationRef: { current: Promise<void> };
  setPendingUserMessage: (value: null) => void;
  setTurnStartAnchor: (value: null) => void;
  setEvents: Dispatch<SetStateAction<SessionEventDTO[]>>;
  setTodos: Dispatch<SetStateAction<TodoItem[] | null>>;
  setWorkflowStates: Dispatch<SetStateAction<Map<string, WorkflowState>>>;
  setPendingApprovals: Dispatch<SetStateAction<PendingApprovalEntry[]>>;
  setHasMoreOlder: Dispatch<SetStateAction<boolean>>;
  setLoadingOlder: Dispatch<SetStateAction<boolean>>;
  setError: Dispatch<SetStateAction<string | null>>;
  setLoading: Dispatch<SetStateAction<boolean>>;
  /** Narrower than the raw ``useState`` setter on purpose: the page threads
   *  a deferred wrapper because ``draftBootstrapSettled`` is declared after
   *  the hook call site (block-scoped — a direct reference would TDZ). */
  setDraftBootstrapSettled: (settled: boolean) => void;
  setProjects: Dispatch<SetStateAction<ProjectListItem[]>>;
  setSessionTriggerMode: Dispatch<SetStateAction<string | null>>;
  setSessionAgentSlug: Dispatch<SetStateAction<string | null>>;
  setSelectedProjectId: Dispatch<SetStateAction<string | null>>;
  setSessions: Dispatch<SetStateAction<SessionListItem[]>>;
  setSelectedSessionId: Dispatch<SetStateAction<string | null>>;
};

/**
 * ── Conversation history loading ─────────────────────────────────────
 *
 * Owns the turn-window history pipeline of the conversation page: the
 * transcript window refresh (``refreshEvents``), the upward "load older
 * turns" pager, the single-session re-fetch, and the route-driven
 * ``bootstrap``. Bodies are moved verbatim from ``ConversationPage``.
 */
export function useConversationHistory({
  id,
  location,
  searchParams,
  panelSetCollapsed,
  selectedSessionIdRef,
  handoffSessionIdRef,
  currentClarifyingPendingRef,
  historyCursorRef,
  seenEventUidsRef,
  minSeqRef,
  hasMoreOlderRef,
  streamReconnectAttemptsRef,
  loadingOlderRef,
  userScrolledRef,
  scrollContainerRef,
  pendingScrollAnchorRef,
  isSendInFlightRef,
  promotingSessionIdRef,
  consumedPromoteSessionIdsRef,
  historyHydrationRef,
  setPendingUserMessage,
  setTurnStartAnchor,
  setEvents,
  setTodos,
  setWorkflowStates,
  setPendingApprovals,
  setHasMoreOlder,
  setLoadingOlder,
  setError,
  setLoading,
  setDraftBootstrapSettled,
  setProjects,
  setSessionTriggerMode,
  setSessionAgentSlug,
  setSelectedProjectId,
  setSessions,
  setSelectedSessionId,
}: ConversationHistoryParams) {
  // A same-session bootstrap may skip REST history only after that session's
  // window completed successfully. Keeping selection and hydration separate
  // makes a failed/blank load retryable without a hard refresh.
  const historyHydratedSessionIdRef = useRef<string | null>(null);

  const refreshEventsInner = useCallback(async (sessionId: string | null) => {
    if (sessionId === null || selectedSessionIdRef.current === sessionId) {
      historyHydratedSessionIdRef.current = null;
    }
    // Switching sessions invalidates any optimistic pending message —
    // it belongs to whatever session was active before, not this one. Same
    // for the send anchor: another session's turn must not inherit this
    // session's send time.
    //
    // Exception: a pending handed over WITH this navigation belongs to the
    // session being loaded, not the previous one. Bootstrap calls this on
    // landing, so without the guard the handoff would be seeded and then wiped
    // a beat later, and the project-detail send would still land on a blank
    // conversation.
    if (sessionId === null || handoffSessionIdRef.current !== sessionId) {
      setPendingUserMessage(null);
      setTurnStartAnchor(null);
    }
    // CRITICAL: clear ``events`` synchronously BEFORE awaiting the
    // network fetch. The URL-change handler updates ``selectedSessionId``
    // and then calls this function, but ``selectedSessionId`` and
    // ``events`` would otherwise commit in different render cycles —
    // for the few hundred ms that the new ``listEventsWindow`` round-
    // trip takes, the page renders the NEW session's id (so the title,
    // sidebar highlight, right panel all flip) against the OLD
    // session's events (so the conversation body still shows the
    // previous chat). The user perceives this as the previous turns
    // "stacking" under the new session header.
    setEvents([]);
    setTodos(null);
    // Workflow progress is live-only and per-session — drop the previous
    // session's snapshots so a Workflow tool card can't leak across a switch.
    setWorkflowStates(new Map());
    // The clarifying-pending ref is keyed to whatever session we're
    // leaving. Reset it so a stale pending_id from the previous session
    // can't get POSTed against the new one — and so the post-fetch walk
    // below has a clean slate to repopulate from history.
    currentClarifyingPendingRef.current = null;
    // Same rationale for the approval tray: clear it so a card parked in the
    // session we're leaving can neither linger against — nor POST its
    // pending_id against — the session we're switching to. The post-fetch
    // walk below rebuilds it from the new session's own history.
    setPendingApprovals([]);
    historyCursorRef.current = 0;
    seenEventUidsRef.current.clear();
    minSeqRef.current = Number.POSITIVE_INFINITY;
    hasMoreOlderRef.current = false;
    setHasMoreOlder(false);
    streamReconnectAttemptsRef.current = 0;
    if (!sessionId) {
      return;
    }
    try {
      const response = await sessionsApi.listEventsWindow(sessionId, {
        turnLimit: TURN_PAGE_SIZE,
      });
      // Race guard: another session switch may have completed while
      // we were awaiting this fetch. Discard stale results so we
      // don't briefly render the previous session's events under the
      // new session's header. Without this, two fast-clicked switches
      // can resolve out of order and the slower fetch would overwrite
      // the faster one with mismatched events.
      if (selectedSessionIdRef.current !== sessionId) return;
      // Merge, don't clobber: a resume subscription may already be streaming
      // this session (its live deltas + replayed rows landed in ``events``
      // between our synchronous clear above and this resolve). Replacing with
      // the window snapshot wiped that streamed content — the refresh-mid-turn
      // blank/inconsistent transcript. ``mergeEventWindow`` only inserts the
      // persisted rows we don't have yet, in order.
      setEvents((prev) => mergeEventWindow(prev, response.items));
      if (response.items.length > 0) {
        // ``listEventsWindow`` items are HISTORY-space, so seeding the
        // history cursor from the last one is safe. Forward-only: a
        // concurrent REST reconcile/poll may have advanced the cursor past
        // this snapshot — never rewind it (a rewound cursor makes the next
        // gap-fill refetch rows we already rendered).
        historyCursorRef.current = Math.max(
          historyCursorRef.current,
          response.items[response.items.length - 1].seq,
        );
        minSeqRef.current = Math.min(minSeqRef.current, response.items[0].seq);
      }
      hasMoreOlderRef.current = response.has_more;
      setHasMoreOlder(response.has_more);
      // Walk the historical events for the most recent
      // ``session.todos.update`` snapshot so a re-entry after refresh
      // restores the panel's state without waiting for the next turn.
      // Same walk also rebuilds the clarifying-pending ref so the
      // AskUserQuestionCard's submit handler can POST /actions with the
      // right pending_id when the user opens a session that's *already*
      // parked on an unanswered question — without this, the SSE-only
      // ref stays null on cold open and the 回答 click silently fails
      // with a "common.error" toast (the handler early-returns at
      // ``pendingId`` is null).
      let lastTodos: TodoItem[] | null = null;
      let unresolvedClarifyingPending: string | null = null;
      const resolvedPendingIds = new Set<string>();
      // First pass — collect the set of pending_ids that have already
      // been resolved (so we don't carry a "live" pending forward when
      // the kernel has already moved past it).
      for (const ev of response.items) {
        if (ev.event.event_type === SESSION_ACTION_RESOLVED_EVENT) {
          const ar = parseActionResolved(ev);
          if (ar) resolvedPendingIds.add(ar.pending_id);
        }
      }
      // Rebuild the approval tray from history too (the sibling of the
      // clarifying-ref rebuild). ``pendingApprovals`` was otherwise
      // live-SSE-only: a card parked in a session that is later reopened —
      // or switched back to — would vanish with no way to approve it. Since
      // an ``exit_plan_mode`` pending always parks and waits on slow human
      // review, it is the one most likely to outlive its session context.
      const rebuiltApprovals: PendingApprovalEntry[] = [];
      for (const ev of response.items) {
        const parsedTodos = parseTodosUpdate(ev);
        if (parsedTodos) lastTodos = parsedTodos;
        if (ev.event.event_type === SESSION_REQUIRES_ACTION_EVENT) {
          const ra = parseRequiresAction(ev);
          if (!ra || resolvedPendingIds.has(ra.pending_id)) continue;
          if (ra.subject === "clarifying_questions") {
            unresolvedClarifyingPending = ra.pending_id;
          } else {
            rebuiltApprovals.push({
              pendingId: ra.pending_id,
              subject: ra.subject as ApprovalCardSubject,
              payload: ra.payload,
              availableDecisions: ra.available_decisions,
              sessionRulePreviewDisplay:
                ra.session_rule_preview?.display ?? null,
              originalInput: ra.original_input,
              receivedAt: ev.timestamp,
            });
          }
        }
      }
      // Carry-forward, matching the other two setTodos sites (detail hydrate
      // + live SSE): the persisted ``detail.todos`` snapshot is the
      // authoritative source and history/live frames only ever refine it.
      // The window covers the last TURN_PAGE_SIZE turns — on a long session
      // it may contain no parseable ``session.todos.update`` frame at all,
      // and an unconditional write here clobbered the detail hydrate with
      // null on every cold re-open ("No todos yet" while sessions.todos is
      // intact). An agent that *clears* its todos emits ``[]`` (truthy),
      // which still lands.
      if (lastTodos) setTodos(lastTodos);
      currentClarifyingPendingRef.current = unresolvedClarifyingPending;
      setPendingApprovals(rebuiltApprovals);
      historyHydratedSessionIdRef.current = sessionId;
    } catch {
      if (selectedSessionIdRef.current !== sessionId) return;
      setEvents([]);
      historyCursorRef.current = 0;
      seenEventUidsRef.current.clear();
      minSeqRef.current = Number.POSITIVE_INFINITY;
      hasMoreOlderRef.current = false;
      setHasMoreOlder(false);
      setTodos(null);
      // A failed history load used to be swallowed here, leaving an
      // existing session rendering a permanently blank transcript with
      // no error and no retry path (the empty state is welcome-gated to
      // ``/conversation/new`` only). Surface it so the user sees an
      // error card instead of a white page.
      setError(_t("conversation.historyLoadFailed" as I18nKey));
    }
  }, []);

  // Public wrapper: registers the in-flight load as the hydration gate
  // SYNCHRONOUSLY (so a subscription started in the same tick already waits on
  // it), then runs the load. Same signature and identity stability as before.
  const refreshEvents = useCallback(
    async (sessionId: string | null) => {
      const load = refreshEventsInner(sessionId);
      historyHydrationRef.current = load.catch(() => {});
      await load;
    },
    [refreshEventsInner],
  );

  // Upward pagination: triggered by the top sentinel's IntersectionObserver
  // (and safe to call manually). Captures the scroll anchor BEFORE the
  // state update so the layout effect below can restore the user's
  // scroll position once React commits the prepend.
  //
  // Skipped when the scroller is far from the top: the observer can fire
  // during transient layout shifts (initial render before the
  // auto-scroll-to-bottom effect lands; ResizeObserver-driven
  // re-measurement) where the sentinel is briefly visible but the user
  // hasn't actually scrolled there. Without this guard, those firings
  // chain into a cascade that pre-loads the entire session in one
  // microtask and defeats the whole point of pagination.
  const loadOlderTurns = useCallback(async () => {
    const sessionId = selectedSessionIdRef.current;
    if (!sessionId) return;
    if (loadingOlderRef.current) return;
    if (!hasMoreOlderRef.current) return;
    if (!Number.isFinite(minSeqRef.current) || minSeqRef.current <= 0) return;

    if (!userScrolledRef.current) return;
    const el = scrollContainerRef.current;
    if (el && el.scrollTop > 200) return;

    loadingOlderRef.current = true;
    setLoadingOlder(true);
    const oldScrollHeight = el?.scrollHeight ?? 0;
    const oldScrollTop = el?.scrollTop ?? 0;

    try {
      const response = await sessionsApi.listEventsWindow(sessionId, {
        beforeSeq: minSeqRef.current,
        turnLimit: TURN_PAGE_SIZE,
      });
      // Staleness guard: a session switch may have landed while this page
      // was in flight — prepending the OLD session's turns into the NEW
      // session's transcript (and poisoning ``minSeqRef``, which the switch
      // just reset) mixes histories across sessions.
      if (selectedSessionIdRef.current !== sessionId) return;
      if (response.items.length > 0) {
        // Defensive dedup: SSE shouldn't backfill historical events but
        // a slow turn could in theory race with this fetch.
        setEvents((prev) => {
          // uid-first dedup (seq fallback restricted to uid-less rows —
          // history and live seqs are independent spaces).
          const seenUids = new Set<string>();
          const seenLegacySeqs = new Set<number>();
          for (const p of prev) {
            if (p.event_uid) seenUids.add(p.event_uid);
            else if (p.seq > 0) seenLegacySeqs.add(p.seq);
          }
          const fresh = response.items.filter((e) =>
            e.event_uid
              ? !seenUids.has(e.event_uid)
              : !seenLegacySeqs.has(e.seq),
          );
          if (fresh.length === 0) return prev;
          return [...fresh, ...prev];
        });
        minSeqRef.current = response.items[0].seq;
        pendingScrollAnchorRef.current = { oldScrollHeight, oldScrollTop };
      }
      hasMoreOlderRef.current = response.has_more;
      setHasMoreOlder(response.has_more);
    } catch {
      // Non-fatal — the user can scroll away and back to retry the
      // sentinel observer; surfacing a toast for a background pager
      // would be noisier than helpful.
    } finally {
      loadingOlderRef.current = false;
      setLoadingOlder(false);
    }
  }, []);

  /**
   * Re-fetch the *one* session this page is currently rendering.
   *
   * Replaces the previous ``refreshSessions(projectId, preferredId)``
   * which did ``GET /v1/sessions?project_id=…`` and ``find()``-d the
   * row we cared about. That pattern had two problems:
   *
   *   1. It needed a project id to call the list endpoint, but the
   *      ``selectedProjectId`` captured in callback closures could be
   *      stale (the ``"chat-default"`` sentinel before
   *      ``ensureSession``'s state update settled). The post-turn
   *      refresh would then list against a non-existent project id,
   *      get back an empty list, and silently null-out
   *      ``selectedSessionId`` — the symptom users saw as "refresh"
   *      ".
   *   2. The page only ever renders one session at a time. Listing a
   *      whole project's sessions just to ``find()`` one was
   *      wasteful and brittle.
   *
   * The clean replacement uses ``GET /v1/sessions/{id}`` directly. No
   * project id needed; one round-trip; the result feeds both the
   * one-row ``sessions[]`` (the optimistic-merge code paths still
   * mutate this) and ``selectedSessionId``.
   */
  const refreshActiveSession = useCallback(
    async (sessionId: string | null) => {
      if (!sessionId) {
        setSessions([]);
        setSelectedSessionId(null);
        return;
      }
      try {
        const detail = await sessionsApi.get(sessionId);
        const item = sessionDetailToListItem(detail);
        setSessions([item]);
        const previousId = selectedSessionIdRef.current;
        setSelectedSessionId(detail.id);
        // Same session, no events refetch — SSE already accumulated
        // them locally and ``list_events_after`` caps at 500 rows ASC,
        // so a refetch on a long session silently drops the most
        // recent turn (which is exactly what we just streamed).
        if (detail.id !== previousId) {
          await refreshEvents(detail.id);
        }
      } catch {
        // Session not found / 4xx — clear selection so the UI doesn't
        // pretend we're still on a deleted row.
        setSessions([]);
        setSelectedSessionId(null);
      }
    },
    [refreshEvents],
  );

  const bootstrap = useCallback(
    async (isCurrent: () => boolean) => {
      const routeState = location.state as {
        promotedFromNew?: boolean;
        promotedSessionId?: string;
      } | null;
      // The promotion fast-path (skip the loading flash + skip the history
      // refetch) is only valid while a send is genuinely IN FLIGHT — that live
      // subscription is what fills ``events`` for the freshly promoted session,
      // so the refetch is redundant and the loader would just flash. On a cold
      // page reload there is no in-flight send: ``history.state`` still carries
      // ``promotedFromNew`` (the hash router restores it across refreshes) and
      // the ``promotingSessionIdRef`` / ``consumedPromoteSessionIdsRef`` refs
      // reset with the page, so without this ``isSendInFlightRef`` guard bootstrap
      // would replay the promotion skip on every refresh — never calling
      // ``refreshEvents`` — and leave the conversation body blank.
      const isPromoteBootstrap =
        isSendInFlightRef.current &&
        id !== NEW_SESSION_ID &&
        (promotingSessionIdRef.current === id ||
          (routeState?.promotedFromNew === true &&
            routeState.promotedSessionId === id &&
            !consumedPromoteSessionIdsRef.current.has(id)));
      if (!isPromoteBootstrap) {
        setLoading(true);
      }
      setError(null);
      // A fresh bootstrap invalidates the previous page's settle: the handoff
      // must not fire against state this run is about to tear down.
      setDraftBootstrapSettled(false);
      try {
        const wsResponse = await projectsApi.list();
        if (!isCurrent()) return;
        setProjects(wsResponse.projects);

        // Two URL shapes drive the page:
        //
        //   /conversation/new           — fresh draft, no session yet
        //   /conversation/{session_id}  — existing session, fetch detail
        //
        // For the fresh-draft case we set ``selectedProjectId`` to the
        // ``"chat-default"`` sentinel so skill autocomplete still loads
        // (the backend skills router treats it as the global chat-scope
        // key); ``refreshFileTree`` skips the sentinel (no project row /
        // workdir exists yet) and renders an empty tree. The real project
        // materializes when the user sends the first message and
        // ``ensureSession`` navigates us to ``/conversation/{real-id}``.
        if (id === NEW_SESSION_ID) {
          setSessionTriggerMode(null);
          setSessionAgentSlug(null);
          // 09-assistant D4: ``/conversation/new?project=<id>`` (e.g. a
          // project-page "+ 新对话" entry) pre-selects that project so the 📁
          // chip lands on it instead of 临时对话. The user can flip back to
          // 临时 with one click. Falls back to the ``"chat-default"`` sentinel
          // (临时) when the query is absent or doesn't match a project.
          const projectParam = searchParams.get("project");
          const presetProject =
            projectParam &&
            wsResponse.projects.some(
              (w) => w.id === projectParam && w.kind === "project",
            )
              ? projectParam
              : null;
          setSelectedProjectId(presetProject ?? "chat-default");
          // Preset project (e.g. home-page footer bar hand-off): same reveal
          // rule as an in-page pick.
          if (presetProject) panelSetCollapsed(false);
          setSessions([]);
          selectedSessionIdRef.current = null;
          setSelectedSessionId(null);
          // CRITICAL: also clear the conversation state (events / todos
          // / optimistic pending message) so navigating from a real
          // session ``/conversation/{X}`` → ``/conversation/new`` shows
          // an empty composer instead of leaving session X's turns on
          // screen. ``refreshEvents(null)`` is the canonical "switch
          // away from any session" path — it nulls every per-session
          // ref + state synchronously.
          await refreshEvents(null);
          // Everything this branch tears down is now rebuilt, so an optimistic
          // turn created from here on will survive. This is what releases the
          // project-detail send handoff.
          setDraftBootstrapSettled(true);
          return;
        }

        // Existing session path — one round-trip via the dedicated
        // ``GET /v1/sessions/{id}`` endpoint feeds every piece of state
        // the page needs: trigger_meta (skill-creator banner restore),
        // project_id (right panel cwd / file tree), the session row
        // itself (composer status + optimistic-merge target).
        try {
          const sessionDetail = await sessionsApi.get(id);
          if (!isCurrent()) return;
          const routePromotedSession =
            routeState?.promotedFromNew === true &&
            routeState.promotedSessionId === sessionDetail.id &&
            !consumedPromoteSessionIdsRef.current.has(sessionDetail.id);
          // Same guard as ``isPromoteBootstrap`` above: only treat this as a
          // promotion — and therefore SKIP ``refreshEvents`` — while a send is
          // in flight (the live subscription is already streaming this turn's
          // events in). On a reload the restored ``promotedFromNew`` state must
          // NOT suppress the history load, or the transcript stays empty.
          const isPromotedNewSession =
            isSendInFlightRef.current &&
            (promotingSessionIdRef.current === sessionDetail.id ||
              routePromotedSession);
          setSessionTriggerMode(sessionDetail.trigger_meta?.mode ?? null);
          setSessionAgentSlug(sessionDetail.agent_slug ?? null);
          setSelectedProjectId(sessionDetail.project_id);
          // Origin inheritance: a session's project lives on the same backend
          // as the session (managed remote projects are list-hidden, so this
          // is often the ONLY chance to learn their origin). Also self-heals
          // entries lost before project recording existed at creation time.
          {
            const sessionOrigin = getEntityOrigin(sessionDetail.id, "session");
            if (
              sessionOrigin &&
              sessionDetail.project_id &&
              !getEntityOrigin(sessionDetail.project_id)
            ) {
              recordEntityOrigin(sessionDetail.project_id, sessionOrigin);
            }
          }
          setSessions([sessionDetailToListItem(sessionDetail)]);
          selectedSessionIdRef.current = sessionDetail.id;
          setSelectedSessionId(sessionDetail.id);
          if (isPromotedNewSession) {
            promotingSessionIdRef.current = null;
            consumedPromoteSessionIdsRef.current.add(sessionDetail.id);
          }
          // Selection alone is not proof that history loaded. Skip only when
          // this exact session already hydrated successfully, or while the
          // promoted session's live stream is supplying the first turn.
          if (
            shouldRefreshConversationHistory({
              hydratedSessionId: historyHydratedSessionIdRef.current,
              sessionId: sessionDetail.id,
              promotedWithLiveStream: isPromotedNewSession,
            })
          ) {
            await refreshEvents(sessionDetail.id);
          }
        } catch {
          if (!isCurrent()) return;
          // 404 / network — render the page in "no session" state and
          // surface an error banner. Don't fall back to the sentinel
          // because that would silently move the user off the URL they
          // typed / clicked.
          setSessionTriggerMode(null);
          setSessionAgentSlug(null);
          setSelectedProjectId(null);
          setSessions([]);
          selectedSessionIdRef.current = null;
          setSelectedSessionId(null);
          setError("Session not found.");
        }
      } catch (cause) {
        if (!isCurrent()) return;
        setError(
          cause instanceof Error ? cause.message : "Failed to load project.",
        );
      } finally {
        if (isCurrent() && !isPromoteBootstrap) {
          setLoading(false);
        }
      }
    },
    [id, location.state, refreshEvents, searchParams],
  );

  return { refreshEvents, loadOlderTurns, refreshActiveSession, bootstrap };
}
