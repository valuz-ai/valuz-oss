import type { SessionEventDTO } from "@valuz/core";

/**
 * Loading-state derivation for the conversation view
 * (docs/design/session-stream-lifetime.md §2.1).
 *
 * The composer's Stop button, the streaming logo, and the "已处理 X 秒" timer
 * are all driven by whether a turn is active. On the session-lifetime stream
 * the stream's open/close says NOTHING about turns, so the state is derived
 * from two authoritative inputs:
 *
 * - ``sendPending`` — the optimistic click → turn-start bridge. Set on Send,
 *   released by the turn's ``message.user`` echo / turn-start
 *   ``session.update{running}`` / a genuine terminal frame / a send error.
 *   While pending it OVERRIDES a terminal status: during a slow start
 *   (attachment parse threading) the session legitimately still reads the
 *   pre-turn ``idle`` for seconds — honoring it froze the elapsed timer and
 *   reverted the Stop button (the image-upload regression).
 * - ``status === "running"`` — the reconciled session status, written by the
 *   data-plane ``session.update`` events (the kernel announces ``running`` at
 *   turn start since #590), the optimistic send write, and the turn-boundary
 *   ``refreshActiveSession``. This is what carries busy for turns started by
 *   ANY actor — queue drain, schedule, another client — not just local sends.
 */

/** Session statuses that mean the turn is finished (no loading / no running pill). */
export const TERMINAL_SESSION_STATUSES: ReadonlySet<string> = new Set([
  "idle",
  "failed",
  "cancelled",
  "archived",
  "terminated",
]);

export const isTerminalSessionStatus = (
  status: string | null | undefined,
): boolean => status != null && TERMINAL_SESSION_STATUSES.has(status);

/**
 * Whether the composer should render its loading / Stop state.
 *
 * - ``sendPending`` true → loading, regardless of status (the optimistic
 *   click → turn-start bridge; a stale pre-turn terminal status must not
 *   collapse it — the slow-start hazard above).
 * - status ``running`` → loading (a turn is in flight, whoever started it).
 * - anything else → not loading. A stuck ``sendPending`` cannot pin the state
 *   forever: it is released by the turn's start/terminal events and by send
 *   errors, and the turn-boundary reconciliation converges ``status``.
 */
export const deriveTurnActive = (
  sendPending: boolean,
  status: string | null | undefined,
): boolean => sendPending || status === "running";

/**
 * Whether a ``session.update`` frame's status may be applied to the session
 * list (and thereby the header pill / composer busy state).
 *
 * The rule is deliberately SYMMETRIC: replayed frames are fully inert,
 * non-terminal and terminal alike. An earlier gate special-cased them
 * (replayed ``running`` always applied, replayed terminal suppressed "so a
 * redelivered old terminal cannot clobber a newer running turn") and that
 * asymmetry deadlocked the pill at "running" on cloud sessions:
 *
 * - a turn's LIVE frames arrive from the sandbox kernel WITHOUT ``event_uid``
 *   (the kernel store never mints uids), so the page's replay detection has
 *   nothing to remember while the turn actually runs;
 * - the durable-store backfill on SSE reconnect then redelivers the finished
 *   turn WITH uids — the first backfill seeds the seen-set, and every later
 *   backfill (cloud streams churn: CLB idle timeouts, instance clamps) counts
 *   as a replay;
 * - under the asymmetric gate each such replay applied ``running``@turn-start
 *   while suppressing the terminal that follows it, re-flipping a finished
 *   conversation to 运行中 until a manual refresh re-read REST state.
 *
 * Symmetry also covers the clobber the old gate feared: a genuinely new
 * turn's ``running`` arrives as a live or first-delivery frame — never as a
 * seen-uid replay — so dropping replayed ``running`` loses nothing.
 */
export const shouldApplySessionStatus = (
  status: string | null | undefined,
  isReplayOfSeen: boolean,
): boolean => Boolean(status) && !isReplayOfSeen;

export type ProviderCatalogStatus = "loading" | "ready" | "error";

export const shouldShowNoModelEmptyState = ({
  isNewConversation,
  pageLoading,
  providerCount,
  providerStatus,
}: {
  isNewConversation: boolean;
  pageLoading: boolean;
  providerCount: number;
  providerStatus: ProviderCatalogStatus;
}): boolean =>
  isNewConversation &&
  !pageLoading &&
  providerStatus === "ready" &&
  providerCount === 0;

export const shouldRefreshConversationHistory = ({
  hydratedSessionId,
  sessionId,
  promotedWithLiveStream,
}: {
  hydratedSessionId: string | null;
  sessionId: string;
  promotedWithLiveStream: boolean;
}): boolean =>
  !promotedWithLiveStream && hydratedSessionId !== sessionId;

/**
 * Derive the transient host-side Citation / Audit / Task Coverage window from
 * persisted lifecycle events. Reading the event sequence instead of a timer
 * keeps live delivery, reconnect backfill, and mid-pass page entry consistent.
 * Any terminal event clears the state defensively if a failed pass never
 * managed to emit its explicit ``completed`` marker.
 */
export const reducePostRunVerificationActive = (
  active: boolean,
  event: SessionEventDTO,
): boolean => {
  const eventType = event.event.event_type;
  const payload = event.event.payload;
  if (
    eventType === "session.turn_phase" &&
    payload.phase === "post_run_verification"
  ) {
    if (payload.state === "started") return true;
    if (payload.state === "completed") return false;
    return active;
  }
  if (
    eventType === "message.user" ||
    eventType === "session.idle" ||
    eventType === "run.failed" ||
    (eventType === "session.update" &&
      isTerminalSessionStatus(payload.status))
  ) {
    return false;
  }
  return active;
};

export const derivePostRunVerificationActive = (
  events: SessionEventDTO[],
): boolean => events.reduce(reducePostRunVerificationActive, false);
