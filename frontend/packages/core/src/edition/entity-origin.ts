/**
 * Entity origin observation seam — which execution target an entity
 * (session / project / task) was observed on.
 *
 * OSS is single-backend and registers no adapter: every function is a no-op /
 * ``undefined`` and origin-aware UI (creation selectors, origin badges)
 * renders nothing. A multi-target edition registers an adapter at boot that
 * persists observations (create responses, list fan-out answer sources) and
 * resolves cache misses (deep-link probes).
 *
 * origin is a CLIENT OBSERVATION — it is never a server-side column. The
 * adapter's lookup may kick off an async probe on miss; it signals completion
 * through {@link notifyEntityOriginsChanged} so ``useEntityOrigin`` consumers
 * re-render.
 */

import { useSyncExternalStore } from "react";

export type EntityOriginKind =
  | "session"
  | "project"
  | "task"
  | "automation"
  | "playbook"
  | "kb";

export interface EntityOriginAdapter {
  /** Synchronous lookup. May trigger an async fill on miss (then notify). */
  lookup: (id: string, kind?: EntityOriginKind) => string | undefined;
  /** Persist an observation (e.g. a create response's chosen target). */
  record: (id: string, targetId: string) => void;
  /** Batch persist (list fan-out tags whole pages) — one write, one notify. */
  recordMany?: (entries: Array<[string, string]>) => void;
}

let _adapter: EntityOriginAdapter | null = null;
let _version = 0;
const _listeners = new Set<() => void>();

/** Register (or clear with ``null``) the edition's origin adapter. */
export function setEntityOriginAdapter(
  adapter: EntityOriginAdapter | null,
): void {
  _adapter = adapter;
  notifyEntityOriginsChanged();
}

/** Bump subscribers (async probe finished, index hydrated, …). */
export function notifyEntityOriginsChanged(): void {
  _version++;
  for (const fn of _listeners) fn();
}

/** Record where an entity lives (called right after a targeted create). */
export function recordEntityOrigin(id: string, targetId: string): void {
  _adapter?.record(id, targetId);
  notifyEntityOriginsChanged();
}

/** Batch form of {@link recordEntityOrigin} for list fan-out pages. */
export function recordEntityOrigins(entries: Array<[string, string]>): void {
  if (!_adapter || entries.length === 0) return;
  if (_adapter.recordMany) {
    _adapter.recordMany(entries);
  } else {
    for (const [id, targetId] of entries) _adapter.record(id, targetId);
  }
  notifyEntityOriginsChanged();
}

/** Observed origin target id, or ``undefined`` (unknown / single-target). */
export function getEntityOrigin(
  id: string,
  kind?: EntityOriginKind,
): string | undefined {
  return _adapter?.lookup(id, kind);
}

function subscribe(fn: () => void): () => void {
  _listeners.add(fn);
  return () => _listeners.delete(fn);
}

/**
 * Reactive origin for badges. Passing ``kind`` lets the adapter probe on
 * miss; the badge appears when the probe lands.
 */
export function useEntityOrigin(
  id: string | null | undefined,
  kind?: EntityOriginKind,
): string | undefined {
  return useSyncExternalStore(subscribe, () => {
    // _version is captured so the snapshot changes whenever an observation
    // lands, forcing getSnapshot to re-run lookups.
    void _version;
    return id ? getEntityOrigin(id, kind) : undefined;
  });
}
