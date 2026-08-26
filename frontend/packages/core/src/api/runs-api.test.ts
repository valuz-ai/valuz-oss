import { afterEach, describe, expect, it, vi } from "vitest";

import { setExecutionTargets } from "../edition/execution-targets";
import { runsApi, setRunsApiBase } from "./runs-api";

afterEach(() => {
  setExecutionTargets([]);
  vi.unstubAllGlobals();
});

describe("runsApi", () => {
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
