/**
 * Fold a fetch outcome into the AG-UI-shaped messages the design calls for
 * (finance-components.md §3.7): a `STATE_DELTA` (JSON Patch ops against the
 * slot path) per poll, and a `STATE_SNAPSHOT` for initial load / reconnect.
 *
 * See `index.ts` for why this module is destined for OSS. `FetchResult` is
 * this module's half of the edition boundary: any edition's data client can
 * hand this module a result shaped `{ok:true,data}` /
 * `{ok:false,error,notConnected?}` — this file never imports an edition's
 * own result type, it only requires that shape structurally.
 */

export type SlotState = "filled" | "empty" | "error" | "stale";

export interface SlotValue<T = unknown> {
  state: SlotState;
  /**
   * The mapped data (already carrying its own provenance, wherever the
   * edition's mapper attaches that). `null` for empty, or for error/stale
   * with no prior good value to carry forward.
   */
  data: T | null;
  /** Present for error/stale/empty-with-missing-param; absent for a normal empty (well-formed response with nothing to draw) or filled. */
  reason?: string;
}

/** The minimal fetch-outcome shape this module folds — structurally compatible with any edition's own `DataResult<T>`. */
export type FetchResult<T> =
  { ok: true; data: T } | { ok: false; error: string; notConnected?: boolean };

export type JsonPatchOp =
  | { op: "add"; path: string; value: unknown }
  | { op: "replace"; path: string; value: unknown }
  | { op: "remove"; path: string };

export interface StateDeltaMessage {
  type: "STATE_DELTA";
  delta: JsonPatchOp[];
}

export interface StateSnapshotMessage {
  type: "STATE_SNAPSHOT";
  snapshot: Record<string, SlotValue>;
}

/**
 * Classify a fetch outcome into the state blocks render. 424
 * (`notConnected`) is modeled as `stale`, not `error`: a disconnected
 * connector is a recoverable, user-actionable condition ("connect the
 * source") distinct from the data source misbehaving, and the block should
 * keep showing its last good value with a stale badge rather than swap to
 * an error card. Any other failure carries the prior value forward too —
 * a transient poll failure shouldn't blank a card that has real data.
 */
export function classifyOutcome<T>(
  result: FetchResult<T | null>,
  previous?: SlotValue<T>,
): SlotValue<T> {
  if (!result.ok) {
    const carried = previous?.state === "filled" ? previous.data : null;
    return result.notConnected
      ? { state: "stale", data: carried, reason: result.error }
      : { state: "error", data: carried, reason: result.error };
  }
  if (result.data === null || result.data === undefined) {
    return { state: "empty", data: null };
  }
  return { state: "filled", data: result.data };
}

/**
 * The empty-with-reason outcome for a ref whose params didn't resolve
 * (§3.4) — constructed without ever calling a data source, since there is
 * nothing valid to fetch yet.
 */
export function emptyForMissingParams(reason: string): SlotValue<never> {
  return { state: "empty", data: null, reason };
}

/** Fold one fetch outcome into a STATE_DELTA patch against its slot path. */
export function foldToDelta<T>(
  path: string,
  result: FetchResult<T | null>,
  previous?: SlotValue<T>,
): { value: SlotValue<T>; message: StateDeltaMessage } {
  const value = classifyOutcome(result, previous);
  return {
    value,
    message: { type: "STATE_DELTA", delta: [{ op: "replace", path, value }] },
  };
}

/**
 * Build the full-state STATE_SNAPSHOT for initial load / reconnect (§3.7):
 * every open slot's current value, keyed by its DataModel path.
 */
export function buildSnapshot(
  entries: Array<{ path: string; value: SlotValue }>,
): StateSnapshotMessage {
  const snapshot: Record<string, SlotValue> = {};
  for (const entry of entries) snapshot[entry.path] = entry.value;
  return { type: "STATE_SNAPSHOT", snapshot };
}
