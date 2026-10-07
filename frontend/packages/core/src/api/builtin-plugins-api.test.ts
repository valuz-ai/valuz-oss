import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { builtinPluginsApi, setBuiltinPluginsApiBase } from "./builtin-plugins-api";
import { ApiError } from "./request";

const BASE = "http://local.test";

/** Fresh ``Response`` per call — a body can only be read once. */
function json(body: unknown, status = 200): () => Promise<Response> {
  return async () =>
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    });
}

function call(fetchMock: { mock: { calls: unknown[][] } }, index = 0) {
  const [url, init] = fetchMock.mock.calls[index] as [string, RequestInit];
  return {
    url: String(url),
    method: init.method,
    body: init.body ? JSON.parse(String(init.body)) : undefined,
  };
}

async function failureOf(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise;
  } catch (error) {
    return error as ApiError;
  }
  throw new Error("expected the call to reject");
}

describe("builtinPluginsApi", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    setBuiltinPluginsApiBase(BASE);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("lists backend extensions with a plain GET", async () => {
    const list = {
      composed: true,
      editable: true,
      plugins: [
        {
          id: "commercial-sites",
          status: "active",
          required: false,
          needs: ["auth"],
          provides: ["sites"],
          entitlement: "sites",
          error: null,
          hasConfig: false,
          desiredEnabled: true,
          boundPorts: [],
        },
      ],
      config_schemas: {},
    };
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(json(list));

    await expect(builtinPluginsApi.listBuiltinPlugins()).resolves.toEqual(list);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(call(fetchMock)).toMatchObject({
      url: `${BASE}/v1/builtin-plugins`,
      method: "GET",
    });
  });

  it("reads which backend plugins are inactive with a plain GET on /state", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(json({ inactive: ["oss-browser", "oss-backup"] }));

    await expect(builtinPluginsApi.backendState()).resolves.toEqual({
      inactive: ["oss-browser", "oss-backup"],
    });

    expect(call(fetchMock)).toMatchObject({
      url: `${BASE}/v1/builtin-plugins/state`,
      method: "GET",
    });
  });

  it("rejects the state read on an older backend (404) so callers can fall back", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      json({ detail: "Not Found" }, 404),
    );

    const error = await failureOf(builtinPluginsApi.backendState());

    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(404);
  });

  it("posts { enabled } to the plugin's enabled route, encoding the id", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(json({ application: "restart-required" }));

    await expect(
      builtinPluginsApi.setBuiltinPluginEnabled("a/b c", false),
    ).resolves.toEqual({ application: "restart-required" });
    await builtinPluginsApi.setBuiltinPluginEnabled("plain", true);

    expect(call(fetchMock, 0)).toEqual({
      url: `${BASE}/v1/builtin-plugins/a%2Fb%20c/enabled`,
      method: "POST",
      body: { enabled: false },
    });
    expect(call(fetchMock, 1)).toEqual({
      url: `${BASE}/v1/builtin-plugins/plain/enabled`,
      method: "POST",
      body: { enabled: true },
    });
  });

  it.each([
    [403, "extensions are managed by the operator here"],
    [404, "no backend plugin host in this deployment"],
    [409, "'core' is required"],
  ])(
    "surfaces a %i as an ApiError carrying the server detail",
    async (status, detail) => {
      vi.spyOn(globalThis, "fetch").mockImplementation(
        json({ detail }, status),
      );

      const error = await failureOf(
        builtinPluginsApi.setBuiltinPluginEnabled("core", false),
      );

      expect(error).toBeInstanceOf(ApiError);
      expect(error.status).toBe(status);
      expect(error.message).toBe(detail);
    },
  );
});
