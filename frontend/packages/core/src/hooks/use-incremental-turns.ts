import { useMemo, useState } from "react";
import type { ConversationTurn, SessionEventDTO } from "@valuz/shared";
import { createIncrementalTurns } from "../conversation";

/**
 * Streaming-friendly replacement for ``useStableTurns(buildTurns(events))``.
 *
 * ``buildTurns`` re-folds the ENTIRE event array on every render; driven per
 * token during a long streamed reply that is O(N²) and makes deltas arrive in
 * visible bursts (the main thread saturates re-walking all prior events and
 * re-concatenating the growing assistant text from scratch). This holds a
 * resumable builder across renders and, when ``events`` grew append-only, folds
 * ONLY the new events — then hands back turns that already satisfy the
 * ``useStableTurns`` reference contract (stable refs for sealed turns, fresh
 * refs for the mutated tail), so no extra stabilisation pass is needed.
 *
 * A non-append change (window replace, reconcile splice, session switch)
 * transparently triggers a full rebuild inside the builder, so correctness
 * never depends on the caller only ever appending.
 */
export function useIncrementalTurns(
  events: SessionEventDTO[],
): ConversationTurn[] {
  // Lazy ``useState`` gives one stable builder instance for the component's
  // lifetime (ConversationPage remounts per navigation, so state never leaks
  // across sessions). ``update`` folds only what's new and returns the snapshot.
  const [builder] = useState(createIncrementalTurns);
  return useMemo(() => builder.update(events), [builder, events]);
}
