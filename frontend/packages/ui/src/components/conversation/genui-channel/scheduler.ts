/**
 * The polling scheduler (finance-components.md §3.5): dedups by slot path
 * (same source+params share ONE poll), floors a ref's requested
 * `refresh.interval` against the source's registered `minIntervalSec`,
 * pauses while the page is hidden, backs off on consecutive failures, and
 * stops polling a slot outright on a 424.
 *
 * See `index.ts` for why this module is destined for OSS. The edition
 * boundary is exactly the two seams below:
 *
 *   - `SourceRegistryLookup` — "what do we know about this source" (its TTL
 *     and its minimum poll interval). The edition's own source registry
 *     (proxy paths, verification status, notes, …) satisfies this with a
 *     thin projection; none of that edition detail crosses into this file.
 *   - `SlotFetcher` — "go get this source+params". The edition's data
 *     client (auth, base URL, proxy path resolution, response mapping)
 *     satisfies this; this file never imports a fetch client of its own.
 *
 * Everything else (dedup, floor enforcement, visibility pause, backoff,
 * 424-stop) is pure scheduling logic that has nothing to do with finance.
 */

import type { ResolvedParams } from "./dataRef";
import { type FetchResult, type SlotValue, classifyOutcome } from "./patch";
import { slotPath } from "./slots";

export interface SchedulerClock {
  now(): number;
}

export interface VisibilitySource {
  isVisible(): boolean;
}

export interface BackoffPolicy {
  baseMs: number;
  multiplier: number;
  maxMs: number;
}

const DEFAULT_BACKOFF: BackoffPolicy = {
  baseMs: 5_000,
  multiplier: 2,
  maxMs: 5 * 60_000,
};

/** What the scheduler needs to know about a source — the edition-boundary registry lookup. */
export interface SourceMeta {
  ttlMs: number;
  minIntervalSec: number;
}

export type SourceRegistryLookup = (sourceId: string) => SourceMeta | undefined;

/** Go fetch one source+params(+shape) — the edition-boundary fetcher. */
export type SlotFetcher = (
  sourceId: string,
  params: ResolvedParams,
  shape?: string,
) => Promise<FetchResult<unknown>>;

export interface RegisterRefInput {
  /** Unique per data-ref registration (one per component/binding instance, not per slot — several refs commonly share a slot). */
  refId: string;
  source: string;
  /** Already-resolved params (dataRef.resolveParams output) — the scheduler never resolves `$host`/`$state` itself, so a caller can't accidentally schedule a fetch for an unresolved ref. */
  params: ResolvedParams;
  /** Component-declared `refresh.interval` (seconds), if any; absent = poll at the source's TTL. */
  refreshIntervalSec?: number;
  /** Canonical shape for a multi-shape source (dataRef.shape, already validated by the edition); part of the slot identity. */
  shape?: string;
}

export interface SchedulerDeps {
  clock: SchedulerClock;
  visibility: VisibilitySource;
  sourceRegistry: SourceRegistryLookup;
  fetchSlot: SlotFetcher;
  /** Called once per settled poll with the classified value and its slot path — the caller turns this into a STATE_DELTA via `patch.foldToDelta`/`classifyOutcome` output and forwards it to the DataModel. */
  onOutcome?: (path: string, value: SlotValue) => void;
  backoff?: Partial<BackoffPolicy>;
}

interface SlotEntry {
  source: string;
  params: ResolvedParams;
  shape?: string;
  refIds: Set<string>;
  intervalMs: number;
  nextPollAtMs: number;
  inFlight: boolean;
  /** Terminal — either a 424 or an unregistered source; no further ticks poll this slot. */
  stopped: boolean;
  consecutiveFailures: number;
  lastValue?: SlotValue;
}

/**
 * The minInterval floor is enforced here against the *source's* registered
 * value, never against whatever a ref asks for — `minIntervalSec` models a
 * shared upstream rate budget (one source, many consumers), so a single
 * misconfigured or model-authored `refresh.interval` must not be able to
 * exceed it even by accident. Absent an explicit `refresh.interval`, the
 * source's own TTL is the default cadence — polling roughly as often as the
 * data would go stale anyway.
 */
function effectiveIntervalMs(
  meta: SourceMeta,
  requestedSec: number | undefined,
): number {
  const requestedMs = (requestedSec ?? meta.ttlMs / 1000) * 1000;
  return Math.max(requestedMs, meta.minIntervalSec * 1000);
}

