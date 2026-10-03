import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { extensionsApi, setExtensionsApiBase } from "./extensions-api";
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

describe("extensionsApi", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    setExtensionsApiBase(BASE);
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

    await expect(extensionsApi.listBackendExtensions()).resolves.toEqual(list);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(call(fetchMock)).toMatchObject({
      url: `${BASE}/v1/extensions/backend`,
      method: "GET",
    });
  });

  it("posts { enabled } to the plugin's enabled route, encoding the id", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(json({ application: "restart-required" }));

    await expect(
      extensionsApi.setBackendExtensionEnabled("a/b c", false),
    ).resolves.toEqual({ application: "restart-required" });
    await extensionsApi.setBackendExtensionEnabled("plain", true);

    expect(call(fetchMock, 0)).toEqual({
      url: `${BASE}/v1/extensions/backend/a%2Fb%20c/enabled`,
      method: "POST",
      body: { enabled: false },
    });
    expect(call(fetchMock, 1)).toEqual({
      url: `${BASE}/v1/extensions/backend/plain/enabled`,
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
        extensionsApi.setBackendExtensionEnabled("core", false),
      );

      expect(error).toBeInstanceOf(ApiError);
      expect(error.status).toBe(status);
      expect(error.message).toBe(detail);
    },
  );
});
