import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { MockInstance } from "vitest";

import { fetchEventSource } from "./fetch-event-source";
import * as request from "./request";

function sseResponse(body: string): Response {
  return new Response(body, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

function statusResponse(status: number): Response {
  // Response() forbids bodies on some statuses; an empty body is fine here.
  return new Response(null, { status });
}

describe("fetchEventSource reconnect backoff", () => {
  let raw: MockInstance<typeof request.requestRaw>;
  let close: (() => void) | null = null;

  beforeEach(() => {
    vi.useFakeTimers();
    raw = vi.spyOn(request, "requestRaw");
  });

  afterEach(() => {
    close?.();
    close = null;
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("should jump straight to the max delay on 401", async () => {
    raw.mockResolvedValue(statusResponse(401));
    close = fetchEventSource(
      () => "/v1/notifications/stream",
      () => {},
      {
        reconnectDelayMs: 1000,
        maxReconnectDelayMs: 30_000,
      },
    );
    await vi.advanceTimersByTimeAsync(0);
    expect(raw).toHaveBeenCalledTimes(1);

    // 1s 后不应重试(旧行为每秒一次);30s 才有下一次。
    await vi.advanceTimersByTimeAsync(29_000);
    expect(raw).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1_000);
    expect(raw).toHaveBeenCalledTimes(2);
  });

  it("should back off exponentially on repeated network failures", async () => {
    raw.mockRejectedValue(new Error("network down"));
    close = fetchEventSource(
      () => "/stream",
      () => {},
      {
        reconnectDelayMs: 1000,
        maxReconnectDelayMs: 8_000,
      },
    );
    await vi.advanceTimersByTimeAsync(0);
    expect(raw).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1_000); // +1s
    expect(raw).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(2_000); // +2s
    expect(raw).toHaveBeenCalledTimes(3);
    await vi.advanceTimersByTimeAsync(4_000); // +4s
    expect(raw).toHaveBeenCalledTimes(4);
    // 封顶 8s。
    await vi.advanceTimersByTimeAsync(8_000);
    expect(raw).toHaveBeenCalledTimes(5);
  });

  it("should reset the delay after a successful connection", async () => {
    raw
      .mockRejectedValueOnce(new Error("down"))
      .mockRejectedValueOnce(new Error("down"))
      .mockResolvedValueOnce(sseResponse("data: hi\n\n"))
      .mockRejectedValue(new Error("down again"));
    const frames: string[] = [];
    close = fetchEventSource(
      () => "/stream",
      (f) => frames.push(f.data),
      { reconnectDelayMs: 1000, maxReconnectDelayMs: 30_000 },
    );
    await vi.advanceTimersByTimeAsync(0); // fail #1 → next in 1s
    await vi.advanceTimersByTimeAsync(1_000); // fail #2 → next in 2s
    await vi.advanceTimersByTimeAsync(2_000); // success → delay reset
    expect(frames).toEqual(["hi"]);
    expect(raw).toHaveBeenCalledTimes(3);
    // 成功后的下一次断连应回到基础 1s 间隔。
    await vi.advanceTimersByTimeAsync(1_000);
    expect(raw).toHaveBeenCalledTimes(4);
  });

  it("should stop reconnecting after close", async () => {
    raw.mockResolvedValue(statusResponse(401));
    const stop = fetchEventSource(
      () => "/stream",
      () => {},
      {
        reconnectDelayMs: 1000,
        maxReconnectDelayMs: 5_000,
      },
    );
    await vi.advanceTimersByTimeAsync(0);
    stop();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(raw).toHaveBeenCalledTimes(1);
  });
});
