/**
 * Activity pinning comparator — the SINGLE sort rule shared by the menu
 * (recent list / sidebar Recents) and Activity (PRD "置顶排序", plan §4.3). Two
 * data sources (`RunSummary` and `AutomationItem`) are each normalized to a
 * `{ isRunning, activeTs, id }` key, then ordered identically so every surface
 * agrees on what's pinned and in what order.
 *
 * Order:
 *   1. `isRunning` desc — running entries pin to the top as one group.
 *   2. `activeTs` desc — within a group, most-recently-active first.
 *   3. `id` asc — stable tie-breaker when timestamps collide.
 */

import type { AutomationItem } from "./automations-api";
import type { RunSummary } from "./runs-api";

export interface ActivitySortKey {
  isRunning: boolean;
  /** Active timestamp (ms) used for the in-group recency ordering. */
  activeTs: number;
  /** Stable identity for the tie-breaker (session_id / automation_id). */
  id: string;
}

/** Compare two normalized keys: running first, then recency, then id. */
export function compareActivityEntries(
  a: ActivitySortKey,
  b: ActivitySortKey,
): number {
  if (a.isRunning !== b.isRunning) return a.isRunning ? -1 : 1;
  if (a.activeTs !== b.activeTs) return b.activeTs - a.activeTs;
  return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
}

/**
 * Normalize an `AutomationItem` (menu/sidebar/Activity "automation" tab). Run
 * state is the server-side `is_running` projection; activity timestamp follows
 * the PRD gradient `last_run_at ?? next_run_at ?? created_at`.
 */
export function automationItemSortKey(item: AutomationItem): ActivitySortKey {
  return {
    isRunning: item.is_running,
    activeTs: item.last_run_at ?? item.next_run_at ?? item.created_at,
    id: item.automation_id,
  };
}

/**
 * Normalize a `RunSummary` (cross-type Running group / sidebar liveRuns).
 * `isRunning` is supplied by the caller (membership in the running pool); the
 * activity timestamp is `updated_at` (consistent with the history bucketing).
 */
export function runSummarySortKey(
  run: RunSummary,
  isRunning: boolean,
): ActivitySortKey {
  return { isRunning, activeTs: run.updated_at, id: run.session_id };
}
