import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { projectsApi, setExtraProjectsProvider } from "./projects-api";
import { invalidateRequestCache } from "./request";
import { setExecutionTargets } from "../edition/execution-targets";

const LOCAL = { id: "local", labelKey: "l", baseUrl: "", isDefault: true };
const DEVICE = { id: "device:d1", labelKey: "d", baseUrl: "https://relay/proxy" };

function answer(projects: string[]): typeof fetch {
  return vi.fn(async () =>
    new Response(
      JSON.stringify({ projects: projects.map((id) => ({ id, name: id })) }),
      { status: 200, headers: { "content-type": "application/json" } },
    ),
  ) as unknown as typeof fetch;
}

beforeEach(() => {
  setExecutionTargets([]);
  setExtraProjectsProvider(null);
  invalidateRequestCache({ tags: ["projects"] });
});

afterEach(() => {
  setExecutionTargets([]);
  setExtraProjectsProvider(null);
  vi.restoreAllMocks();
});

describe("projects an edition contributes", () => {
  it("appends them on a single-backend build", async () => {
    // A narrow grant opens ONE project on someone else's machine: no target
    // lists it, so without this seam it can never appear.
    vi.stubGlobal("fetch", answer(["mine"]));
    setExtraProjectsProvider(async () => [
      { id: "lent", name: "工作台", exec_origin: "device:d2" } as never,
    ]);

    const { projects } = await projectsApi.list();

    expect(projects.map((p) => p.id)).toEqual(["mine", "lent"]);
  });

  it("appends them alongside the fan-out too", async () => {
    setExecutionTargets([LOCAL, DEVICE]);
    vi.stubGlobal("fetch", answer(["mine"]));
    setExtraProjectsProvider(async () => [
      { id: "lent", name: "工作台", exec_origin: "device:d2" } as never,
    ]);

    const { projects } = await projectsApi.list();

    expect(projects.map((p) => p.id)).toContain("lent");
  });

  it("never lets a lender's hiccup empty the list", async () => {
    vi.stubGlobal("fetch", answer(["mine"]));
    setExtraProjectsProvider(async () => {
      throw new Error("that host went away");
    });

    const { projects } = await projectsApi.list();

    expect(projects.map((p) => p.id)).toEqual(["mine"]);
  });
});
