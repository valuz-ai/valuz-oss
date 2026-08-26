/**
 * Gate for the project-detail send handoff.
 *
 * The handoff arrives at ``/conversation/new?project=A`` carrying only a
 * draft, and the page mints the session and sends. Both of the conditions
 * below are load-bearing, and each was learned the hard way:
 *
 * 1. **The project must be bound.** ``?project=`` becomes
 *    ``selectedProjectId`` only after bootstrap has fetched and validated the
 *    project list, and ``ensureSession`` mints from that state, not the URL.
 *    Sending earlier falls back to ``"chat-default"``, which mints a QUICK
 *    CHAT — unbound from the project and routed by the chat target picker
 *    instead of the project's execution origin.
 *
 * 2. **Bootstrap must have FINISHED.** Its ``/conversation/new`` branch sets
 *    ``selectedProjectId`` and then, several statements later, awaits
 *    ``refreshEvents(null)`` — the canonical "switch away from any session"
 *    path, which unconditionally nulls the optimistic pending message. Gating
 *    on the binding alone let the send fire in that window: the optimistic
 *    turn was created and then wiped a moment later, so the message went out
 *    but the user saw no bubble and no runtime-startup header at all.
 *
 * Extracted so both conditions are testable — ConversationPage itself has no
 * harness, which is why this class of ordering bug kept reaching QA.
 */
export interface ProjectHandoffReadiness {
  /** ``?project=`` on the URL, or null for a non-project entry. */
  projectParam: string | null;
  /** Bootstrap's validated binding; null until it resolves. */
  selectedProjectId: string | null;
  /**
   * Whether bootstrap has run to completion for the current draft page. False
   * while it is still mid-flight, including the window in which it clears
   * per-session state.
   */
  draftBootstrapSettled: boolean;
}

export function canSendProjectHandoff(
  params: ProjectHandoffReadiness,
): boolean {
  const { projectParam, selectedProjectId, draftBootstrapSettled } = params;
  // Never send into a page whose bootstrap is still tearing down and
  // rebuilding per-session state — it would wipe the optimistic turn.
  if (!draftBootstrapSettled) return false;
  // No project in the URL: nothing further to wait for (temp / quick chat).
  if (!projectParam) return true;
  return selectedProjectId === projectParam;
}
