/**
 * Global "running runs" overview, shared across consumers.
 *
 * A single module-level connection backs every mount (the sidebar count badge +
 * the Activity page), so we never open N streams. It seeds once from
 * ``/v1/runs?status=running`` then refreshes on the control-plane stream.
 * Lifecycle frames (``run.started`` / ``run.finished`` / ``run.status``) are the
 * fast-path trigger; the REST snapshot stays the source of the enriched
 * {@link RunSummary} rows (title / project / todo). Keeps the last good snapshot
 * on error.
 *
 * Backstop (see ``_maybeBackstopPoll``): the control stream can miss the
 * ``run.finished`` frame that clears a finished run — a stalled or
 * proxy-buffered ``/v1/stream`` delivers nothing yet never errors, so
 * ``fetchEventSource`` doesn't even reconnect, and the running dot then sticks
 * with no recovery. A slow reconciliation poll runs ONLY while at least one run
 * is showing as running (so an idle client never polls — the event-driven
 * design is preserved for the common case) and is paused while the tab is
 * hidden. It bounds a stuck running indicator to ``BACKSTOP_POLL_MS`` regardless
 * of why the stream missed the frame.
 */

import { useEffect, useState } from "react";

import { runsApi, type RunSummary } from "../api/runs-api";
import { subscribeUserStream } from "../api/user-stream";

// Collapse a burst of lifecycle frames (a turn emits several) into one refresh.
const REFRESH_DEBOUNCE_MS = 250;
// Backstop reconciliation cadence — only ticks while something shows running.
const BACKSTOP_POLL_MS = 15_000;

let _running: RunSummary[] = [];
const _subscribers = new Set<() => void>();
let _unsubStream: (() => void) | null = null;
let _debounce: number | null = null;
let _inFlight = false;
let _backstop: number | null = null;
let _onVisibility: (() => void) | null = null;

const _notify = (): void => {
  _subscribers.forEach((fn) => fn());
};

const _poll = async (): Promise<void> => {
  if (_inFlight) return;
  _inFlight = true;
  try {
    const res = await runsApi.list({ status: "running" });
    _running = res.runs;
    _notify();
  } catch {
    // keep the last good snapshot; the next frame/refresh retries
  } finally {
    _inFlight = false;
  }
};

const _scheduleRefresh = (): void => {
  if (_debounce !== null) return;
  _debounce = window.setTimeout(() => {
    _debounce = null;
    void _poll();
  }, REFRESH_DEBOUNCE_MS);
};

const _tabHidden = (): boolean =>
  typeof document !== "undefined" && document.visibilityState === "hidden";

// Reconcile against the server ONLY while a run is showing as running — an idle
// snapshot has no dot that could be stuck, so an idle client never polls. This
// self-heals a running indicator the control stream failed to clear (stalled /
// reconnecting / proxy-buffered ``/v1/stream``) within one cadence.
const _maybeBackstopPoll = (): void => {
  if (_tabHidden()) return;
  if (_running.length === 0) return;
  void _poll();
};

/**
 * Force an immediate refresh of the shared running-runs snapshot — call after an
 * action that mints a run (sending the first message of a session) so the
 * sidebar's runs-derived lists paint without waiting for the stream frame.
 *
 * The control-plane stream normally delivers the ``run.started`` transition on
 * its own; this is a belt-and-suspenders nudge for the mint path.
 */
export const refreshRunningRuns = (): void => {
  void _poll();
};

const _start = (): void => {
  if (_unsubStream) return;
  void _poll(); // cold-start snapshot
  _unsubStream = subscribeUserStream((frame) => {
    // Any run lifecycle transition may add/remove/restate a running run.
    if (
      frame.eventType === "run.started" ||
      frame.eventType === "run.finished" ||
      frame.eventType === "run.status"
    ) {
      _scheduleRefresh();
    }
  });
  _backstop = window.setInterval(_maybeBackstopPoll, BACKSTOP_POLL_MS);
  if (typeof document !== "undefined") {
    // Returning to a backgrounded tab: reconcile immediately (the backstop was
    // paused while hidden, so a run may have finished in the meantime).
    _onVisibility = (): void => {
      if (!_tabHidden() && _running.length > 0) void _poll();
    };
    document.addEventListener("visibilitychange", _onVisibility);
  }
};

const _stop = (): void => {
  _unsubStream?.();
  _unsubStream = null;
  if (_debounce !== null) {
    window.clearTimeout(_debounce);
    _debounce = null;
  }
  if (_backstop !== null) {
    window.clearInterval(_backstop);
    _backstop = null;
  }
  if (_onVisibility !== null && typeof document !== "undefined") {
    document.removeEventListener("visibilitychange", _onVisibility);
    _onVisibility = null;
  }
};

export interface UseRunningRunsResult {
  runs: RunSummary[];
  count: number;
}

export const useRunningRuns = (): UseRunningRunsResult => {
  const [, setTick] = useState(0);
  useEffect(() => {
    const sub = (): void => setTick((t) => t + 1);
    _subscribers.add(sub);
    _start();
    return () => {
      _subscribers.delete(sub);
      if (_subscribers.size === 0) _stop();
    };
  }, []);
  return { runs: _running, count: _running.length };
};
