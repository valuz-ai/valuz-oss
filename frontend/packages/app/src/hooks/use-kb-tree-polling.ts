import { useEffect, useRef } from "react";

/** How often to re-read the tree while a parse is in flight. Matches the
 *  document-detail poll so the list and the panel never disagree for longer
 *  than one tick. */
const DEFAULT_INTERVAL_MS = 3000;

export interface UseKbTreePollingOptions {
  /** Whether anything in the tree is still ``queued`` / ``processing``.
   *  Polling stops the moment this goes false, so a settled library costs
   *  nothing. */
  active: boolean;
  /** Re-read the tree. Must not reset selection or expansion — this fires
   *  underneath the user, and a refresh that collapsed their folders would be
   *  worse than the stale badge it fixes. */
  refresh: () => Promise<void>;
  intervalMs?: number;
}

/**
 * Keep a knowledge-base tree's statuses live while its documents are parsing.
 *
 * The document *detail* panel already polls itself, so an open document walks
 * ``queued → processing → ready`` on its own. The **list** had nothing: its
 * badges were whatever the last ``enterKb`` returned, and parsing takes
 * minutes, so every freshly uploaded document sat at "等待中" until the user
 * navigated away and back. The API was right the whole time — only the screen
 * was stale, which is the version of this bug that wastes the most time,
 * because it looks exactly like a backend that has stopped working.
 *
 * Deliberately driven by a caller-supplied ``refresh`` rather than owning the
 * fetch: the page already knows what it has expanded and how to merge a
 * response into its state, and duplicating that here would give the two copies
 * room to disagree.
 */
export function useKbTreePolling({
  active,
  refresh,
  intervalMs = DEFAULT_INTERVAL_MS,
}: UseKbTreePollingOptions): void {
  // Held in a ref so a caller that rebuilds ``refresh`` every render does not
  // restart the interval on every render — the effect depends on ``active``
  // and the interval only.
  const refreshRef = useRef(refresh);
  refreshRef.current = refresh;

  useEffect(() => {
    if (!active) return;
    // Guards against a slow response landing after the effect was torn down
    // (the user left the KB, or everything settled): its ``setState`` would
    // reinstate a tree that is no longer on screen.
    let cancelled = false;
    const handle = window.setInterval(() => {
      void (async () => {
        try {
          if (cancelled) return;
          await refreshRef.current();
        } catch {
          // Transient fetch failure — the next tick retries. Surfacing a toast
          // every 3s for a blip would be worse than the stale badge.
        }
      })();
    }, intervalMs);
    return () => {
      cancelled = true;
      window.clearInterval(handle);
    };
  }, [active, intervalMs]);
}
