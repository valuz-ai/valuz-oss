import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { pluginsApi, setPluginsApiBase } from "./plugins-api";

const BASE = "http://local.test";

/** Fresh ``Response`` per call — a body can only be read once. */
function json(body: unknown, status = 200): () => Promise<Response> {
  return async () =>
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    });
}

describe("pluginsApi", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    setPluginsApiBase(BASE);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("lists and reads plugins from /v1/plugins", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(json({ items: [] }));
    await pluginsApi.list();
    await pluginsApi.get("abc/def");
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(`${BASE}/v1/plugins`);
    expect(String(fetchMock.mock.calls[1]?.[0])).toBe(
      `${BASE}/v1/plugins/abc%2Fdef`,
    );
  });

  it("posts a JSON body for path / url / market_item_id installs", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(json({ plugin: {}, status: "installed" }));
    await pluginsApi.install({
      market_item_id: "market:plugin:x",
      on_conflict: "overwrite",
    });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(String(url)).toBe(`${BASE}/v1/plugins/install`);
    expect(init.method).toBe("POST");
    expect(new Headers(init.headers).get("Content-Type")).toBe(
      "application/json",
    );
    expect(JSON.parse(String(init.body))).toEqual({
      market_item_id: "market:plugin:x",
      on_conflict: "overwrite",
    });
  });

  it("posts multipart form data for zip installs and previews", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(
        json({
          manifest: {},
          members: [],
          conflicts: [],
          warnings: [],
          format: "agent_plugins",
        }),
      );
    const file = new File(["zip"], "plugin.zip", { type: "application/zip" });
    await pluginsApi.preview({ file, on_conflict: "skip" });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(String(url)).toBe(`${BASE}/v1/plugins/preview`);
    expect(init.body).toBeInstanceOf(FormData);
    const form = init.body as FormData;
    expect(form.get("file")).toBeInstanceOf(File);
    expect(form.get("on_conflict")).toBe("skip");
    expect(new Headers(init.headers).has("Content-Type")).toBe(false);
  });

  it("hits enable / disable / update / uninstall / export routes", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(json({}));
    await pluginsApi.enable("p1");
    await pluginsApi.disable("p1");
    await pluginsApi.update("p1", "skip");
    await pluginsApi.uninstall("p1");
    const calls = fetchMock.mock.calls.map(
      ([url, init]) => `${(init as RequestInit).method} ${String(url)}`,
    );
    expect(calls).toEqual([
      `POST ${BASE}/v1/plugins/p1/enable`,
      `POST ${BASE}/v1/plugins/p1/disable`,
      `POST ${BASE}/v1/plugins/p1/update`,
      `DELETE ${BASE}/v1/plugins/p1`,
    ]);
    expect(
      JSON.parse(String((fetchMock.mock.calls[2][1] as RequestInit).body)),
    ).toEqual({ on_conflict: "skip" });
    expect(pluginsApi.exportUrl("p1")).toBe(`${BASE}/v1/plugins/p1/export`);
  });

  it("batches memberships into one request and short-circuits on empty input", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(json({ a: [{ id: "p1", name: "kit" }], b: [] }));
    expect(await pluginsApi.memberships("skill", [])).toEqual({});
    expect(fetchMock).not.toHaveBeenCalled();
    const res = await pluginsApi.memberships("skill", ["a", "b", "a"]);
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      `${BASE}/v1/plugins/memberships?kind=skill&slugs=a%2Cb`,
    );
    expect(res.a).toEqual([{ id: "p1", name: "kit" }]);
  });
});
