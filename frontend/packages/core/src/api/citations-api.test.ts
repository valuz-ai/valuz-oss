import { afterEach, describe, expect, it, vi } from "vitest";

import { citationsApi, setCitationsApiBase } from "./citations-api";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("citationsApi", () => {
  it("sends identities only to the canonical resolver", async () => {
    setCitationsApiBase("http://api.test");
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          document: null,
          effective_locator: null,
          status: "missing",
          fallback_reason: null,
          canonical_url: null,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await citationsApi.resolve({
      sessionId: "s1",
      messageId: "m1",
      citationId: "c1",
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://api.test/v1/citations/resolve");
    expect(JSON.parse(String(init.body))).toEqual({
      session_id: "s1",
      message_id: "m1",
      citation_id: "c1",
    });
  });
});
