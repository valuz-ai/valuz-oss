/**
 * Singleton hook that maintains the global Decision Inbox subscription
 * (ADR-022).
 *
 * Mount-once semantics: the underlying ``_init()`` is idempotent via
 * the store's ``_inited`` flag, so calling ``useDecisionInbox()`` from
 * multiple components only opens ONE stream and ONE ``fetchPending()``
 * call per process. In practice we mount it at the AppShell level
 * (``DecisionInboxProvider``); anywhere else is just defensive and cheap.
 *
 * Wire protocol:
 * - Initial snapshot: ``GET /v1/decisions/pending`` → store.reset
 * - Then SSE ``GET /v1/decisions/stream`` named events:
 *   - ``snapshot`` (sent automatically as first frame by backend) → store.reset
 *   - ``added`` (one DecisionEntry payload) → store.add
 *   - ``resolved`` ({pending_id}) → store.remove
 * - ``fetchEventSource`` auto-reconnects on transient drops; on reconnect the
 *   backend re-sends ``snapshot`` so the store always converges. It reads the
 *   stream over ``fetch`` (not ``EventSource``) so the request carries auth.
 *
 * Errors are silent (matches ``useTaskEvents``). The store is the
 * authoritative state; rendering components read from it.
 */

import { useEffect } from "react";

import { decisionsApi, type DecisionEntry } from "../api/decisions-api";
import { fetchEventSource } from "../api/fetch-event-source";
import { useDecisionStore } from "../store/decision-store";

let _closeStream: (() => void) | null = null;

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

  // 2) Open the SSE stream. fetchEventSource auto-reconnects on transient
  //    drops; on reconnect the backend sends a fresh snapshot, so the
  //    store self-heals.
  if (_closeStream) return;
  _closeStream = fetchEventSource(
    () => decisionsApi.streamUrl(),
    (frame) => {
      try {
        if (frame.event === "snapshot") {
          const data = JSON.parse(frame.data) as { entries?: DecisionEntry[] };
          useDecisionStore.getState().reset(data.entries ?? []);
        } else if (frame.event === "added") {
          const data = JSON.parse(frame.data) as { entry?: DecisionEntry };
          if (data.entry) useDecisionStore.getState().add(data.entry);
        } else if (frame.event === "resolved") {
          const data = JSON.parse(frame.data) as { pending_id?: string };
          if (data.pending_id) {
            useDecisionStore.getState().remove(data.pending_id);
          }
        }
        // "heartbeat" and any other frames: ignore.
      } catch {
        // Malformed frame — ignore.
      }
    },
  );
}

/**
 * Idempotent mount hook. Components calling this share the singleton
 * subscription; no per-call stream is opened. Returns nothing because the
 * store is the authoritative state — consumers should read via
 * ``useDecisionPending`` / ``useDecisionUnreadCount`` etc.
 */
export function useDecisionInbox(): void {
  useEffect(() => {
    void _init();
    // No teardown — the subscription lives for the app's whole lifetime.
  }, []);
}
