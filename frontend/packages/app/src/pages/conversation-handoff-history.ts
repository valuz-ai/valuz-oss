/**
 * Drop a consumed handoff out of the current history entry WITHOUT navigating.
 *
 * The state has to go, or a reload replays the send. But routing to clear it
 * is self-defeating here: ``navigate`` mints a new ``location.key`` even with
 * ``replace``, ConversationPage's bootstrap effect keys on that, and its
 * ``/conversation/new`` branch awaits ``refreshEvents(null)`` — which nulls
 * the optimistic pending message the handoff had just created. The send stayed
 * in flight, so the symptom was a conversation with no bubble, no header, and
 * a live Stop button.
 *
 * React Router keeps user state under ``usr`` and its own bookkeeping
 * (``key`` / ``idx``) alongside it, so blanking just ``usr`` scrubs the
 * handoff while leaving the router's history model intact.
 */
export function dropHandoffFromHistory(): void {
  if (typeof window === "undefined") return;
  const entry = (window.history.state ?? {}) as Record<string, unknown>;
  window.history.replaceState({ ...entry, usr: null }, "");
}
