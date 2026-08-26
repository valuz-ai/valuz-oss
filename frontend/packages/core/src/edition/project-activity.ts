/**
 * Project-scoped activity source override.
 *
 * A project feed normally lives on the project's own backend
 * (``use-activity-feed`` routes ``GET /v1/activity`` there via the entity
 * resolver). Some edition-injected projects have no backend that can answer
 * that call — e.g. a project reached through a narrow proxy grant where the
 * activity route is not part of the allowed verb table, while the edition's
 * control plane DOES know the conversations that matter to this user.
 *
 * The edition registers one source; for each project feed fetch the hook asks
 * it first. Returning ``null`` (or having no source) keeps the stock
 * single-backend path — OSS behaviour is unchanged. Returning a promise makes
 * that promise the page: the source owns filtering by tab, pagination via
 * ``cursor``/``next_cursor``, and any ``exec_origin`` tagging its rows need
 * (the hook does not re-tag them).
 */

import type { ActivityItem, ActivityTab } from "../api/activity-api";

export interface ProjectActivityPage {
  items: ActivityItem[];
  next_cursor: string | null;
}

export interface ProjectActivityQuery {
  projectId: string;
  tab: ActivityTab;
  limit: number;
  /** ``null`` for the head page; otherwise the source's own ``next_cursor``. */
  cursor: string | null;
}

export type ProjectActivitySource = (
  query: ProjectActivityQuery,
) => Promise<ProjectActivityPage> | null;

let _source: ProjectActivitySource | null = null;

/** Register (or clear with ``null``) the edition's project activity source. */
export function setProjectActivitySource(
  source: ProjectActivitySource | null,
): void {
  _source = source;
}

/** The page promise for this query, or ``null`` for the stock path. */
export function resolveProjectActivity(
  query: ProjectActivityQuery,
): Promise<ProjectActivityPage> | null {
  return _source ? _source(query) : null;
}
