import { afterEach, describe, expect, it, vi } from "vitest";

import { setExecutionTargets } from "../edition/execution-targets";
import {
  runsApi,
  setExtraRunsProvider,
  setRunsApiBase,
  type RunSummary,
} from "./runs-api";

afterEach(() => {
  setExecutionTargets([]);
  setExtraRunsProvider(null);
  vi.unstubAllGlobals();
});

function run(id: string, updatedAt: number): RunSummary {
  return {
    session_id: id,
    source_kind: "project_chat",
    origin: "user",
    project_id: "proj-1",
    project_name: "Shared",
    task_id: null,
    title: id,
    updated_at: updatedAt,
  } as RunSummary;
}

function jsonResponse(runs: RunSummary[]): Response {
  return new Response(JSON.stringify({ runs }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("runsApi", () => {
  it("merges provider runs into the single-backend result, by recency", async () => {
    setRunsApiBase("http://local.test");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse([run("own-old", 100), run("own-new", 300)])),
    );
    setExtraRunsProvider(async () => [run("shared", 200)]);

    const { runs } = await runsApi.list({ status: "finished" });

    // A narrow grant's conversations are the only rows its project ever has:
    // without this the sidebar accordion for a shared project stays empty.
    expect(runs.map((r) => r.session_id)).toEqual([
      "own-new",
      "shared",
      "own-old",
    ]);
  });

  it("passes the caller's scope to the provider", async () => {
    setRunsApiBase("http://local.test");
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse([])));
    const seen: unknown[] = [];
    setExtraRunsProvider(async (params) => {
      seen.push(params);
      return [];
    });

    await runsApi.list({ status: "finished", projectId: "proj-1", limit: 5 });

    expect(seen).toEqual([
      { status: "finished", projectId: "proj-1", limit: 5 },
    ]);
  });

  it("keeps the reachable row when both sides know a session", async () => {
    setRunsApiBase("http://local.test");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse([{ ...run("dup", 100), title: "real" }])),
    );
    setExtraRunsProvider(async () => [{ ...run("dup", 999), title: "stale" }]);

    const { runs } = await runsApi.list({ status: "finished" });

    expect(runs).toHaveLength(1);
    expect(runs[0].title).toBe("real");
  });

  it("a throwing provider cannot empty the list", async () => {
    setRunsApiBase("http://local.test");
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse([run("own", 1)])));
    setExtraRunsProvider(async () => {
      throw new Error("shared host is offline");
    });

    const { runs } = await runsApi.list({ status: "finished" });

    expect(runs.map((r) => r.session_id)).toEqual(["own"]);
  });

  it("coalesces concurrent requests for the same overview", async () => {
    setRunsApiBase("http://local.test");
    let resolveFetch!: (response: Response) => void;
    const fetchMock = vi.fn(
      () =>
        new Promise<Response>((resolve) => {
          resolveFetch = resolve;
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const first = runsApi.list({ status: "finished" });
    const duplicate = runsApi.list({ status: "finished" });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    resolveFetch(
      new Response(JSON.stringify({ runs: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await expect(first).resolves.toEqual({ runs: [] });
    await expect(duplicate).resolves.toEqual({ runs: [] });

    const afterCompletion = runsApi.list({ status: "finished" });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    resolveFetch(
      new Response(JSON.stringify({ runs: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    await expect(afterCompletion).resolves.toEqual({ runs: [] });
  });
});
