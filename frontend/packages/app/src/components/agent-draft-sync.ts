/**
 * Data → draft reconciliation for the agent detail tabs.
 *
 * Every tab on the agent detail view keeps a local draft that the user edits
 * and saves explicitly. The view also re-fetches the agent — on mount, and
 * after ANY tab's save (``doSave`` reloads so the other tabs see fresh data).
 * A blind ``setDraft(server.value)`` on each of those re-fetches throws away
 * whatever the user is halfway through typing in a different tab: save the
 * name while a long instruction is half-written, or keep typing while the
 * instructions save is still in flight, and the reply lands and reverts the
 * text to the stored version.
 *
 * So a fresh server value is adopted only when the draft is *pristine* —
 * still identical to the value it was last seeded with. A draft the user has
 * touched belongs to the user until they save or leave.
 */

/** A different agent landed in the same mounted view (the full-page route
 * swaps ``slug`` without remounting). Those drafts belong to the previous
 * agent, so they are replaced regardless of how dirty they are. */
export interface ReconcileOptions<T> {
  agentChanged?: boolean;
  isEqual?: (a: T, b: T) => boolean;
}

export function reconcileDraft<T>(
  current: T,
  lastSeeded: T,
  incoming: T,
  { agentChanged = false, isEqual = Object.is }: ReconcileOptions<T> = {},
): T {
  if (agentChanged) return incoming;
  return isEqual(current, lastSeeded) ? incoming : current;
}

/** The brain tab's draft is an object, so it needs a structural comparison —
 * every re-fetch builds a new one and ``Object.is`` would call it dirty. */
export function sameBrain(
  a: { runtime: string; providerId: string | null; model: string },
  b: { runtime: string; providerId: string | null; model: string },
): boolean {
  return (
    a.runtime === b.runtime &&
    a.providerId === b.providerId &&
    a.model === b.model
  );
}
