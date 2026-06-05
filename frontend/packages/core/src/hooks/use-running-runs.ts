/**
 * Global "running runs" overview, shared across consumers.
 *
 * A single module-level poller backs every mount (the sidebar count badge +
 * the Activity page), so we never open N intervals. Polls ``/v1/runs`` every
 * {@link POLL_MS}; skips a tick while the document is hidden; keeps the last
 * good snapshot on error.
 */

import { useEffect, useState } from "react";

import { runsApi, type RunSummary } from "../api/runs-api";

const POLL_MS = 2500;

let _running: RunSummary[] = [];
const _subscribers = new Set<() => void>();
let _timer: number | null = null;
let _inFlight = false;

const _notify = (): void => {
  _subscribers.forEach((fn) => fn());
};

const _poll = async (force = false): Promise<void> => {
  if (_inFlight) return;
  // Pause recurring ticks while the window is backgrounded, but always run a
  // forced poll (initial mount) so a freshly-opened/hidden window still paints.
  if (!force && typeof document !== "undefined" && document.hidden) return;
  _inFlight = true;
  try {
    const res = await runsApi.list({ status: "running" });
    _running = res.runs;
    _notify();
  } catch {
    // keep the last good snapshot; the next tick retries
  } finally {
    _inFlight = false;
  }
};

const _onVisible = (): void => {
  if (typeof document !== "undefined" && !document.hidden) void _poll(true);
};

const _start = (): void => {
  if (_timer !== null) return;
  void _poll(true);
  _timer = window.setInterval(() => void _poll(), POLL_MS);
  if (typeof document !== "undefined") {
    document.addEventListener("visibilitychange", _onVisible);
  }
};

const _stop = (): void => {
  if (_timer === null) return;
  window.clearInterval(_timer);
  _timer = null;
  if (typeof document !== "undefined") {
    document.removeEventListener("visibilitychange", _onVisible);
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