export interface Scheduler {
  /** Register (or join) a data ref's slot; returns the slot's DataModel path. */
  registerRef(input: RegisterRefInput): string;
  unregisterRef(refId: string): void;
  /**
   * Poll every due, visible, non-stopped slot; resolves once this tick's
   * fetches have all settled. Takes no time argument on purpose — "now"
   * always comes from `deps.clock.now()`, so a test drives the schedule
   * entirely by mutating its fake clock between calls, never by waiting on
   * a real timer.
   */
  tick(): Promise<void>;
  getSlotState(path: string): SlotValue | undefined;
}

export function createScheduler(deps: SchedulerDeps): Scheduler {
  const backoff: BackoffPolicy = { ...DEFAULT_BACKOFF, ...deps.backoff };
  const slots = new Map<string, SlotEntry>();
  const refToSlot = new Map<string, string>();

  function registerRef(input: RegisterRefInput): string {
    const path = slotPath(input.source, input.params, input.shape);
    const meta = deps.sourceRegistry(input.source);
    let entry = slots.get(path);

    if (!entry) {
      const unknownReason = meta
        ? undefined
        : `unknown data source "${input.source}"`;
      entry = {
        source: input.source,
        params: input.params,
        shape: input.shape,
        refIds: new Set(),
        intervalMs: meta
          ? effectiveIntervalMs(meta, input.refreshIntervalSec)
          : 0,
        // Due immediately on the first tick, so the initial STATE_SNAPSHOT
        // fill doesn't wait out a full interval.
        nextPollAtMs: 0,
        inFlight: false,
        stopped: unknownReason !== undefined,
        consecutiveFailures: 0,
        lastValue: unknownReason
          ? { state: "stale", data: null, reason: unknownReason }
          : undefined,
      };
      slots.set(path, entry);
      if (unknownReason) deps.onOutcome?.(path, entry.lastValue!);
    } else if (meta) {
      // Fastest requested cadence among refs sharing this slot wins — each
      // ref's own interval is already floored individually, so the minimum
      // across them is still never below the source's floor.
      entry.intervalMs = Math.min(
        entry.intervalMs,
        effectiveIntervalMs(meta, input.refreshIntervalSec),
      );
    }

    entry.refIds.add(input.refId);
    refToSlot.set(input.refId, path);
    return path;
  }

  function unregisterRef(refId: string): void {
    const path = refToSlot.get(refId);
    if (!path) return;
    refToSlot.delete(refId);
    const entry = slots.get(path);
    if (!entry) return;
    entry.refIds.delete(refId);
    if (entry.refIds.size === 0) slots.delete(path);
  }

  async function poll(
    path: string,
    entry: SlotEntry,
    nowMs: number,
  ): Promise<void> {
    entry.inFlight = true;
    const result = await deps.fetchSlot(
      entry.source,
      entry.params,
      entry.shape,
    );
    entry.inFlight = false;

    const value = classifyOutcome(result, entry.lastValue);
    entry.lastValue = value;
    deps.onOutcome?.(path, value);

    if (!result.ok && result.notConnected) {
      // 424: the connector isn't connected — polling again won't fix that
      // until the user reconnects, so stop rather than backing off forever.
      entry.stopped = true;
      return;
    }
    if (!result.ok) {
      entry.consecutiveFailures += 1;
      const delay = Math.min(
        backoff.baseMs *
          Math.pow(backoff.multiplier, entry.consecutiveFailures - 1),
        backoff.maxMs,
      );
      entry.nextPollAtMs = nowMs + delay;
      return;
    }
    entry.consecutiveFailures = 0;
    entry.nextPollAtMs = nowMs + entry.intervalMs;
  }

  async function tick(): Promise<void> {
    if (!deps.visibility.isVisible()) return;
    const nowMs = deps.clock.now();
    const due = Array.from(slots.entries()).filter(
      ([, entry]) =>
        !entry.stopped && !entry.inFlight && nowMs >= entry.nextPollAtMs,
    );
    await Promise.all(due.map(([path, entry]) => poll(path, entry, nowMs)));
  }

  function getSlotState(path: string): SlotValue | undefined {
    return slots.get(path)?.lastValue;
  }

  return { registerRef, unregisterRef, tick, getSlotState };
}

// ── Thin real-environment factories — the only place window/document/Date
// are allowed to leak in; everything above takes them as parameters. ──────

export function systemClock(): SchedulerClock {
  return { now: () => Date.now() };
}

export function documentVisibilitySource(): VisibilitySource {
  return {
    isVisible: () =>
      typeof document === "undefined" ||
      document.visibilityState !== "hidden" ||
      // Electron can keep reporting `hidden` for its only BrowserWindow even
      // after the window is active and its document owns focus. Treat focus as
      // the stronger signal there; a genuinely background browser tab does
      // not have document focus, so it still pauses polling.
      document.hasFocus(),
  };
}
