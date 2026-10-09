import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  describeDshApiError,
  dshPluginsApi,
  isDshUnavailableError,
  setDshPluginsApiBase,
} from "./dsh-plugins-api";
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

/** The ``{ method, url, body }`` of the n-th fetch call. */
function call(fetchMock: { mock: { calls: unknown[][] } }, index = 0) {
  const [url, init] = fetchMock.mock.calls[index] as [string, RequestInit];
  return {
    url: String(url),
    method: init.method,
    body: init.body ? JSON.parse(String(init.body)) : undefined,
  };
}

async function failureOf(promise: Promise<unknown>): Promise<unknown> {
  try {
    await promise;
  } catch (error) {
    return error;
  }
  throw new Error("expected the call to reject");
}

describe("dshPluginsApi", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    setDshPluginsApiBase(BASE);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("reads the manager status without starting it", async () => {
    const status = {
      enabled: true,
      available: true,
      running: false,
      unavailable_reason: null,
      home: "/data/dsh-home",
      profile: "valuz",
      ui_url: null,
    };
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(json(status));
    await expect(dshPluginsApi.status()).resolves.toEqual(status);
    // A plain read: GET, never the manager/start POST.
    expect(call(fetchMock)).toMatchObject({
      url: `${BASE}/v1/dsh/plugins/status`,
      method: "GET",
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("starts and stops the manager host with POST", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(json({ running: true, ui_url: "http://x/?token=t" }));
    const started = await dshPluginsApi.startManager();
    await dshPluginsApi.stopManager();
    expect(started.ui_url).toBe("http://x/?token=t");
    expect(call(fetchMock, 0)).toMatchObject({
      url: `${BASE}/v1/dsh/plugins/manager/start`,
      method: "POST",
    });
    expect(call(fetchMock, 1)).toMatchObject({
      url: `${BASE}/v1/dsh/plugins/manager/stop`,
      method: "POST",
    });
  });

  it("unwraps { value } and sends dsh's parameter names under args", async () => {
    const bundles = [{ name: "valuz-dsh-bundle", enabled: true }];
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(json({ value: bundles }));

    await expect(dshPluginsApi.listBundles()).resolves.toEqual(bundles);
    await dshPluginsApi.listPlugins();
    await dshPluginsApi.inspect("@scope/pkg@^1", { registry: null });
    await dshPluginsApi.installBundle("/abs/path", { enabled: true });
    await dshPluginsApi.setBundleEnabled("@scope/pkg", false);
    await dshPluginsApi.setPluginEnabled("entry-1", true);
    await dshPluginsApi.removeBundle("@scope/pkg");
    await dshPluginsApi.cancelInstall("req-1");

    const calls = fetchMock.mock.calls.map((_, i) => call(fetchMock, i));
    expect(calls.map((c) => `${c.method} ${c.url}`)).toEqual(
      [
        "listBundles",
        "listPlugins",
        "inspect",
        "installBundle",
        "setBundleEnabled",
        "setPluginEnabled",
        "removeBundle",
        "cancelInstall",
      ].map((m) => `POST ${BASE}/v1/dsh/plugins/remote/${m}`),
    );
    expect(calls.map((c) => c.body)).toEqual([
      { args: {} },
      { args: {} },
      { args: { spec: "@scope/pkg@^1", options: { registry: null } } },
      { args: { spec: "/abs/path", options: { enabled: true } } },
      { args: { name: "@scope/pkg", enabled: false } },
      { args: { id: "entry-1", enabled: true } },
      { args: { name: "@scope/pkg" } },
      { args: { requestId: "req-1" } },
    ]);
  });

  it("omits an absent options object from the wire args", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(json({ value: { status: "refused" } }));
    await dshPluginsApi.inspect("left-pad");
    expect(call(fetchMock).body).toEqual({ args: { spec: "left-pad" } });
  });

  it("resolves a failed change instead of throwing (dsh answers HTTP 200)", async () => {
    const result = {
      changed: false,
      application: "failed",
      stage: "install",
      target: "x",
      error: { code: "operation-error", diagnostic: "pnpm exploded" },
      packageResult: { exitCode: 1, output: "ERR_PNPM", truncated: false },
    };
    vi.spyOn(globalThis, "fetch").mockImplementation(json({ value: result }));
    await expect(dshPluginsApi.installBundle("x")).resolves.toEqual(result);
  });
});

describe("dsh api errors", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    setDshPluginsApiBase(BASE);
  });

  it("surfaces 409 as an unavailable manager with the backend's reason", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      json(
        { detail: "dsh plugin management is disabled in this deployment" },
        409,
      ),
    );
    const error = await failureOf(dshPluginsApi.startManager());
    expect(error).toBeInstanceOf(ApiError);
    expect(isDshUnavailableError(error)).toBe(true);
    expect(describeDshApiError(error)).toEqual({
      status: 409,
      code: null,
      message: "dsh plugin management is disabled in this deployment",
    });
  });

  it("reads dsh's { code, error } out of a 400", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      json({ detail: { code: "rpc-failed", error: { message: "boom" } } }, 400),
    );
    const error = await failureOf(dshPluginsApi.listBundles());
    expect(isDshUnavailableError(error)).toBe(false);
    expect(describeDshApiError(error)).toEqual({
      status: 400,
      code: "rpc-failed",
      message: "boom",
    });
  });

  it("falls back to the code when a 400 carries no message", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      json(
        { detail: { code: "rpc-failed", error: { code: "rpc-failed" } } },
        400,
      ),
    );
    const error = await failureOf(dshPluginsApi.listBundles());
    expect(describeDshApiError(error).message).toBe("rpc-failed");
  });

  it("describes a non-HTTP failure without a status", () => {
    expect(describeDshApiError(new TypeError("Failed to fetch"))).toEqual({
      status: null,
      code: null,
      message: "Failed to fetch",
    });
    expect(describeDshApiError("nope").message).toBe("nope");
  });
});
