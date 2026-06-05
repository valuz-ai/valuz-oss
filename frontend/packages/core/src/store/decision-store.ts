/**
 * Decision Inbox Zustand store (ADR-022).
 *
 * Single source of truth for inbox state across the app. Components
 * subscribe via the exported selectors; the singleton SSE subscription
 * (``useDecisionInbox`` hook) writes into the store. Crucially:
 *
 * - Components NEVER instantiate EventSource themselves
 * - Components NEVER call decisionsApi.fetchPending — the hook does
 * - Components ONLY read via selectors / call mutation actions
 *
 * Toast emission lives in the Provider component, NOT the store —
 * keeping the store framework-agnostic. The Provider subscribes to
 * ``pending.size`` changes and fires ``toast.info`` for new entries
 * via the ``seenPendingIds`` set on the store.
 */

import { useMemo } from "react";
import { create } from "zustand";

import type { DecisionEntry } from "../api/decisions-api";

interface DecisionStoreState {
  /** Pending entries keyed by ``pending_id`` — the resolution key
   *  every backend / frontend / SSE handler indexes by. */
  pending: Map<string, DecisionEntry>;

  /** pending_ids the user hasn't seen yet (badge animates). Cleared by
   *  ``markAllRead`` when the drawer opens. */
  unreadIds: Set<string>;

  /** pending_ids the Provider has already fired a toast for. Survives
   *  resolve → re-add cycles so the same pending doesn't double-toast.
   *  (The kernel can re-emit a ``requires_action`` for the same
   *  pending_id when the runtime retries; we don't want to spam.) */
  toastedIds: Set<string>;

  /** Drawer open/closed. */
  isOpen: boolean;

  /** Whether the singleton hook has kicked off its SSE subscription.
   *  Idempotency guard — multiple ``useDecisionInbox`` callers share
   *  one EventSource. */
  _inited: boolean;

  // ---- Mutations (called by the hook + UI) -----------------------

  /** Replace the entire pending set with a fresh snapshot. Used on
   *  initial mount + on SSE reconnect's ``snapshot`` frame. NEW
   *  entries (not present before) are marked unread + toastable. */
  reset: (entries: DecisionEntry[]) => void;

  /** Add a single entry (from SSE ``added`` event). Idempotent on
   *  pending_id collision (latest write wins). */
  add: (entry: DecisionEntry) => void;

  /** Drop a pending (from SSE ``resolved`` event). */
  remove: (pendingId: string) => void;

  /** Mark drawer-visible entries as read. Called when the drawer opens. */
  markAllRead: () => void;

  /** Mark a pending_id as having had its toast already fired. */
  markToasted: (pendingId: string) => void;

  /** Open / close the drawer. */
  setOpen: (open: boolean) => void;

  /** Set the inited flag (used internally by the hook). */
  setInited: () => void;
}

export const useDecisionStore = create<DecisionStoreState>((set) => ({
  pending: new Map(),
  unreadIds: new Set(),
  toastedIds: new Set(),
  isOpen: false,
  _inited: false,

  reset: (entries) =>
    set(() => {
      const pending = new Map<string, DecisionEntry>();
      const unreadIds = new Set<string>();
      for (const e of entries) {
        pending.set(e.pending_id, e);
        // Snapshot entries are NOT marked unread — they were already
        // present before the user opened the app; surfacing them as
        // "new" would mis-fire the toast on every page load.
      }
      return { pending, unreadIds };
    }),

  add: (entry) =>
    set((state) => {
      const pending = new Map(state.pending);
      pending.set(entry.pending_id, entry);
      const unreadIds = new Set(state.unreadIds);
      unreadIds.add(entry.pending_id);
      return { pending, unreadIds };
    }),

  remove: (pendingId) =>
    set((state) => {
      if (!state.pending.has(pendingId)) return {};
      const pending = new Map(state.pending);
      pending.delete(pendingId);
      const unreadIds = new Set(state.unreadIds);
      unreadIds.delete(pendingId);
      return { pending, unreadIds };
    }),

  markAllRead: () =>
    set((state) => {
      if (state.unreadIds.size === 0) return {};
      return { unreadIds: new Set() };
    }),

  markToasted: (pendingId) =>
    set((state) => {
      if (state.toastedIds.has(pendingId)) return {};
      const toastedIds = new Set(state.toastedIds);
      toastedIds.add(pendingId);
      return { toastedIds };
    }),

  setOpen: (isOpen) => set({ isOpen }),

  setInited: () => set({ _inited: true }),
}));

// ---- Convenience selectors --------------------------------------

/** Live list sorted by ``raised_at`` ASC (oldest first — matches the
 *  drawer's reading order).
 *
 *  CRITICAL: the selector returns the *stable* ``pending`` Map ref
 *  (only replaced on mutation), and the sorted array is derived in a
 *  ``useMemo``. Sorting inside the selector itself would mint a fresh
 *  array every render → ``getSnapshot should be cached`` → infinite
 *  re-render loop. */
export const useDecisionPending = (): DecisionEntry[] => {
  const pending = useDecisionStore((s) => s.pending);
  return useMemo(
    () =>
      Array.from(pending.values()).sort((a, b) => a.raised_at - b.raised_at),
    [pending],
  );
};

export const useDecisionUnreadCount = (): number =>
  useDecisionStore((s) => s.unreadIds.size);

export const useDecisionTotalCount = (): number =>
  useDecisionStore((s) => s.pending.size);

export const useDecisionIsOpen = (): boolean =>
  useDecisionStore((s) => s.isOpen);
