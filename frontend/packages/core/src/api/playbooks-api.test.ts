import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fanOutTargets, useDegradedListTargets } from "../edition/list-fanout";
import { setExecutionTargets } from "../edition/execution-targets";
import { playbooksApi, setPlaybooksApiBase } from "./playbooks-api";

const LOCAL = {
  id: "local",
  labelKey: "local",
  baseUrl: "http://local.test",
  isDefault: true,
};
const CLOUD = {
  id: "cloud",
  labelKey: "cloud",
  baseUrl: "http://cloud.test",
};

beforeEach(async () => {
  setPlaybooksApiBase("http://api.test");
  setExecutionTargets([]);
  await fanOutTargets(() => Promise.resolve(null));
});

describe("playbooksApi lifecycle", () => {
  it("maps create responses to the shared detail shape and sends initial status", async () => {
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, init?: RequestInit) => {
        expect(JSON.parse(String(init?.body))).toMatchObject({
          name: "Review",
          status: "active",
        });
        return new Response(
          JSON.stringify({
            definition: {
              id: "pb-1",
              project_id: null,
              name: "Review",
              status: "active",
              origin: "user",
              source_definition_id: null,
              current_version: 1,
              revision: 1,
              created_at: 1,
              updated_at: 1,
            },
            version: {
              id: "pv-1",
              definition_id: "pb-1",
              version: 1,
              content: "Review evidence",
              reference_metadata: [],
              default_executor: {},
              created_by: "owner",
              produced_by_run: null,
              base_version: null,
              created_at: 1,
            },
          }),
          { status: 201, headers: { "Content-Type": "application/json" } },
        );
      },
    );
    vi.stubGlobal("fetch", fetchMock);

    const detail = await playbooksApi.create({
      name: "Review",
      content: "Review evidence",
      status: "active",
    });

    expect(detail.current_version.version).toBe(1);
    expect(detail.versions).toHaveLength(1);
  });

  it("lists immutable versions and deletes with optimistic revision", async () => {
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, init?: RequestInit) => {
        if (init?.method === "DELETE") {
          return new Response(null, { status: 204 });
        }
        return new Response("[]", {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      },
    );
    vi.stubGlobal("fetch", fetchMock);

    await playbooksApi.listVersions("pb-1");
    await playbooksApi.deleteDefinition("pb-1", 4);

    expect(String(fetchMock.mock.calls[0]?.[0])).toContain(
      "/v1/playbooks/pb-1/versions",
    );
    expect(String(fetchMock.mock.calls[1]?.[0])).toContain(
      "/v1/playbooks/pb-1?expected_revision=4",
    );
    expect(fetchMock.mock.calls[1]?.[1]).toMatchObject({ method: "DELETE" });
  });
});

afterEach(async () => {
  setExecutionTargets([]);
  await act(async () => {
    await fanOutTargets(() => Promise.resolve(null));
  });
  vi.unstubAllGlobals();
});

describe("playbooksApi.list", () => {
  it("treats a reachable legacy target without the Playbook route as empty", async () => {
    setExecutionTargets([LOCAL, CLOUD]);
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith(CLOUD.baseUrl)) {
        return new Response("not found", { status: 404 });
      }
      return new Response(
        JSON.stringify([
          {
            id: "pb-local",
            project_id: null,
            name: "Quarterly review",
          },
        ]),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    });
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useDegradedListTargets());

    let definitions: Awaited<ReturnType<typeof playbooksApi.list>> = [];
    await act(async () => {
      definitions = await playbooksApi.list();
    });

    expect(definitions).toMatchObject([
      { id: "pb-local", exec_origin: "local" },
    ]);
    expect(result.current).toEqual([]);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("still reports a target as degraded for real server failures", async () => {
    setExecutionTargets([LOCAL, CLOUD]);
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        return url.startsWith(CLOUD.baseUrl)
          ? new Response("unavailable", { status: 503 })
          : new Response("[]", {
              status: 200,
              headers: { "Content-Type": "application/json" },
            });
      }),
    );
    const { result } = renderHook(() => useDegradedListTargets());

    await act(async () => {
      await playbooksApi.list();
    });

    expect(result.current).toEqual(["cloud"]);
  });
});
