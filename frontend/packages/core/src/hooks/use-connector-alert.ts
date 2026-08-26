/**
 * Connector "needs attention" alert, shared across consumers.
 *
 * Drives the small red dot on the Connectors nav item: it shows when a custom
 * connector is configured but not connected — it either failed to connect
 * (status === "error") or still needs authorization (status === "pending_auth",
 * e.g. an OAuth connector like Valuz·搜索 sitting at a 401) — and the user
 * hasn't acknowledged it yet. Visiting the Connectors page acknowledges
 * the currently-failing set ({@link acknowledgeConnectorAlert}) so the dot
 * clears. Acknowledgement is in-memory only and is wiped whenever the user
 * re-enters the home screen ({@link refreshConnectorAlert}) or restarts the
 * app — so each return to home re-surfaces the dot for anything still
 * unconnected. A NEWLY failing connector (a fresh id) isn't acknowledged, so
 * the dot reappears for it too, even between those resets.
 *
 * A single module-level poller backs every mount (the nav badge), so we never
 * open N intervals — the shared-singleton pattern also used by
 * {@link useRunningRuns} (which is stream-driven rather than interval-polled).
 */

import { useEffect, useState } from "react";

import { connectorsApi } from "../api/connectors-api";
import type { ConnectorItem } from "@valuz/shared";

// Connector status changes are rare (connect / fail / self-heal) and never
// urgent for the nav dot, so a lazy 5-minute cadence is plenty — a forced poll
// on mount / focus still repaints promptly when the user actually looks.
const POLL_MS = 300000;

let _connectors: ConnectorItem[] = [];
const _acknowledged = new Set<string>();
const _subscribers = new Set<() => void>();
let _timer: number | null = null;
let _inFlight = false;

/** A custom connector that has settings but isn't connected — the "配置了但没连上"
 * signal. Two terminal "not connected" states qualify: ``"error"`` (the backend
 * tried and the connection failed) and ``"pending_auth"`` (an OAuth connector
 * still waiting to be authorized, e.g. a 401). Transient/intentional states do
 * NOT —  "connecting" (in flight), "connected" (fine), "disabled" (user turned
 * it off), and "unknown" (enabled but never probed). */
const ATTENTION_STATUSES = new Set(["error", "pending_auth"]);
const needsAttention = (c: ConnectorItem): boolean =>
  c.connector_type === "custom" && ATTENTION_STATUSES.has(c.status);

const failingIds = (): string[] =>
  _connectors.filter(needsAttention).map((c) => c.id);

const _notify = (): void => {
  _subscribers.forEach((fn) => fn());
};

const _poll = async (force = false): Promise<void> => {
  if (_inFlight) return;
  // Pause recurring ticks while backgrounded; a forced poll (mount / focus)
  // always runs so a freshly-opened window still paints.
  if (!force && typeof document !== "undefined" && document.hidden) return;
  _inFlight = true;
  try {
    const res = await connectorsApi.list();
    _connectors = res.connectors ?? [];
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

/**
 * Mark every currently-failing custom connector as seen, clearing the dot.
 * Call this when the user opens the Connectors page. A connector that starts
 * failing later (new id) isn't in the set, so its dot will still show.
 *
 * Pass the page's freshly-loaded ``connectors`` to acknowledge against the
 * latest truth (the module poller may be up to {@link POLL_MS} stale);
 * defaults to the poller's last snapshot.
 */
export const acknowledgeConnectorAlert = (
  connectors?: ConnectorItem[],
): void => {
  const ids = (connectors ?? _connectors)
    .filter(needsAttention)
    .map((c) => c.id);
  let changed = false;
  for (const id of ids) {
    if (!_acknowledged.has(id)) {
      _acknowledged.add(id);
      changed = true;
    }
  }
  if (changed) _notify();
};

/**
 * Re-check connectors for the nav dot and re-surface it. Called when the user
 * lands on the home screen: it (1) clears the in-memory acknowledgement so a
 * still-unconnected connector the user dismissed earlier shows the dot again,
 * and (2) forces an immediate re-poll, independent of the lazy {@link POLL_MS}
 * interval, so a status that changed during the gap shows up at once. Visiting
 * the Connectors page re-acknowledges and clears the dot until the next home
 * entry / restart.
 */
export const refreshConnectorAlert = (): void => {
  if (_acknowledged.size > 0) {
    // Re-surface immediately from the last snapshot, then refresh from the
    // backend — the dot shouldn't wait on the network round-trip.
    _acknowledged.clear();
    _notify();
  }
  void _poll(true);
};

export interface ConnectorAlertResult {
  /** True when ≥1 custom connector is failing and not yet acknowledged. */
  showDot: boolean;
}

export const useConnectorAlert = (): ConnectorAlertResult => {
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
  const showDot = failingIds().some((id) => !_acknowledged.has(id));
  return { showDot };
};
