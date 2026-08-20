/**
 * Multi-target list fan-out.
 *
 * List surfaces (projects / sessions / runs / activity) must show BOTH
 * backends' rows on a multi-target edition, each row tagged with the target
 * that answered (``exec_origin`` — a client-side field, never a server
 * column; distinct from the server-side ``origin`` initiator field on
 * sessions/runs). OSS registers no targets → ``getListFanOutTargets()`` is
 * empty and every list keeps its single-backend path unchanged.
 *
 * Degraded mode: when one target's fetch fails, the merged list shows the
 * other target's rows and the failing target id is published through
 * ``useDegradedListTargets`` so shells can render a "list may be incomplete"
 * hint. Only when EVERY target fails does the fan-out throw.
 *
 * A target that never settles counts as failed too: browser fetch has no
 * default timeout, so a black-holed backend (connection accepted, response
 * never sent — e.g. an OOM-wedged cloud deployment) would otherwise hold
 * ``Promise.allSettled`` open forever and pin every list surface on
 * "loading" even though the healthy target answered in milliseconds. Each
 * target therefore races ``LIST_TARGET_TIMEOUT_MS``; on timeout the target's
 * ``AbortSignal`` fires (so the underlying request is torn down rather than
 * leaking a hung connection per poll tick) and the target goes down the same
 * degraded path as a rejection.
 *
 * Recovery is active, not just passive: while any target is degraded, the
 * store re-probes the failed targets every ``DEGRADED_REPROBE_MS`` by
 * replaying the most recent fan-out's ``fetchOne`` against them. A hint that
 * only failed requests could set and only successful requests could clear
 * would otherwise linger forever on quiet pages (e.g. a conversation left
 * open) where no list refresh happens to run after the outage ends.
 */

import { useSyncExternalStore } from "react";
import {
  getExecutionTargets,
  selectableExecutionTargets,
  type ExecutionTarget,
} from "./execution-targets";

/** Targets to fan a list call out to; [] = single-backend fast path. */
export function getListFanOutTargets(): ExecutionTarget[] {
  // Unselectable targets are narrow grants (see ExecutionTarget.selectable):
  // they answer for the entities the edition already holds, not for "list
  // everything you have". Fanning out to one only ever yields a refusal, and
  // a refusal here is what raises the "list may be incomplete" banner.
  const targets = selectableExecutionTargets(getExecutionTargets());
  return targets.length >= 2 ? targets : [];
}

export interface FanOutOutcome<T> {
  /** Fulfilled per-target values, in registration order. */
  values: Array<{ target: ExecutionTarget; value: T }>;
  /** Ids of targets whose fetch rejected. */
  failedTargets: string[];
}

/** Per-target budget before a hung list fetch counts as a failed target. */
export const LIST_TARGET_TIMEOUT_MS = 10_000;

/** Re-probe cadence for degraded targets (banner auto-clears on recovery). */
export const DEGRADED_REPROBE_MS = 30_000;

type FanOutFetch<T> = (
  target: ExecutionTarget,
  signal?: AbortSignal,
) => Promise<T>;

/**
 * Reject after ``LIST_TARGET_TIMEOUT_MS`` when ``promise`` hasn't settled,
 * invoking ``onTimeout`` first (used to abort the underlying request). The
 * late settlement of the losing promise stays observed (routed into the
 * already-settled deferred, a no-op) so it can't surface as an unhandled
 * rejection.
 */
function withTargetTimeout<T>(
  promise: Promise<T>,
  targetId: string,
  onTimeout?: () => void,
): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => {
      onTimeout?.();
      reject(
        new Error(
          `list target '${targetId}' timed out after ${LIST_TARGET_TIMEOUT_MS}ms`,
        ),
      );
    }, LIST_TARGET_TIMEOUT_MS);
    promise.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (err) => {
        clearTimeout(timer);
        reject(err);
      },
    );
  });
}

