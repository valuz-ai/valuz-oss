import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { sessionsApi, setSessionsApiBase } from "./sessions-api";

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("sessionsApi.sendMessage", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    setSessionsApiBase("http://api.test");
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("should omit host_ref for a plain conversation send", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse({ id: "s1" }));

    await sessionsApi.sendMessage("s1", "hello", "prov-1", "model-1");

    const [url, init] = fetchMock.mock.calls[0] ?? [];
    expect(String(url)).toBe("http://api.test/v1/sessions/s1/messages");
    const body = JSON.parse(String(init?.body));
    expect(body).toEqual({
      prompt: "hello",
      provider_id: "prov-1",
      model_id: "model-1",
    });
  });

  it("should carry host_ref when the panel declares its host", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse({ id: "s1" }));

    await sessionsApi.sendMessage("s1", "hello", null, null, {
      host_type: "finance.research-desk",
      host_id: "desk:u1",
      slot: "main",
    });

    const [, init] = fetchMock.mock.calls[0] ?? [];
    const body = JSON.parse(String(init?.body));
    expect(body).toEqual({
      prompt: "hello",
      host_ref: {
        host_type: "finance.research-desk",
        host_id: "desk:u1",
        slot: "main",
      },
    });
  });
});
