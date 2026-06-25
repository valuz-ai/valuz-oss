/**
 * Automation run-state — the single source of truth shared by the detail page,
 * the menu, and Activity (PRD "运行态判定" table). Extracted out of
 * `AutomationPage` so all three surfaces compute run state identically; the
 * backend `AutomationItem.is_running` is the server-side projection of the same
 * rule, and a unit test pins both to this table.
 *
 * Lives in `@valuz/core` (depends on `shared` only), so the `ExecutionLog`
 * status union from `@valuz/ui` can't be imported here — it's redeclared as the
 * structurally-identical `LogStatus`. Callers assign it to
 * `ExecutionLogRow["status"]` directly (same string literals).
 */

import type { AutomationRunItem } from "./automations-api";

/** Mirror of `@valuz/ui` `ExecutionLogRow["status"]` (same literals). */
export type LogStatus = "ok" | "err" | "pending" | "skip";

/** Map a raw run status to its execution-log badge tone. */
export function runStatusToLogStatus(
  status: AutomationRunItem["status"],
): LogStatus {
  if (status === "success") return "ok";
  if (status === "failed") return "err";
  if (status === "queued" || status === "running") return "pending";
  // skipped / interrupted_by_shutdown → neutral.
  return "skip";
}

/**
 * Badge status for a run row. For a task automation the run row freezes to
 * `success` the instant kickoff returns, so prefer the live `task_status`
 * (resolved server-side) — `active` / `paused` are still in flight.
 */
export function runToLogStatus(run: AutomationRunItem): LogStatus {
  if (run.task_status) {
    if (run.task_status === "completed") return "ok";
    if (run.task_status === "failed") return "err";
    return "pending"; // active / paused → still running
  }
  return runStatusToLogStatus(run.status);
}

/**
 * Is the automation running right now? Decided from its latest run (PRD table):
 *   1. latest run `status ∈ {queued, running}` → running (run.status wins).
 *   2. else, for a task automation, the live `task_status === "active"` →
 *      running (the run row already settled to `success` at kickoff).
 *   3. else not running (`success+paused` = paused, `failed`/`skipped`/
 *      `interrupted_by_shutdown` = their own terminal states).
 *
 * `null` / `undefined` (never run) → not running. Same口径 as the backend
 * `_compute_is_running`.
 */
export function isAutomationRunning(
  latestRun: AutomationRunItem | null | undefined,
): boolean {
  if (!latestRun) return false;
  if (latestRun.status === "queued" || latestRun.status === "running") {
    return true;
  }
  return latestRun.task_status === "active";
}
