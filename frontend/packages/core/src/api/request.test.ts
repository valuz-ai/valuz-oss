import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  buildApiUrl,
  clearRequestCacheForTests,
  invalidateRequestCache,
  requestJson,
} from "./request";

function jsonResponse(body: unknown, init?: ResponseInit): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
    ...init,
  });
}

function makeStorage(): Storage {
  const values = new Map<string, string>();
  return {
    get length() {
      return values.size;
    },
    clear: () => values.clear(),
    getItem: (key: string) => values.get(key) ?? null,
    key: (index: number) => [...values.keys()][index] ?? null,
    removeItem: (key: string) => values.delete(key),
    setItem: (key: string, value: string) => values.set(key, value),
  };
}

describe("request", () => {
  beforeEach(() => {
    vi.stubGlobal("localStorage", makeStorage());
    clearRequestCacheForTests();
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    clearRequestCacheForTests();
  });

  it("joins API base URLs and paths without double slashes", () => {
    expect(buildApiUrl("http://localhost:8000/", "/v1/example")).toBe(
      "http://localhost:8000/v1/example",
    );
    expect(buildApiUrl("http://localhost:8000", "v1/example")).toBe(
      "http://localhost:8000/v1/example",
    );
  });

  it("adds the stored locale as Accept-Language", async () => {
    localStorage.setItem("valuz-locale", "en-US");
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse({ ok: true }));

    await requestJson("/v1/example", { baseUrl: "http://api.test" });

    expect(fetchMock).toHaveBeenCalledOnce();
    const [, init] = fetchMock.mock.calls[0];
    expect(new Headers(init?.headers).get("Accept-Language")).toBe("en-US");
  });

  it("serializes JSON bodies and sets Content-Type", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse({ ok: true }));

    await requestJson("/v1/example", {
      baseUrl: "http://api.test",
      method: "POST",
      json: { name: "Ada" },
    });

    const [, init] = fetchMock.mock.calls[0];
    expect(new Headers(init?.headers).get("Content-Type")).toBe(
      "application/json",
    );
    expect(init?.body).toBe(JSON.stringify({ name: "Ada" }));
  });

  it("throws ApiError with structured i18n details", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(
        {
          detail: {
            message_key: "billing.insufficient_balance",
            message_params: { amount: 3 },
            message: "Insufficient balance",
          },
        },
        { status: 402 },
      ),
    );

    await expect(
      requestJson("/v1/example", { baseUrl: "http://api.test" }),
    ).rejects.toMatchObject({
      name: "ApiError",
      status: 402,
      message: "Insufficient balance",
      i18nKey: "billing.insufficient_balance",
      i18nParams: { amount: 3 },
    } satisfies Partial<ApiError>);
  });

  it("turns request timeouts into a user-facing error", async () => {
    vi.useFakeTimers();
    vi.spyOn(globalThis, "fetch").mockImplementation((_url, init) => {
      return new Promise((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => {
          reject(new DOMException("Aborted", "AbortError"));
        });
      });
    });

    const pending = requestJson("/v1/slow", {
      baseUrl: "http://api.test",
      timeoutMs: 50,
    });
    const assertion = expect(pending).rejects.toThrow("请求超时");
    await vi.advanceTimersByTimeAsync(50);

    await assertion;
  });

  it("does not cache GET requests unless cache options are supplied", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(() => Promise.resolve(jsonResponse({ value: 1 })));

    await requestJson("/v1/example", { baseUrl: "http://api.test" });
    await requestJson("/v1/example", { baseUrl: "http://api.test" });

    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("serves fresh explicit cache hits without refetching", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse({ value: 1 }));

    const first = await requestJson<{ value: number }>("/v1/example", {
      baseUrl: "http://api.test",
      cache: { ttlMs: 1_000, tags: ["example"] },
    });
    const second = await requestJson<{ value: number }>("/v1/example", {
      baseUrl: "http://api.test",
      cache: { ttlMs: 1_000, tags: ["example"] },
    });

    expect(first).toEqual({ value: 1 });
    expect(second).toEqual({ value: 1 });
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("deduplicates concurrent cacheable GET requests", async () => {
    let resolveFetch: ((response: Response) => void) | undefined;
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(
        () =>
          new Promise<Response>((resolve) => {
            resolveFetch = resolve;
          }),
      );

    const first = requestJson<{ value: number }>("/v1/example", {
      baseUrl: "http://api.test",
      cache: { ttlMs: 1_000, tags: ["example"] },
    });
    const second = requestJson<{ value: number }>("/v1/example", {
      baseUrl: "http://api.test",
      cache: { ttlMs: 1_000, tags: ["example"] },
    });

    resolveFetch?.(jsonResponse({ value: 1 }));

    await expect(Promise.all([first, second])).resolves.toEqual([
      { value: 1 },
      { value: 1 },
    ]);
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("invalidates cached responses by tag", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(jsonResponse({ value: 1 }))
      .mockResolvedValueOnce(jsonResponse({ value: 2 }));

    await requestJson("/v1/example", {
      baseUrl: "http://api.test",
      cache: { ttlMs: 1_000, tags: ["example"] },
    });
    invalidateRequestCache({ tags: ["example"] });
    const fresh = await requestJson<{ value: number }>("/v1/example", {
      baseUrl: "http://api.test",
      cache: { ttlMs: 1_000, tags: ["example"] },
    });

    expect(fresh).toEqual({ value: 2 });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
