/**
 * Tests for ``sessionsApi.listEventsWindow`` — the URL construction layer
 * for the turn-aligned pagination endpoint.
 *
 * The actual server-side slicing is covered in
 * ``backend/tests/adapters/test_event_sse_adapter_window.py``; here we
 * only assert that the client builds the right query string for each
 * call shape (initial load, with cursor, custom turn limit) and that
 * the response shape flows back unchanged.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { sessionsApi, type SessionEventWindowResponse } from "./sessions-api";

const okResponse = (body: SessionEventWindowResponse): Response =>
  new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });

describe("sessionsApi.listEventsWindow", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("should omit before_seq when the caller does not pass one (initial load)", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        okResponse({ session_id: "s1", items: [], has_more: false }),
      );

    await sessionsApi.listEventsWindow("s1");

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const url = String(fetchSpy.mock.calls[0][0]);
    expect(url).toContain("/v1/sessions/s1/events/window");
    expect(url).toContain("turn_limit=20");
    expect(url).not.toContain("before_seq");
  });

  it("should append before_seq to the query string when paginating upward", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        okResponse({ session_id: "s1", items: [], has_more: false }),
      );

    await sessionsApi.listEventsWindow("s1", { beforeSeq: 42 });

    const url = String(fetchSpy.mock.calls[0][0]);
    expect(url).toContain("before_seq=42");
    expect(url).toContain("turn_limit=20");
  });

  it("should accept a custom turnLimit overriding the default 20", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        okResponse({ session_id: "s1", items: [], has_more: false }),
      );

    await sessionsApi.listEventsWindow("s1", { turnLimit: 5 });

    const url = String(fetchSpy.mock.calls[0][0]);
    expect(url).toContain("turn_limit=5");
  });

  it("should percent-encode session ids that contain reserved characters", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        okResponse({ session_id: "s/1", items: [], has_more: false }),
      );

    await sessionsApi.listEventsWindow("s/1");

    const url = String(fetchSpy.mock.calls[0][0]);
    expect(url).toContain("/v1/sessions/s%2F1/events/window");
  });

  it("should pass the response body through unchanged when the call succeeds", async () => {
    const body: SessionEventWindowResponse = {
      session_id: "s1",
      items: [
        {
          seq: 100,
          event: { event_type: "message.user", payload: { text: "hi" } },
        },
        {
          seq: 101,
          event: {
            event_type: "message.assistant.delta",
            payload: { text: "hello" },
          },
        },
      ],
      has_more: true,
    };
    vi.spyOn(globalThis, "fetch").mockResolvedValue(okResponse(body));

    const result = await sessionsApi.listEventsWindow("s1", { beforeSeq: 200 });

    expect(result).toEqual(body);
  });

  it("should reject with a useful message when the server returns a non-2xx", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("session not found", { status: 404 }),
    );

    await expect(sessionsApi.listEventsWindow("missing")).rejects.toThrow(
      /API 404: session not found/,
    );
  });

  it("should treat beforeSeq of 0 as a real cursor (not omit it)", async () => {
    // Edge: a session whose first user_message lives at seq=0 should
    // still be paginatable. ``0`` is falsy in JS so a sloppy check
    // would drop it from the query string and the server would
    // re-return the latest page.
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        okResponse({ session_id: "s1", items: [], has_more: false }),
      );

    await sessionsApi.listEventsWindow("s1", { beforeSeq: 0 });

    const url = String(fetchSpy.mock.calls[0][0]);
    expect(url).toContain("before_seq=0");
  });
});
