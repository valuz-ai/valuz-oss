import { describe, expect, it } from "vitest";

import { projectsWithEndedRuns } from "./project-runs-refresh";

const live = (...pairs: Array<[string, string]>) => new Map(pairs);
const known = (...ids: string[]) => new Set(ids);

describe("projectsWithEndedRuns", () => {
  it("names only the project whose run ended", () => {
    // The shape this replaced re-read every project on any transition.
    expect(
      projectsWithEndedRuns(
        live(["s1", "p1"], ["s2", "p2"]),
        live(["s2", "p2"]),
        known("p1", "p2", "p3", "p4"),
      ),
    ).toEqual(["p1"]);
  });

  it("says nothing when a run merely STARTS", () => {
    expect(
      projectsWithEndedRuns(
        live(["s1", "p1"]),
        live(["s1", "p1"], ["s2", "p2"]),
        known("p1", "p2"),
      ),
    ).toEqual([]);
  });

  it("says nothing when the running set is unchanged", () => {
    const same = live(["s1", "p1"]);
    expect(projectsWithEndedRuns(same, same, known("p1"))).toEqual([]);
  });

  it("collapses two runs of one project into one refresh", () => {
    expect(
      projectsWithEndedRuns(
        live(["s1", "p1"], ["s2", "p1"]),
        live(),
        known("p1"),
      ),
    ).toEqual(["p1"]);
  });

  it("drops a project the sidebar is not showing", () => {
    // Another target's project, or one deleted while its run was in flight —
    // there is no row to fill, so no request should go out for it.
    expect(
      projectsWithEndedRuns(live(["s1", "gone"]), live(), known("p1")),
    ).toEqual([]);
  });

  it("reports several projects when runs of each end together", () => {
    expect(
      projectsWithEndedRuns(
        live(["s1", "p1"], ["s2", "p2"], ["s3", "p3"]),
        live(["s3", "p3"]),
        known("p1", "p2", "p3"),
      ).sort(),
    ).toEqual(["p1", "p2"]);
  });
});
