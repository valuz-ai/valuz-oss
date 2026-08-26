import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setApiBaseResolver } from "./base-resolver";
import { sessionsApi, setSessionsApiBase } from "./sessions-api";

const LOCAL = "http://local.test";

function forkedSession(): Response {
  return new Response(JSON.stringify({ id: "forked-1", name: "研究 (2)" }), {
    status: 201,
    headers: { "Content-Type": "application/json" },
  });
}

describe("sessionsApi.fork", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    setSessionsApiBase(LOCAL);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    setApiBaseResolver(null);
  });

  it("POSTs the anchor message for a message-granularity fork", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(forkedSession());

    const forked = await sessionsApi.fork("src-1", "m2");

    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      `${LOCAL}/v1/sessions/src-1/fork`,
    );
    const init = fetchMock.mock.calls[0]?.[1];
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({ message_id: "m2" });
    expect(forked.id).toBe("forked-1");
  });

  it("sends an empty body for a whole-session fork", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(forkedSession());

    await sessionsApi.fork("src-1");

    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual({});
  });
});
