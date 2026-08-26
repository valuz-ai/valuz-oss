import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { queueApi, type QueuedInput, type QueuedInputList } from "@valuz/core";

type InputQueueParams = {
  selectedSessionId: string | null;
  selectedProviderId: string | null;
  selectedModelId: string | null;
  draft: string;
  /** Derived turn-activity flag (``deriveTurnActive``), computed in the page. */
  isBusy: boolean;
  setDraft: (value: string) => void;
  setSelectedComposerSkill: (value: null) => void;
  refreshActiveSession: (sessionId: string | null) => Promise<void>;
  fetchSidebarSessions: () => Promise<void>;
};

/**
 * ── Session input queue ──────────────────────────────────────────────
 *
 * Owns the queued-inputs cluster of the conversation page: the queue
 * state itself, the single-writer ticket pipeline (``applyQueueList`` /
 * ``refreshQueue`` / ``refreshQueueRef``), the enqueue / edit / delete /
 * resume / steer handlers, and the queue-lifecycle effects (hydration on
 * session change, turn-boundary refetches, drain-quiet sidebar refresh,
 * and the 5s backstop poll). Bodies are moved verbatim from
 * ``ConversationPage``.
 */
export function useInputQueue({
  selectedSessionId,
  selectedProviderId,
  selectedModelId,
  draft,
  isBusy,
  setDraft,
  setSelectedComposerSkill,
  refreshActiveSession,
  fetchSidebarSessions,
}: InputQueueParams) {
  // Session input queue (docs/design/session-input-queue.md): follow-up inputs
  // submitted while a turn is running, drained FIFO after it. ``queuePaused``
  // is set after an interrupt — the user resumes explicitly.
  const [queue, setQueue] = useState<QueuedInput[]>([]);
  const [queuePaused, setQueuePaused] = useState(false);
  // True while a host drain chain is in flight. A dispatched (in-flight) item
  // is invisible in ``queue`` (only queued/blocked list), so the drain-follower
  // keys on this to keep re-subscribing until the LAST drained turn finishes —
  // not just while ``queue`` is non-empty (session-input-queue §14.5).
  const [queueDraining, setQueueDraining] = useState(false);
  // The item the drain is executing RIGHT NOW (status ``dispatched``): already
  // out of ``queue`` but its turn may not have landed a durable user message
  // yet. Rendered as a non-editable bubble while nothing is streaming so the
  // accepted message is never invisible in BOTH the queue bar and the
  // transcript (session-input-queue §14.5 补强③).
  const [queueDispatching, setQueueDispatching] = useState<QueuedInput | null>(
    null,
  );
  // Monotonic ticket for queue-list responses. Every fetch/mutation takes a
  // ticket before its await; only the NEWEST ticket may write the four queue
  // states. Without this, a slow ``GET /queue`` issued before an enqueue could
  // resolve after the enqueue's response and clobber the fresh list — making
  // the just-accepted item vanish until the next turn boundary.
  const queueListTicketRef = useRef(0);
  // "The drain chain continues after this turn" — mirrored into a ref so the
  // turn-end bookkeeping effect reads the freshest value without re-arming.
  // Gates the per-boundary sidebar refetch: refetching between every drained
  // item is visible churn.
  const queueContinuesRef = useRef(false);

  // Single writer for the queue states. Every fetch/mutation takes a ticket
  // BEFORE its await and applies through here — a response whose ticket is no
  // longer the newest is stale (issued earlier, resolved later) and dropped,
  // so an in-flight refetch can never clobber a mutation's fresher list.
  const applyQueueList = useCallback(
    (list: QueuedInputList, ticket: number) => {
      if (ticket !== queueListTicketRef.current) return;
      setQueue(list.items);
      setQueuePaused(list.paused);
      setQueueDraining(list.draining ?? false);
      setQueueDispatching(list.dispatching ?? null);
    },
    [],
  );

  const refreshQueue = useCallback(async () => {
    if (!selectedSessionId) return;
    const ticket = ++queueListTicketRef.current;
    try {
      const list = await queueApi.list(selectedSessionId);
      applyQueueList(list, ticket);
    } catch {
      /* best-effort — a queue fetch failure must not break the conversation */
    }
  }, [selectedSessionId, applyQueueList]);

  // Kept current so the SSE ``appendEvent`` closure (created once inside the
  // deps-``[]`` ``subscribeToSession`` callback) always calls the latest
  // ``refreshQueue`` without re-subscribing.
  const refreshQueueRef = useRef(refreshQueue);
  useEffect(() => {
    refreshQueueRef.current = refreshQueue;
  }, [refreshQueue]);

  const performEnqueue = async () => {
    const text = draft.trim();
    if (!text || !selectedSessionId) return;
    setDraft("");
    setSelectedComposerSkill(null);
    const ticket = ++queueListTicketRef.current;
    try {
      const list = await queueApi.enqueue(selectedSessionId, text, {
        providerId: selectedProviderId,
        modelId: selectedModelId,
      });
      applyQueueList(list, ticket);
    } catch (cause) {
      toast.error(
        cause instanceof Error ? cause.message : "Failed to queue message.",
      );
      setDraft(text); // restore so the user can retry
    }
  };

  const handleEditQueued = async (queueId: string, text: string) => {
    if (!selectedSessionId) return;
    const ticket = ++queueListTicketRef.current;
    try {
      const list = await queueApi.edit(selectedSessionId, queueId, text);
      applyQueueList(list, ticket);
    } catch (cause) {
      toast.error(cause instanceof Error ? cause.message : "Edit failed.");
    }
  };

  const handleDeleteQueued = async (queueId: string) => {
    if (!selectedSessionId) return;
    const ticket = ++queueListTicketRef.current;
    try {
      const list = await queueApi.remove(selectedSessionId, queueId);
      applyQueueList(list, ticket);
    } catch (cause) {
      toast.error(cause instanceof Error ? cause.message : "Delete failed.");
    }
  };

  const handleResumeQueue = async () => {
    if (!selectedSessionId) return;
    const ticket = ++queueListTicketRef.current;
    try {
      const list = await queueApi.resume(selectedSessionId);
      applyQueueList(list, ticket);
      // The resumed drain's turns stream in on the session-lifetime stream.
    } catch (cause) {
      toast.error(cause instanceof Error ? cause.message : "Resume failed.");
    }
  };

  const handleSteerQueued = async (queueId: string) => {
    if (!selectedSessionId) return;
    const ticket = ++queueListTicketRef.current;
    try {
      // Send-now: the backend silently interrupts the active turn and drains
      // this item. The steered turn streams in on the session-lifetime stream.
      const list = await queueApi.steer(selectedSessionId, queueId);
      applyQueueList(list, ticket);
    } catch (cause) {
      toast.error(cause instanceof Error ? cause.message : "Send failed.");
    }
  };

  // Hydrate the persisted queue when the active session changes. Clear the
  // previous session's list synchronously first (ticket bump invalidates any
  // still-in-flight response of the old session) so its bubbles never bleed
  // into the new conversation while the fetch is out.
  useEffect(() => {
    queueListTicketRef.current++;
    setQueue([]);
    setQueuePaused(false);
    setQueueDraining(false);
    setQueueDispatching(null);
    void refreshQueue();
  }, [refreshQueue]);

  // NO drain-follower here anymore: drained queue turns arrive on the
  // session-lifetime stream like any other turn (the whole
  // resubscribe/checkNow/control-frame-trigger machinery this effect used to
  // carry existed only because the stream was torn down at every turn end —
  // see docs/design/session-stream-lifetime.md).

  // Refetch on BOTH turn boundaries (session-input-queue §8.4):
  // - busy → idle: drained items drop, blocked / paused state surfaces;
  // - idle → busy: the drain consumed the head to START this turn — without
  //   this edge the already-dispatched item kept rendering as "queued" under
  //   the composer for the WHOLE next turn (its message is simultaneously
  //   streaming in the transcript above), because the busy-gated 5s backstop
  //   is off while a turn runs and the fall-edge refetch raced the dispatch.
  //   The backend marks the row ``dispatched`` BEFORE the turn's first event,
  //   so a refetch triggered by the turn start always reads post-dispatch
  //   state; the ticket guard absorbs any stragglers.
  // Keyed on the derived ``isBusy`` (not raw ``sending``) so the resync still
  // fires when only the status reconciliation ends the turn.
  const prevQueueBusyRef = useRef(false);
  useEffect(() => {
    if (prevQueueBusyRef.current !== isBusy) void refreshQueue();
    prevQueueBusyRef.current = isBusy;
  }, [isBusy, refreshQueue]);

  // Turn-end bookkeeping, formerly smuggled into the per-turn stream teardown
  // (``stopSubscription``): reconcile the authoritative session row, and
  // refresh the sidebar — per QUEUE, not per turn (while more drained items
  // are about to run, reordering the sidebar at every sub-second boundary
  // reads as flicker; the drain-quiet effect below lands the final refresh).
  const prevTurnBusyRef = useRef(false);
  useEffect(() => {
    const sid = selectedSessionId;
    if (prevTurnBusyRef.current && !isBusy && sid) {
      void refreshActiveSession(sid);
      if (!queueContinuesRef.current) void fetchSidebarSessions();
    }
    prevTurnBusyRef.current = isBusy;
  }, [isBusy, selectedSessionId, refreshActiveSession, fetchSidebarSessions]);

  // Keep ``queueContinuesRef`` (read by the turn-end bookkeeping effect) in step with the
  // queue states so the per-boundary sidebar refetch is skipped only while the
  // chain genuinely continues.
  useEffect(() => {
    queueContinuesRef.current =
      !queuePaused &&
      (queueDraining || queue.some((i) => i.status === "queued"));
  }, [queue, queueDraining, queuePaused]);

  // Deferred sidebar refetch: ``stopSubscription`` skips it while the chain
  // continues, so land it once the drain goes quiet (draining true → false
  // with nothing streaming) — one reorder per queue instead of one per item.
  const prevQueueDrainingRef = useRef(false);
  useEffect(() => {
    if (prevQueueDrainingRef.current && !queueDraining && !isBusy) {
      void fetchSidebarSessions();
    }
    prevQueueDrainingRef.current = queueDraining;
  }, [queueDraining, isBusy, fetchSidebarSessions]);

  // Backstop: while the queue claims pending/in-flight work but nothing is
  // streaming locally, re-poll the list every 5s. This converges every stale
  // corner the event-driven paths can miss — a ``draining`` flag gone stale in
  // either direction (which would otherwise pin ``displayBusy``'s loading
  // affordances or hide them), a blocked head surfacing, another client
  // draining the same session. Bounded: idle sessions with an empty quiet
  // queue never poll.
  useEffect(() => {
    if (!selectedSessionId || isBusy) return;
    if (!queueDraining && !queue.some((i) => i.status === "queued")) return;
    const timer = window.setInterval(() => void refreshQueue(), 5000);
    return () => window.clearInterval(timer);
  }, [selectedSessionId, isBusy, queueDraining, queue, refreshQueue]);

  return {
    queue,
    queuePaused,
    queueDraining,
    queueDispatching,
    refreshQueueRef,
    performEnqueue,
    handleEditQueued,
    handleDeleteQueued,
    handleResumeQueue,
    handleSteerQueued,
  };
}
