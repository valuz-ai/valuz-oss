import { useEffect } from "react";
import { sessionsApi, type TodoItem } from "@valuz/core";

type SessionLifecycleParams = {
  selectedSessionId: string | null;
  /** ``useSessionSubscription``'s opener — supersedes the previous stream. */
  subscribeToSession: (sessionId: string, afterSeq: number) => void;
  historyCursorRef: { current: number };
  /** The session-lifetime stream's controller (owned by the subscription). */
  abortRef: { current: AbortController | null };
  selectedSessionIdRef: { current: string | null };
  setTodos: (todos: TodoItem[]) => void;
};

/**
 * ── Session-open / stream-teardown lifecycle ─────────────────────────
 *
 * Owns the two effects that bound the session-lifetime stream: the
 * SESSION-OPEN effect (todos hydration + stream open, with the
 * connection-budget visibility guard) and the unmount teardown of any
 * in-flight SSE subscription. Bodies, comments and dependency arrays
 * are moved verbatim from ``ConversationPage``.
 */
export function useSessionLifecycle({
  selectedSessionId,
  subscribeToSession,
  historyCursorRef,
  abortRef,
  selectedSessionIdRef,
  setTodos,
}: SessionLifecycleParams) {
  // SESSION-OPEN effect — the ONE owner of the data-plane stream
  // (docs/design/session-stream-lifetime.md). Opening a session:
  //   1. hydrates ``todos`` from the canonical detail (persistent snapshot);
  //   2. opens the session-lifetime stream. It carries EVERY turn for as long
  //      as the page stays here — resumed mid-turn sessions, fresh sends,
  //      drained queue items, scheduled turns, bg-task wake-ups — so the old
  //      created→running control-plane bridge, the finished-turn reconcile
  //      and the "subscribe only while running" dance are all gone: an open
  //      stream on an idle session is harmless (it parks on server
  //      heartbeats; busy is derived from events + status, not from the
  //      stream being open).
  // The stream is superseded by the next session's open (subscribeToSession
  // aborts the previous controller) and torn down on unmount.
  //
  // CONNECTION-BUDGET guard: Chromium caps ~6 connections per origin
  // (HTTP/1.1), and held SSE streams count against it — a pool exhaustion
  // blocks every fetch (the verified white-screen incident class). A hidden
  // tab / minimized window doesn't need live paint, so release the held
  // stream on ``visibilitychange: hidden`` and reopen on return — the
  // (re)open path resumes from the history cursor and the server's initial
  // drain + reconcile burst deliver everything missed while hidden. This
  // keeps the always-open model's steady-state cost scoped to the ONE
  // visible conversation tab.
  useEffect(() => {
    if (!selectedSessionId) return;
    const sid = selectedSessionId;
    let cancelled = false;
    sessionsApi
      .get(sid)
      .then((detail) => {
        if (cancelled) return;
        if (detail.todos !== undefined && detail.todos !== null) {
          setTodos(detail.todos);
        }
      })
      .catch(() => {
        // Non-fatal — refreshEvents already hydrated todos from the
        // historical event log.
      });
    if (document.visibilityState !== "hidden") {
      subscribeToSession(sid, historyCursorRef.current);
    }
    const onVisibility = () => {
      if (cancelled) return;
      if (document.visibilityState === "hidden") {
        if (abortRef.current) {
          abortRef.current.abort();
          abortRef.current = null;
        }
      } else if (selectedSessionIdRef.current === sid && !abortRef.current) {
        subscribeToSession(sid, historyCursorRef.current);
      }
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      cancelled = true;
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [selectedSessionId, subscribeToSession]);

  // Tear down any in-flight SSE subscription when the page unmounts.
  useEffect(() => {
    return () => {
      if (abortRef.current) {
        abortRef.current.abort();
        abortRef.current = null;
      }
    };
  }, []);
}
