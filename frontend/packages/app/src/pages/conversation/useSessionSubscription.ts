import { useCallback } from "react";
import type { Dispatch, SetStateAction } from "react";
import {
  mergeEventWindow,
  parseActionResolved,
  parseRequiresAction,
  parseTodosUpdate,
  parseWorkflowProgress,
  sessionsApi,
  SESSION_ACTION_RESOLVED_EVENT,
  SESSION_REQUIRES_ACTION_EVENT,
  SESSION_WORKFLOW_PROGRESS_EVENT,
  type SessionEventDTO,
  type SessionListItem,
  type TodoItem,
  type WorkflowState,
} from "@valuz/core";
import type { ApprovalCardSubject, ApprovalResolvedDecision } from "@valuz/ui";
import { shouldApplySessionStatus } from "../conversation-loading";
import { isWorkflowRunning } from "./tool-card-helpers";
import {
  TURN_PAGE_SIZE,
  type PendingApprovalEntry,
} from "./useConversationHistory";

// Structural copy of ``ConversationPage``'s component-local
// ``AutoApprovedNotice`` interface (declared inside the component body, so
// it cannot be imported here). TypeScript's structural typing keeps the
// page's ``setAutoApprovedNotices`` assignable to the param typed with this
// copy. Keep the two shapes in sync.
export interface AutoApprovedNotice {
  pendingId: string;
  receivedAtLabel?: string;
  rulePreviewDisplay: string | null;
  // Captured from the matching ``requires_action`` row in
  // ``pendingApprovals`` at resolve time. ``null`` when the
  // requires_action couldn't be matched (rare — replay edge case).
  subject: ApprovalCardSubject | null;
  payload: Record<string, unknown> | null;
}

type SessionSubscriptionParams = {
  abortRef: { current: AbortController | null };
  selectedSessionIdRef: { current: string | null };
  seenEventUidsRef: { current: Set<string> };
  historyCursorRef: { current: number };
  streamReconnectAttemptsRef: { current: number };
  /** Declared in the page; also consumed by ``useConversationHistory``. */
  historyHydrationRef: { current: Promise<void> };
  handoffSessionIdRef: { current: string | null };
  currentClarifyingPendingRef: { current: string | null };
  ruleIdToPreviewRef: { current: Map<string, string> };
  /** The page's ``refreshQueueRef`` is declared after the hook call site
   *  (block-scoped — a direct reference would TDZ), so the page threads a
   *  deferring getter object with the same ``.current`` read semantics. */
  refreshQueueRef: { current: () => Promise<void> };
  pendingApprovals: PendingApprovalEntry[];
  setEvents: Dispatch<SetStateAction<SessionEventDTO[]>>;
  setPendingUserMessage: (value: null) => void;
  setTodos: Dispatch<SetStateAction<TodoItem[] | null>>;
  setWorkflowStates: Dispatch<SetStateAction<Map<string, WorkflowState>>>;
  setPendingApprovals: Dispatch<SetStateAction<PendingApprovalEntry[]>>;
  setAutoApprovedNotices: Dispatch<SetStateAction<AutoApprovedNotice[]>>;
  setSending: Dispatch<SetStateAction<boolean>>;
  setSessions: Dispatch<SetStateAction<SessionListItem[]>>;
};

/**
 * ── Session-lifetime event stream ────────────────────────────────────
 *
 * Owns ``subscribeToSession`` — the single SSE data-plane subscription per
 * opened session: live frame handling (``appendEvent``), the bounded open-
 * reconcile burst, and the gap-fill + reconnect controller. Body is moved
 * verbatim from ``ConversationPage``.
 */