/** Run one target's fetch under the timeout + abort discipline. */
function fetchTarget<T>(
  fetchOne: FanOutFetch<T>,
  target: ExecutionTarget,
): Promise<T> {
  const controller = new AbortController();
  return withTargetTimeout(fetchOne(target, controller.signal), target.id, () =>
    controller.abort(),
  );
}

/**
 * Run ``fetchOne`` against every registered target concurrently. Publishes
 * failures to the degraded-targets store. Throws (the first rejection) only
 * when no target answered. A target that neither resolves nor rejects within
 * ``LIST_TARGET_TIMEOUT_MS`` is aborted and treated as failed.
 *
 * ``fetchOne`` SHOULD forward the provided ``AbortSignal`` into its request
 * so a timed-out target actually tears down the connection; callers that
 * ignore it still get the timeout, just without the abort.
 */
export async function fanOutTargets<T>(
  fetchOne: FanOutFetch<T>,
): Promise<FanOutOutcome<T>> {
  const targets = getListFanOutTargets();
  if (targets.length > 0) {
    // Remembered so the degraded re-probe can replay the same request shape
    // (auth, params) against the failed targets. GET-only surfaces, so a
    // replay is side-effect free.
    _lastFanOut = fetchOne as FanOutFetch<unknown>;
  }
  const settled = await Promise.allSettled(
    targets.map((target) => fetchTarget(fetchOne, target)),
  );
  const values: Array<{ target: ExecutionTarget; value: T }> = [];
  const failedTargets: string[] = [];
  let firstError: unknown;
  settled.forEach((result, i) => {
    if (result.status === "fulfilled") {
      values.push({ target: targets[i]!, value: result.value });
    } else {
      failedTargets.push(targets[i]!.id);
      firstError ??= result.reason;
    }
  });
  publishDegradedTargets(failedTargets);
  if (values.length === 0 && failedTargets.length > 0) {
    throw firstError;
  }
  return { values, failedTargets };
}

// --- degraded-targets store (shells render a "list incomplete" hint) -------

let _degraded: string[] = [];
const _listeners = new Set<() => void>();
let _lastFanOut: FanOutFetch<unknown> | null = null;
let _reprobeTimer: ReturnType<typeof setTimeout> | null = null;

function publishDegradedTargets(failed: string[]): void {
  const next = [...failed].sort();
  const changed = next.join(",") !== _degraded.join(",");
  _degraded = next;
  if (_degraded.length === 0) {
    if (_reprobeTimer !== null) {
      clearTimeout(_reprobeTimer);
      _reprobeTimer = null;
    }
  } else {
    scheduleReprobe();
  }
  if (!changed) return;
  for (const fn of _listeners) fn();
}

function scheduleReprobe(): void {
  if (_reprobeTimer !== null) return;
  _reprobeTimer = setTimeout(() => {
    _reprobeTimer = null;
    void runReprobe();
  }, DEGRADED_REPROBE_MS);
}

/**
 * Replay the last fan-out's fetch against only the degraded targets; targets
 * that answer are removed from the degraded set (real list content follows
 * with the next regular refresh). Still-failed targets keep the banner and
 * the next probe stays scheduled.
 */
async function runReprobe(): Promise<void> {
  const fetchOne = _lastFanOut;
  const failed = getListFanOutTargets().filter((t) => _degraded.includes(t.id));
  if (!fetchOne || failed.length === 0) return;
  const settled = await Promise.allSettled(
    failed.map((target) => fetchTarget(fetchOne, target)),
  );
  const recovered = new Set(
    failed
      .filter((_, i) => settled[i]!.status === "fulfilled")
      .map((t) => t.id),
  );
  publishDegradedTargets(_degraded.filter((id) => !recovered.has(id)));
}

function subscribe(fn: () => void): () => void {
  _listeners.add(fn);
  return () => _listeners.delete(fn);
}

/** Target ids whose most recent list fan-out failed ([] when healthy). */
export function useDegradedListTargets(): string[] {
  return useSyncExternalStore(subscribe, () => _degraded);
}
