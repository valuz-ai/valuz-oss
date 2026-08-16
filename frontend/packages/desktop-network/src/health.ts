import type {
  EgressConnectionOutcome,
  EgressSnapshot,
} from "./types";

export const DEFAULT_CONNECT_DEGRADED_MS = 5_000;

export interface EgressHealthOptions {
  connectDegradedMs?: number;
  now?: () => number;
}

/**
 * Apply one real connection outcome to a runtime+origin snapshot.
 *
 * Resolution alone never makes a route healthy: only a successful stream
 * establishment does. A fallback, reconnect, or over-budget connection is
 * degraded even when it eventually succeeds. Candidate exhaustion is failed.
 */
export const applyConnectionOutcome = (
  snapshot: EgressSnapshot,
  outcome: EgressConnectionOutcome,
  options: EgressHealthOptions = {},
): EgressSnapshot => {
  const connectDegradedMs =
    options.connectDegradedMs ?? DEFAULT_CONNECT_DEGRADED_MS;
  const fallbackCount =
    snapshot.fallbackCount + Math.max(0, outcome.fallbackCount ?? 0);
  const reconnectCount =
    snapshot.reconnectCount + Math.max(0, outcome.reconnectCount ?? 0);
  const connectMs = outcome.connectMs;

  if (!outcome.success) {
    return {
      ...snapshot,
      health: "failed",
      connectMs,
      fallbackCount,
      reconnectCount,
      lastErrorCode: outcome.errorCode ?? "egress_connect_failed",
      updatedAt: options.now?.() ?? Date.now(),
    };
  }

  const degraded =
    fallbackCount > 0 ||
    reconnectCount > 0 ||
    (connectMs !== undefined && connectMs > connectDegradedMs);
  return {
    ...snapshot,
    health: degraded ? "degraded" : "healthy",
    connectMs,
    fallbackCount,
    reconnectCount,
    lastErrorCode: undefined,
    updatedAt: options.now?.() ?? Date.now(),
  };
};