export function useSessionSubscription({
  abortRef,
  selectedSessionIdRef,
  seenEventUidsRef,
  historyCursorRef,
  streamReconnectAttemptsRef,
  historyHydrationRef,
  handoffSessionIdRef,
  currentClarifyingPendingRef,
  ruleIdToPreviewRef,
  refreshQueueRef,
  pendingApprovals,
  setEvents,
  setPendingUserMessage,
  setTodos,
  setWorkflowStates,
  setPendingApprovals,
  setAutoApprovedNotices,
  setSending,
  setSessions,
}: SessionSubscriptionParams) {
  // SESSION-LIFETIME data-plane stream (docs/design/session-stream-lifetime.md).
  // One call per opened session: the stream stays up across every turn — the
  // server generator never closes at turn end and the kernel bus survives
  // turns, so drained queue items, scheduled turns, bg-task wake-ups and
  // other-client turns all arrive on this one connection with no per-turn
  // re-subscription. Terminal frames are ordinary events (they update status
  // and bookkeeping; they do NOT tear the stream down). The stream ends only
  // when superseded by the next session's open, or on unmount.
  const subscribeToSession = useCallback(
    (
      sessionId: string,
      // HISTORY-space cursor (durable-store seq — pass ``historyCursorRef``):
      // the server backfills persisted events strictly after it. Live frames
      // on the stream carry kernel-LOCAL seqs and must never be compared to
      // (or folded into) this value.
      afterSeq: number,
    ) => {
      if (abortRef.current) {
        abortRef.current.abort();
      }
      const abort = new AbortController();
      // Bounded open-reconcile burst (see below the appendEvent def), cancelled
      // once the stream settles or the subscription is superseded.
      const reconcileBurstTimers: number[] = [];
      abortRef.current = abort;

      const appendEvent = (event: SessionEventDTO) => {
        // Drop deliveries that outlive this subscription. A session switch
        // aborts the stream (the ``selectedSessionId`` effect) — but an
        // in-flight poll response, a gap-fill, or a frame parsed in the same
        // tick as the abort still resolves afterwards. Without this guard
        // the OLD session's events land in the NEW session's transcript and
        // pass the dedupe below (event identities are session-scoped only by
        // this check). The ref check also covers the abort effect's commit
        // lag: ``selectedSessionIdRef`` flips synchronously in bootstrap,
        // before the abort lands.
        if (abort.signal.aborted) return;
        if (selectedSessionIdRef.current !== sessionId) return;
        // Replay detection for the terminal gates below. Reconnect gap-fills
        // and the server's initial drain can legitimately redeliver frames
        // this transcript already consumed; replays must stay render-inert
        // AND gate-inert (a replayed terminal frame must not re-run turn-end
        // handling). Uid-less legacy frames keep the pre-uid behavior. The
        // set is bounded: a session-lifetime page accumulates uids for its
        // whole stay, so shed the oldest half at the cap.
        const frameUid = event.event_uid ?? null;
        const isReplayOfSeen =
          frameUid != null && seenEventUidsRef.current.has(frameUid);
        if (frameUid != null) {
          const seen = seenEventUidsRef.current;
          if (seen.size >= 8192) {
            let shed = 0;
            for (const uid of seen) {
              seen.delete(uid);
              if (++shed >= 4096) break;
            }
          }
          seen.add(frameUid);
        }
        // Deliberately NO history-cursor advancement here: SSE frames mix
        // server-side backfill (history seq) with live frames (kernel-LOCAL
        // seq) and the two are indistinguishable on the wire — feeding a
        // live seq into ``historyCursorRef`` would corrupt every later
        // ``after_seq`` read. The cursor advances only from REST history
        // responses (refreshEvents / reconcile / poll paths); the price is
        // that a reconnect may replay a little more history, which the
        // uid dedup below collapses.
        setEvents((prev) => {
          // uid-first dedup across segments; a uid-less persisted frame
          // falls back to the legacy seq check, but only against other
          // uid-less rows (a uid row's seq may be from the other space).
          const duplicate = event.event_uid
            ? prev.some((existing) => existing.event_uid === event.event_uid)
            : event.seq > 0 &&
              prev.some(
                (existing) => !existing.event_uid && existing.seq === event.seq,
              );
          if (duplicate) return prev;
          return [...prev, event];
        });
        // Once the kernel echoes the user's message back as the
        // first ``message.user`` event of the turn, the optimistic
        // pending card has served its purpose — drop it so we don't
        // double-render the same text.
        if (event.event.event_type === "message.user") {
          setPendingUserMessage(null);
          // The handed-over pending has served its purpose; drop the guard so
          // a later switch back to this session clears normally.
          handoffSessionIdRef.current = null;
        }
        // Live TODO panel update — kernel V5+messages emits
        // ``session.todos.update`` whenever the agent calls
        // TodoWrite. Replace the snapshot so completed/in-progress
        // states flow into the panel in real time.
        if (event.event.event_type === "session.todos.update") {
          const refreshed = parseTodosUpdate(event);
          console.debug(
            "[Conversation] session.todos.update parsed:",
            refreshed?.length ?? 0,
            "items",
            refreshed,
          );
          if (refreshed) setTodos(refreshed);
        }
        // Live workflow progress — Claude ``Workflow`` tool runs stream a
        // snapshot per tick (phases / per-agent state / status), keyed by the
        // launch tool_use_id. Merge into the per-tool map so the Workflow tool
        // card renders live progress. ``script`` / ``scriptPath`` arrive only on
        // the first snapshot, so carry them forward when a later tick omits them.
        if (event.event.event_type === SESSION_WORKFLOW_PROGRESS_EVENT) {
          const wp = parseWorkflowProgress(event);
          if (wp) {
            setWorkflowStates((prev) => {
              const prevState = prev.get(wp.id);
              const next = new Map(prev);
              next.set(wp.id, {
                ...wp.state,
                scriptPath: wp.state.scriptPath ?? prevState?.scriptPath,
                script: wp.state.script ?? prevState?.script,
              });
              return next;
            });
          }
        }
        // ADR-013 approval contract: track the current unresolved
        // clarifying_questions pending so the AskUserQuestionCard's
        // submit can POST /actions with the right pending_id. Non-
        // clarifying subjects (shell_command / file_change /
        // mcp_tool_call / tool_input) feed into the approval tray
        // rendered above the Composer.
        // ADR-013 approval contract v1 (kernel 1aae940) + v2 (kernel
        // d008b53): track parked pendings so the user can resolve
        // them, learn rule_id → preview mappings so subsequent
        // cache-hit ``auto_approved`` events show what rule fired,
        // and surface synthetic seals (``expired`` / ``interrupted``).
        if (event.event.event_type === SESSION_REQUIRES_ACTION_EVENT) {
          const ra = parseRequiresAction(event);
          if (ra) {
            if (ra.subject === "clarifying_questions") {
              currentClarifyingPendingRef.current = ra.pending_id;
            } else {
              const subject = ra.subject as ApprovalCardSubject;
              setPendingApprovals((prev) =>
                prev.some((p) => p.pendingId === ra.pending_id)
                  ? prev
                  : [
                      ...prev,
                      {
                        pendingId: ra.pending_id,
                        subject,
                        payload: ra.payload,
                        availableDecisions: ra.available_decisions,
                        sessionRulePreviewDisplay:
                          ra.session_rule_preview?.display ?? null,
                        originalInput: ra.original_input,
                        receivedAt: event.timestamp,
                      },
                    ],
              );
            }
          }
        }
        if (event.event.event_type === SESSION_ACTION_RESOLVED_EVENT) {
          const ar = parseActionResolved(event);
          if (ar) {
            if (currentClarifyingPendingRef.current === ar.pending_id) {
              currentClarifyingPendingRef.current = null;
            }
            // v2: kernel-synthesized cache-hit. The kernel ALWAYS emits
            // a paired ``requires_action`` immediately before the
            // synthetic ``action_resolved(auto_approved)`` (see
            // ``claude_agent/runtime.py``: ``event_sink.emit(...)`` then
            // ``cache_hit is not None`` branch), so the requires_action
            // handler above has already pushed a pending card into
            // ``pendingApprovals``. Harvest its subject + payload into
            // the notice so the strip can render a one-line summary
            // ("what was approved"), then remove the pending card —
            // otherwise it stays clickable and the user hits "批准",
            // which the kernel rejects with 409 "already resolved as
            // auto_approved". Finally schedule a 5s auto-remove on the
            // notice so the tray doesn't accumulate forever (symmetric
            // with ApprovalResolvedStrip's 2s, longer here because the
            // user didn't initiate the action).
            if (ar.decision === "auto_approved") {
              // Pull the harvested subject/payload BEFORE the pending
              // filter — once filtered out, the row is gone from
              // state and we can't read it.
              const sourceEntry = pendingApprovals.find(
                (p) => p.pendingId === ar.pending_id,
              );
              setPendingApprovals((prev) =>
                prev.filter((p) => p.pendingId !== ar.pending_id),
              );
              const ruleId = ar.auto_resolved_by_rule_id;
              const preview = ruleId
                ? (ruleIdToPreviewRef.current.get(ruleId) ?? null)
                : null;
              setAutoApprovedNotices((prev) =>
                prev.some((n) => n.pendingId === ar.pending_id)
                  ? prev
                  : [
                      ...prev,
                      {
                        pendingId: ar.pending_id,
                        receivedAtLabel: event.timestamp
                          ? new Date(event.timestamp).toLocaleTimeString()
                          : undefined,
                        rulePreviewDisplay: preview,
                        subject: sourceEntry?.subject ?? null,
                        payload: sourceEntry?.payload ?? null,
                      },
                    ],
              );
              // Auto-clear the notice after 5s so the tray stays
              // bounded. Idempotent: if the notice was already
              // removed (e.g. session switch wiped the state), the
              // filter is a no-op.
              window.setTimeout(() => {
                setAutoApprovedNotices((prev) =>
                  prev.filter((n) => n.pendingId !== ar.pending_id),
                );
              }, 5000);
              return;
            }
            // v2: ``approve_for_session`` commits a kernel-owned
            // rule. Snapshot rule_id → preview.display so any
            // subsequent ``auto_approved`` for the same rule can be
            // labelled even though the kernel doesn't re-emit the
            // preview.
            if (ar.decision === "approve_for_session" && ar.rule_id) {
              const match = pendingApprovals.find(
                (p) => p.pendingId === ar.pending_id,
              );
              if (match?.sessionRulePreviewDisplay) {
                ruleIdToPreviewRef.current.set(
                  ar.rule_id,
                  match.sessionRulePreviewDisplay,
                );
              }
            }
            // v1 + v2: AskUserQuestion answered, normal approve /
            // reject, plus the new ``approve_with_changes`` /
            // ``approve_for_session`` verbs and the synthetic seals
            // (``expired`` / ``interrupted``). Flip the matching
            // tray card into ``answered`` so the user sees the
            // outcome before it fades off the tray on the next
            // render tick. ``auto_approved`` is handled by the early
            // ``return`` above; this branch only sees the 7 user /
            // synthetic verbs that the strip renders.
            const resolvedDecision = ar.decision as ApprovalResolvedDecision;
            setPendingApprovals((prev) => {
              const match = prev.find((p) => p.pendingId === ar.pending_id);
              if (!match) return prev;
              return prev.map((p) =>
                p.pendingId === ar.pending_id
                  ? {
                      ...p,
                      answered: true,
                      submitting: false,
                      decision: resolvedDecision,
                      rejectMessage:
                        resolvedDecision === "reject" ? ar.message : null,
                    }
                  : p,
              );
            });
            // Drop the answered card after a short delay so the user
            // sees the confirmation badge before it disappears.
            window.setTimeout(() => {
              setPendingApprovals((prev) =>
                prev.filter((p) => p.pendingId !== ar.pending_id),
              );
            }, 2000);
          }
        }
        // Turn lifecycle on a SESSION-LIFETIME stream: terminal frames are
        // ordinary events. They update status/bookkeeping below but never
        // close the stream — the next turn (queue drain, schedule, another
        // client, a follow-up send) arrives on this same connection.
        const evType = event.event.event_type;
        // A non-replayed ``message.user`` means a turn genuinely started —
        // release the optimistic send-pending flag (its only job is bridging
        // the click → turn-start window; ``status === "running"`` carries the
        // busy state from here). Replays (reconnect backfill) stay inert.
        if (evType === "message.user" && !isReplayOfSeen) {
          setSending(false);
          // A turn starting is the authoritative "a queued head may have just
          // been dispatched" signal — the drain marks the row ``dispatched``
          // (a host-local write, no mirror lag) BEFORE emitting this event, so
          // refetching now always reads post-dispatch state and drops the
          // consumed item from the queue bar. This is timing-independent,
          // unlike the ``isBusy`` edge refetch below (which misses when the
          // idle→running transition is coalesced into one render and ``isBusy``
          // never dips — the "dispatched item keeps showing as queued during
          // its own turn" bug). Harmless for a direct (non-drain) send: the
          // refetch returns the same list. The ticket guard dedups overlap.
          void refreshQueueRef.current();
        }
        const status = event.event.payload.status;
        // Reconcile the authoritative status from the live frame so the derived
        // loading flag + header pill track the turn without waiting for a poll.
        // Replays are FULLY inert — non-terminal and terminal alike; the
        // rationale (an asymmetric gate deadlocked the pill at 运行中 on
        // cloud reconnect backfills) lives on ``shouldApplySessionStatus``.
        // The send path's stale-pre-turn-``idle`` hazard (image-upload slow
        // start) is handled by the busy derivation instead: ``sendPending``
        // overrides a terminal status until the turn's start event or a send
        // error (see ``deriveTurnActive``).
        if (evType === "session.update" && shouldApplySessionStatus(status, isReplayOfSeen)) {
          setSessions((prev) =>
            prev.map((s) =>
              s.id === sessionId
                ? { ...s, status: status as SessionListItem["status"] }
                : s,
            ),
          );
        }
        // ``!isReplayOfSeen``: a redelivered terminal frame must not re-run
        // turn-end handling for a turn that is long over.
        const terminal =
          !isReplayOfSeen &&
          (evType === "session.idle" ||
            evType === "run.failed" ||
            (evType === "session.update" &&
              status !== undefined &&
              status !== "" &&
              status !== "running" &&
              status !== "created"));
        if (terminal) {
          // The turn is over — release any lingering optimistic pending flag
          // (a send whose turn just finished, or a stale one after an error).
          setSending(false);
          // Safety net for workflow cards: the turn is over, so any Workflow
          // run is definitively finished (the kernel blocks the turn until the
          // run completes, then force-emits a terminal snapshot). The snapshot
          // is live-only; if it lost the race against this terminal event, a
          // still-"running" card would pulse forever. Coerce it to
          // ``completed``; cards that already received a terminal status are
          // left untouched. (With the stream no longer closing at terminal, a
          // late snapshot now also arrives on its own — this is pure backstop.)
          setWorkflowStates((prev) => {
            let changed = false;
            const next = new Map(prev);
            for (const [wfId, wfState] of prev) {
              if (isWorkflowRunning(wfState.status)) {
                next.set(wfId, { ...wfState, status: "completed" });
                changed = true;
              }
            }
            return changed ? next : prev;
          });
        }
      };

      // No forever-poll here — ongoing delivery is stream-driven: live frames
      // via the SSE reader, plus the server-side backfill inside
      // ``iter_events_sse`` re-emitting any missed persisted events on THIS
      // stream (~2s); a full stream drop is handled by the unconditional
      // reconnect below (gap-fill + backoff).
      //
      // On OPEN (this now runs once per session stay, not per turn) the
      // initial transcript window (``refreshEvents``) and this subscription
      // race — under some interleavings the loaded window lands empty /
      // superseded, leaving the transcript BLANK until a later event forces a
      // re-render (the "reload mid-turn shows nothing until you jostle it"
      // bug; the content IS persisted, just not painted). Close that race
      // deterministically with a BOUNDED, self-terminating reconcile burst:
      // re-fetch the transcript window a few times over the first ~2.5s and
      // idempotently merge it, forcing the persisted transcript to paint
      // regardless of which async path won.
      const reconcileTranscript = () => {
        if (abort.signal.aborted) return;
        if (selectedSessionIdRef.current !== sessionId) return;
        void sessionsApi
          .listEventsWindow(sessionId, { turnLimit: TURN_PAGE_SIZE })
          .then((resp) => {
            if (abort.signal.aborted) return;
            if (selectedSessionIdRef.current !== sessionId) return;
            if (resp.items.length === 0) return;
            // Order-safe merge, NOT ``appendEvent`` (tail-append would render
            // history after the current turn) and NOT a global seq sort (live
            // deltas are ``seq 0`` — a sort throws them to the FRONT of the
            // transcript, which rendered refreshed-mid-turn content into the
            // previous turn's area). ``mergeEventWindow`` inserts only the
            // genuinely-missing persisted rows at their seq position and keeps
            // live entries glued where they arrived.
            setEvents((prev) => mergeEventWindow(prev, resp.items));
            const top = resp.items[resp.items.length - 1].seq;
            if (top > historyCursorRef.current) historyCursorRef.current = top;
          })
          .catch(() => {});
      };
      for (const ms of [400, 1200, 2500]) {
        reconcileBurstTimers.push(window.setTimeout(reconcileTranscript, ms));
      }

      // Connection controller: the server generator never closes on its own,
      // so ANY stream end that we didn't abort ourselves is abnormal (proxy
      // cut, server restart, dropped socket). Recover unconditionally:
      // gap-fill what the stream missed from the DB, then reconnect with
      // exponential backoff — for as long as this subscription owns the page.
      // There is deliberately NO give-up cap: capping at N attempts turned a
      // flaky stream into a silent fake completion (verified incident: a long
      // quiet tool call kept the stream frame-free, five closes exhausted the
      // cap mid-turn, and the page showed copy/retry while the kernel was
      // still working). The delay is capped at 15s, and each cycle's gap-fill
      // keeps delivering content, so the steady state degrades to cheap
      // polling — never to a lie. No turn-liveness decision is needed anymore:
      // an idle session's open stream just sits on server heartbeats.
      const gapFillAndReconnect = async () => {
        if (abort.signal.aborted) return;
        try {
          const resp = await sessionsApi.listEvents(
            sessionId,
            historyCursorRef.current,
          );
          if (abort.signal.aborted) return;
          // ``listEvents`` results are HISTORY-space — advance the history
          // cursor here (``appendEvent`` deliberately never does: it also
          // handles live frames whose kernel-local seq must not leak in).
          for (const event of resp.items) {
            if (event.seq > 0) {
              historyCursorRef.current = Math.max(
                historyCursorRef.current,
                event.seq,
              );
            }
          }
          for (const event of resp.items) {
            // May deliver a missed terminal event → status + bookkeeping.
            appendEvent(event);
          }
          // Content flowed — the server is alive and producing; keep the
          // backoff snappy. (The live-frame reset can't cover this: a
          // stream that dies between frames never delivers one.)
          if (resp.items.length > 0) {
            streamReconnectAttemptsRef.current = 0;
          }
        } catch {
          // Gap-fill is best-effort — the reconnect below retries anyway.
        }
        if (abort.signal.aborted) return;
        const attempt = streamReconnectAttemptsRef.current;
        streamReconnectAttemptsRef.current = attempt + 1;
        const delay = Math.min(1000 * 2 ** Math.min(attempt, 4), 15000);
        // Diagnosability: unexpected closes are invisible otherwise — the
        // last verified incident could only be reconstructed from the DB.
        console.warn(
          `[Conversation] events stream closed unexpectedly (attempt ${attempt + 1}) — reconnecting in ${delay}ms`,
        );
        window.setTimeout(() => {
          if (abort.signal.aborted) return;
          if (selectedSessionIdRef.current !== sessionId) return;
          connect();
        }, delay);
      };
      // Never let the recovery chain escape as an unhandled rejection —
      // every recoverable branch inside is already try/caught.
      const safeRecover = () => gapFillAndReconnect().catch(() => {});

      const connect = () => {
        if (abort.signal.aborted) return;
        // Re-sample the cursor on every (re)connect so the server's initial
        // drain replays as little as possible; the uid dedup absorbs overlap.
        const startSeq = Math.max(afterSeq, historyCursorRef.current);
        sessionsApi
          .subscribeEvents(
            sessionId,
            (event) => {
              // A delivered frame means the stream is healthy — reset the
              // unexpected-close backoff.
              streamReconnectAttemptsRef.current = 0;
              appendEvent(event);
            },
            startSeq,
            abort.signal,
          )
          .then(safeRecover)
          .catch(safeRecover);
      };

      // Gate the FIRST open on the history-window hydration: ``refreshEvents``
      // resets the cursor synchronously and only hydrates it when its fetch
      // lands, so an ungated open could start at ``afterSeq = 0`` and replay
      // the whole session over SSE. Time-capped so a hung history fetch
      // degrades to an ungated open instead of never opening the stream.
      void Promise.race([
        historyHydrationRef.current,
        new Promise<void>((resolve) => window.setTimeout(resolve, 3000)),
      ]).then(() => {
        if (abort.signal.aborted) {
          reconcileBurstTimers.forEach((t) => window.clearTimeout(t));
          return;
        }
        connect();
      });
    },
    [],
  );

  return { subscribeToSession };
}
