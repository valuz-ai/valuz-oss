/**
 * Singleton hook that maintains the global Decision Inbox subscription
 * (ADR-022).
 *
 * Mount-once semantics: the underlying ``_init()`` is idempotent via
 * the store's ``_inited`` flag, so calling ``useDecisionInbox()`` from
 * multiple components only opens ONE ``EventSource`` and ONE
 * ``fetchPending()`` call per process. In practice we mount it at the
 * AppShell level (``DecisionInboxProvider``); anywhere else is just
 * defensive and cheap.
 *
 * Wire protocol:
 * - Initial snapshot: ``GET /v1/decisions/pending`` → store.reset
 * - Then ``EventSource /v1/decisions/stream`` named events:
 *   - ``snapshot`` (sent automatically as first frame by backend) → store.reset
 *   - ``added`` (one DecisionEntry payload) → store.add
 *   - ``resolved`` ({pending_id}) → store.remove
 * - Browser EventSource auto-reconnects on transient drops. On
 *   reconnect the backend re-sends ``snapshot`` so we always converge.
 *
 * Errors are silent (matches ``useTaskEvents``). The store is the
 * authoritative state; rendering components read from it.
 */

import { useEffect } from "react";

import { decisionsApi, type DecisionEntry } from "../api/decisions-api";
import { useDecisionStore } from "../store/decision-store";

let _eventSource: EventSource | null = null;

async function _init(): Promise<void> {
  const store = useDecisionStore.getState();
  if (store._inited) return;
  store.setInited();

  // 1) Snapshot via REST — covers the cold-start path before SSE
  //    delivers its first frame.
  try {
    const res = await decisionsApi.fetchPending();
    useDecisionStore.getState().reset(res.entries);
  } catch {
    // Non-fatal — the SSE ``snapshot`` event will populate the store.
  }

  // 2) Open the SSE stream. EventSource auto-reconnects on transient
  //    drops; on reconnect the backend sends a fresh snapshot, so the
  //    store self-heals.
  if (_eventSource) return;
  const es = new EventSource(decisionsApi.streamUrl());
  _eventSource = es;

  es.addEventListener("snapshot", (e: MessageEvent) => {
    try {
      const data = JSON.parse(e.data) as { entries?: DecisionEntry[] };
      useDecisionStore.getState().reset(data.entries ?? []);
    } catch {
      /* malformed snapshot frame — ignore */
    }
  });

  es.addEventListener("added", (e: MessageEvent) => {
    try {
      const data = JSON.parse(e.data) as { entry?: DecisionEntry };
      if (data.entry) {
        useDecisionStore.getState().add(data.entry);
      }
    } catch {
      /* ignore */
    }
  });

  es.addEventListener("resolved", (e: MessageEvent) => {
    try {
      const data = JSON.parse(e.data) as { pending_id?: string };
      if (data.pending_id) {
        useDecisionStore.getState().remove(data.pending_id);
      }
    } catch {
      /* ignore */
    }
  });

  es.addEventListener("heartbeat", () => {
    /* keep-alive — no-op */
  });

  es.onerror = () => {
    // Browser EventSource auto-reconnects with exponential backoff. We
    // could close + reopen here to be explicit, but the default behavior
    // is fine for our use case.
  };
}

/**
 * Idempotent mount hook. Components calling this share the singleton
 * subscription; no per-call EventSource is opened. Returns nothing
 * because the store is the authoritative state — consumers should
 * read via ``useDecisionPending`` / ``useDecisionUnreadCount`` etc.
 */
export function useDecisionInbox(): void {
  useEffect(() => {
    void _init();
    // No teardown — the SSE subscription lives for the app's whole
    // lifetime. The browser closes it on page unload.
  }, []);
}
