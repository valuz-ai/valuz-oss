/**
 * Which projects need their sidebar run window re-read.
 *
 * Split out of ``ProjectLayoutBase`` so the decision is testable on its own:
 * the component around it renders half the shell, but this is the part that
 * decides how many requests go out — one per project returned, times the
 * number of execution targets.
 */

/** ``session_id`` → ``project_id`` for the runs currently showing as running. */
export type LiveRunProjects = ReadonlyMap<string, string>;

/**
 * Projects that owned a run in ``previous`` which is no longer running.
 *
 * A run leaving the running pool is the only thing that invalidates a
 * project's finished window, and it invalidates exactly one project. Keying a
 * refetch on the running set as a whole — the shape this replaced — re-read
 * every project each time any run anywhere started or finished.
 *
 * Projects not in ``known`` are dropped: the sidebar has no row to fill for a
 * project this client is not showing (another target's, or one just deleted).
 */
export function projectsWithEndedRuns(
  previous: LiveRunProjects,
  current: LiveRunProjects,
  known: ReadonlySet<string>,
): string[] {
  const ended = new Set<string>();
  for (const [sessionId, projectId] of previous) {
    if (current.has(sessionId)) continue;
    if (!known.has(projectId)) continue;
    ended.add(projectId);
  }
  return [...ended];
}
