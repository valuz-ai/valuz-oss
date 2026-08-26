import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  buildTurns,
  useStableTurns,
  sessionsApi,
  type SessionEventDTO,
} from "@valuz/core";
import type { ConversationTurn } from "@valuz/shared";

export interface LeadFollowUpChat {
  turns: ConversationTurn[];
  sending: boolean;
  send: (text: string) => Promise<void>;
  /** Raw lead-session events (list + live SSE) — drives tool-card renderers
   *  such as ``useAskUserQuestionCards`` that need the event stream. */
  events: SessionEventDTO[];
  /** True from Send until the kernel echoes ``message.user`` — the window in
   *  which the runtime is still coming up. The caller maps it to the turn
   *  header's startup label (and to WHERE the runtime lives). */
  awaitingRuntime: boolean;
}

/**
 * Minimal follow-up chat over a completed task's lead session. Loads history
 * once and subscribes to the SSE stream, then renders the conversation starting
 * from the user's FIRST follow-up message (the first ``message.user`` event
 * after ``sinceTs``) so neither the orchestration history nor the lead's closing
 * summary leaks into the user-facing follow-up conversation.
 *
 * Why anchor on the first user message rather than a raw ``timestamp > sinceTs``
 * cutoff: the lead's finish turn emits its wrap-up ``assistant_message`` AFTER
 * the ``finish_task`` tool result — i.e. a beat *after* the ``task_completed``
 * timestamp. A pure timestamp filter keeps that summary and it surfaces at the
 * top of the chat, duplicating the deliverable card. The first post-completion
 * user message is the true start of the follow-up dialogue; everything above it
 * (the leaked summary included) is dropped.
 */
