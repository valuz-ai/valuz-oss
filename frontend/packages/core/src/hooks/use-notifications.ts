/**
 * Singleton hook that maintains the global notification subscription
 * (docs/design/notifications.md).
 *
 * Mount-once (idempotent via the store's ``_inited`` flag): one SSE stream +
 * one snapshot per process regardless of how many components call it. Mounted
 * at the AppShell level (``NotificationProvider``).
 *
 * Wire protocol (``GET /v1/notifications/stream``):
 * - ``snapshot`` ({entries,unread}) → store.reset
 * - ``added``   ({entry})           → store.add
 * - ``updated`` ({entry})           → store.update  (read-state change)
 * - ``resolved`` ({id})             → store.remove
 * Reads over ``fetch`` (not EventSource) so the request carries auth; a stuck
 * stream is bounded by a low-frequency REST poll backstop.
 */

import { useEffect } from "react";

import {
  notificationsApi,
  type NotificationEntry,
} from "../api/notifications-api";
import { fetchEventSource } from "../api/fetch-event-source";
import { useNotificationStore } from "../store/notification-store";

let _closeStream: (() => void) | null = null;
let _pollTimer: ReturnType<typeof setInterval> | null = null;

const POLL_BACKSTOP_MS = 60_000;

async function _init(): Promise<void> {
  const store = useNotificationStore.getState();
  if (store._inited) return;
  store.setInited();

  try {
    const res = await notificationsApi.fetchOpen();
    useNotificationStore.getState().reset(res.entries);
  } catch {
    // Non-fatal — the SSE snapshot frame will populate the store.
  }

  if (_closeStream) return;
  _closeStream = fetchEventSource(
    () => notificationsApi.streamUrl(),
    (frame) => {
      try {
        if (frame.event === "snapshot") {
          const data = JSON.parse(frame.data) as {
            payload?: { entries?: NotificationEntry[] };
          };
          useNotificationStore.getState().reset(data.payload?.entries ?? []);
        } else if (frame.event === "added") {
          const data = JSON.parse(frame.data) as {
            payload?: { entry?: NotificationEntry };
          };
          if (data.payload?.entry) {
            useNotificationStore.getState().add(data.payload.entry);
          }
        } else if (frame.event === "updated") {
          const data = JSON.parse(frame.data) as {
            payload?: { entry?: NotificationEntry };
          };
          if (data.payload?.entry) {
            useNotificationStore.getState().update(data.payload.entry);
          }
        } else if (frame.event === "resolved") {
          const data = JSON.parse(frame.data) as { payload?: { id?: string } };
          if (data.payload?.id) {
            useNotificationStore.getState().remove(data.payload.id);
          }
        }
        // "heartbeat"/"ping" and anything else: ignore.
      } catch {
        // Malformed frame — ignore.
      }
    },
  );

  _pollTimer ??= setInterval(() => {
    notificationsApi
      .fetchOpen()
      .then((res) => useNotificationStore.getState().reset(res.entries))
      .catch(() => {
        // Non-fatal — next tick / SSE snapshot retries.
      });
  }, POLL_BACKSTOP_MS);
}

/** Idempotent mount hook. Shares the singleton subscription. (Named distinctly
 *  from the ``useNotifications`` store selector.) */
export function useNotificationInbox(): void {
  useEffect(() => {
    void _init();
    // No teardown — the subscription lives for the app's whole lifetime.
  }, []);
}

/**
 * Mark every open, unread notification for ``sessionId`` read — the badge-clear
 * that fires when the user opens the conversation the notification points at
 * (direct link, notification card, or the conversation list all land on the
 * same page). Without this the ``markRead`` endpoint was never called from
 * anywhere, so the unread badge lingered after the user had clearly seen the
 * item.
 *
 * Optimistic: flips the store entry's ``read_at`` immediately so the derived
 * unread badge decrements without waiting for the ~2.5s SSE re-snapshot, then
 * persists each read via the REST endpoint. Marking read only clears the unread
 * state — the entry stays OPEN in the drawer until it is resolved/dismissed.
 * Fire-and-forget: a failed persist self-heals on the next snapshot (which
 * re-reads ``read_at`` from the durable ledger). No-op when nothing matches.
 */
export function markSessionNotificationsRead(sessionId: string): void {
  if (!sessionId) return;
  const store = useNotificationStore.getState();
  const now = Date.now();
  for (const entry of store.entries.values()) {
    if (entry.session_id !== sessionId || entry.read_at != null) continue;
    store.update({ ...entry, read_at: now });
    notificationsApi.markRead(entry.id).catch(() => {
      // Non-fatal — the next SSE snapshot reconciles read_at from the ledger.
    });
  }
}

/**
 * Dismiss one notification with an OPTIMISTIC store removal — the card leaves
 * the drawer immediately instead of waiting for the ~2.5s SSE re-snapshot
 * (which is what made dismiss feel slow). Fire-and-forget: a failed persist
 * self-heals on the next snapshot, which re-reads the durable ledger and
 * restores the entry.
 */
export function dismissNotification(id: string): void {
  useNotificationStore.getState().remove(id);
  notificationsApi.dismiss(id).catch(() => {
    // Non-fatal — the next SSE snapshot reconciles from the ledger.
  });
}

/**
 * The drawer's "clear all": optimistically empty the open set (badge and cards
 * clear at once), then persist via ``:dismiss-all``. Entries move to history —
 * a pending question stays answerable in its session.
 */
export function dismissAllNotifications(): void {
  const store = useNotificationStore.getState();
  for (const id of Array.from(store.entries.keys())) {
    store.remove(id);
  }
  notificationsApi.dismissAll().catch(() => {
    // Non-fatal — the next SSE snapshot reconciles from the ledger.
  });
}
