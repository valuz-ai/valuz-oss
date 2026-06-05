import { sessionsApi, type SessionEventDTO } from "../api/sessions-api";

/**
 * Connection lifecycle for a single session SSE subscription.
 *
 * - ``connecting``    — the initial fetch is in flight
 * - ``connected``     — the response arrived, frames are flowing
 * - ``disconnected``  — the stream ended cleanly (session went idle)
 * - ``reconnecting``  — a transient error fired and we're backing off
 * - ``error``         — retries exhausted; the user must call ``reconnect()``
 * - ``idle``          — controller has not been started (or was stopped)
 */
export type SessionStreamState =
  | "idle"
  | "connecting"
  | "connected"
  | "disconnected"
  | "reconnecting"
  | "error";

export interface SessionStreamSnapshot {
  state: SessionStreamState;
  attempt: number;
  lastSeq: number;
  errorMessage: string | null;
  /**
   * Wallclock timestamp (ms) at which the next reconnect attempt will
   * fire while in ``reconnecting`` state. ``null`` otherwise. Lets the
   * UI render a "reconnecting in 3s..." countdown.
   */
  nextRetryAt: number | null;
}

export interface SessionStreamOptions {
  sessionId: string;
  onEvent: (event: SessionEventDTO) => void;
  onStateChange?: (snapshot: SessionStreamSnapshot) => void;
  /**
   * Initial seq to resume from. If omitted, the controller starts from
   * 0 — backend will replay all persisted events.
   */
  startSeq?: number;
  /**
   * Backoff schedule in milliseconds. Defaults to 1s, 2s, 4s, 8s, 16s
   * — five attempts before transitioning to ``error``.
   */
  backoffSchedule?: number[];
}

const DEFAULT_BACKOFF = [1000, 2000, 4000, 8000, 16000];

/**
 * Manages a single SSE subscription with auto-reconnect on transient
 * failure and a manual recovery path when retries are exhausted.
 *
 * The controller tracks ``lastSeq`` from each incoming envelope so a
 * reconnect picks up from exactly the right cursor — ``after_seq`` on
 * the server side then replays any persisted events the client missed
 * during the gap, and the in-memory broadcast handles live deltas.
 */
export const createSessionStreamController = (
  opts: SessionStreamOptions,
): {
  start: () => void;
  stop: () => void;
  reconnect: () => void;
  snapshot: () => SessionStreamSnapshot;
} => {
  const backoff = opts.backoffSchedule ?? DEFAULT_BACKOFF;
  let abort: AbortController | null = null;
  let retryTimer: ReturnType<typeof setTimeout> | null = null;
  let stopped = false;

  const snap: SessionStreamSnapshot = {
    state: "idle",
    attempt: 0,
    lastSeq: opts.startSeq ?? 0,
    errorMessage: null,
    nextRetryAt: null,
  };

  const emit = () => opts.onStateChange?.({ ...snap });

  const setState = (
    state: SessionStreamState,
    extras: Partial<SessionStreamSnapshot> = {},
  ) => {
    Object.assign(snap, { state, ...extras });
    emit();
  };

  const clearRetryTimer = () => {
    if (retryTimer != null) {
      clearTimeout(retryTimer);
      retryTimer = null;
    }
  };

  const connect = () => {
    if (stopped) return;
    abort = new AbortController();
    setState("connecting", { errorMessage: null, nextRetryAt: null });

    sessionsApi
      .subscribeEvents(
        opts.sessionId,
        (event) => {
          // Reset retry counter on any successful frame — connection is
          // demonstrably healthy.
          if (snap.state !== "connected") {
            setState("connected", { attempt: 0 });
          }
          if (event.seq > snap.lastSeq) {
            snap.lastSeq = event.seq;
            // Don't emit() for every event — the consumer of onEvent
            // already drives re-renders via its own store.
          }
          opts.onEvent(event);
        },
        snap.lastSeq > 0 ? snap.lastSeq : undefined,
        abort.signal,
      )
      .then(() => {
        if (stopped) return;
        // Stream ended cleanly (session went idle on the server).
        setState("disconnected", { attempt: 0, nextRetryAt: null });
      })
      .catch((err: unknown) => {
        if (stopped) return;
        if (abort?.signal.aborted) return;
        scheduleRetry(err);
      });
  };

  const scheduleRetry = (err: unknown) => {
    const message = err instanceof Error ? err.message : String(err);
    const delay = backoff[snap.attempt];

    if (delay === undefined) {
      // Retries exhausted — surface to the UI for manual recovery.
      setState("error", {
        errorMessage: message,
        nextRetryAt: null,
      });
      return;
    }

    snap.attempt += 1;
    setState("reconnecting", {
      errorMessage: message,
      nextRetryAt: Date.now() + delay,
    });

    retryTimer = setTimeout(() => {
      retryTimer = null;
      connect();
    }, delay);
  };

  return {
    start() {
      if (stopped) return;
      if (snap.state !== "idle" && snap.state !== "disconnected") return;
      snap.attempt = 0;
      connect();
    },
    stop() {
      stopped = true;
      clearRetryTimer();
      abort?.abort();
      abort = null;
      setState("idle", { attempt: 0, nextRetryAt: null });
    },
    reconnect() {
      // Manual recovery — reset attempt counter so the new try gets
      // a fresh budget, then connect immediately.
      if (stopped) return;
      clearRetryTimer();
      abort?.abort();
      abort = null;
      snap.attempt = 0;
      connect();
    },
    snapshot() {
      return { ...snap };
    },
  };
};