export function useLeadFollowUpChat(params: {
  leadSessionId: string | null;
  sinceTs: number | null;
}): LeadFollowUpChat {
  const { leadSessionId, sinceTs } = params;
  const [events, setEvents] = useState<SessionEventDTO[]>([]);
  const [sending, setSending] = useState(false);
  // The latest send, held until its own ``message.user`` echo shows up in
  // ``events``. POST /messages returns as soon as the run is scheduled
  // (``asyncio.create_task``), so without an optimistic turn the user's
  // message is invisible for the whole runtime-startup window — seconds
  // locally, tens of seconds on a cold sandbox. ``fromIndex`` is the event
  // count at Send: the echo is the first matching ``message.user`` at or
  // after it. Indexing rather than comparing timestamps on purpose — history
  // and live frames carry independent seq spaces, and ``sentAt`` is a CLIENT
  // stamp that clock skew could hold above every server stamp forever,
  // stranding the optimistic turn on screen.
  const [pendingSend, setPendingSend] = useState<{
    text: string;
    sentAt: number; // Unix epoch ms (UTC), client clock
    fromIndex: number;
  } | null>(null);
  // ``send`` needs the event count at Send time but must not re-create itself
  // on every arriving frame (the caller memoises on ``send``'s identity), so
  // it reads the list through a ref rather than closing over it.
  const eventsRef = useRef(events);
  useEffect(() => {
    eventsRef.current = events;
  }, [events]);

  useEffect(() => {
    setEvents([]);
    // ``fromIndex`` indexes into the list we just emptied, and the pending
    // belongs to the previous lead session anyway.
    setPendingSend(null);
    if (!leadSessionId) return;
    const ac = new AbortController();
    let cancelled = false;
    void (async () => {
      try {
        const { items } = await sessionsApi.listEvents(leadSessionId);
        if (cancelled) return;
        setEvents(items);
        const lastSeq = items.length ? items[items.length - 1].seq : 0;
        await sessionsApi.subscribeEvents(
          leadSessionId,
          (ev) => {
            if (!cancelled) setEvents((prev) => [...prev, ev]);
          },
          lastSeq,
          ac.signal,
        );
      } catch {
        /* listEvents failure or SSE drop/abort — no recovery in this minimal hook */
      }
    })();
    return () => {
      cancelled = true;
      ac.abort();
    };
  }, [leadSessionId]);

  const followUpEvents = useMemo(() => {
    if (sinceTs == null) return [];
    // Anchor on the first ``message.user`` event after completion — that is the
    // user's opening follow-up message. Everything before it (the orchestration
    // history and the lead's leaked closing summary) is sliced off. ``> sinceTs``
    // skips the original task goal (and any mid-run user turns), which carry an
    // earlier timestamp.
    const firstUserIdx = events.findIndex(
      (e) =>
        e.event.event_type === "message.user" && (e.timestamp ?? 0) > sinceTs,
    );
    return firstUserIdx === -1 ? [] : events.slice(firstUserIdx);
  }, [events, sinceTs]);
  const rawTurns = useMemo(() => buildTurns(followUpEvents), [followUpEvents]);
  const stableTurns = useStableTurns(rawTurns);

  // Index of the pending send's own echo, or -1 while the runtime is still
  // coming up. Derived rather than cleared from the SSE callback: the pending
  // is replaced by the next send, so there is no state to race.
  const echoIndex = useMemo(() => {
    if (!pendingSend) return -1;
    for (let i = pendingSend.fromIndex; i < events.length; i++) {
      const envelope = events[i];
      if (
        envelope.event.event_type === "message.user" &&
        (envelope.event.payload.text ?? "") === pendingSend.text
      ) {
        return i;
      }
    }
    return -1;
  }, [events, pendingSend]);
  const awaitingRuntime = pendingSend !== null && echoIndex === -1;

  const turns = useMemo(() => {
    if (!pendingSend) return stableTurns;
    if (echoIndex === -1) {
      // Runtime still starting: show the user's message now, anchored on the
      // Send stamp so its header counts the startup window.
      return [
        ...stableTurns,
        {
          id: "pending-follow-up-turn",
          userMessageSeq: 0,
          userText: pendingSend.text,
          blocks: [],
          failedMessage: null,
          userTimestamp: pendingSend.sentAt,
          clientSentAtMs: pendingSend.sentAt,
        },
      ];
    }
    // The echo landed and is by construction the newest user message, so it
    // built the last turn. Carry the Send stamp onto it or the header would
    // restart from the kernel's — the same reset the main chat view had.
    const lastTurn = stableTurns[stableTurns.length - 1];
    if (!lastTurn || lastTurn.userText !== pendingSend.text) return stableTurns;
    return [
      ...stableTurns.slice(0, -1),
      { ...lastTurn, clientSentAtMs: pendingSend.sentAt },
    ];
  }, [stableTurns, pendingSend, echoIndex]);

  // A run is in flight from a user send until the next ``session.idle`` /
  // ``run.failed`` — mirrors the chat view's ``isStreaming``. Derived from the
  // live event stream (last ``message.user`` newer than the last terminal
  // event) so the latest turn shows the streaming indicator (logo loader) and
  // hides its copy button until the turn actually completes, exactly like a
  // normal conversation.
  const streaming = useMemo(() => {
    let lastTerminal = -1;
    let lastUser = -1;
    events.forEach((e, i) => {
      const type = e.event.event_type;
      if (type === "session.idle" || type === "run.failed") lastTerminal = i;
      else if (type === "message.user") lastUser = i;
    });
    return lastUser > lastTerminal;
  }, [events]);

  // The caller is expected to gate on ``sending`` (disable the composer while a
  // turn is in flight); this hook does not itself guard against concurrent sends.
  const send = useCallback(
    async (text: string) => {
      if (!leadSessionId || !text.trim()) return;
      // Optimistic: hold the streaming state through the send-HTTP window too,
      // before the ``message.user`` event echoes back over SSE — otherwise the
      // indicator flickers off between send and echo.
      setSending(true);
      setPendingSend({
        text,
        sentAt: Date.now(),
        fromIndex: eventsRef.current.length,
      });
      try {
        await sessionsApi.sendMessage(leadSessionId, text);
      } catch (err) {
        // The turn never started, so no echo is coming — drop the optimistic
        // turn instead of leaving it stuck on "正在启动…" forever.
        setPendingSend(null);
        throw err;
      } finally {
        setSending(false);
      }
    },
    [leadSessionId],
  );

  return {
    turns,
    // ``streaming`` can only turn true once the echo lands, and the HTTP call
    // returns long before that (the backend schedules the run and replies), so
    // ``awaitingRuntime`` is what covers the startup window in between.
    sending: sending || streaming || awaitingRuntime,
    send,
    events,
    awaitingRuntime,
  };
}
