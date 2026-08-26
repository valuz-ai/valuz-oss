import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ControlFrame } from "../api/user-stream";

const listMock = vi.fn();
vi.mock("../api/runs-api", () => ({
  runsApi: { list: (...a: unknown[]) => listMock(...a) },
}));

let _streamCb: ((f: ControlFrame) => void) | null = null;
const unsubMock = vi.fn();
vi.mock("../api/user-stream", () => ({
  subscribeUserStream: (cb: (f: ControlFrame) => void) => {
    _streamCb = cb;
    return unsubMock;
  },
}));

import { useRunningRuns } from "./use-running-runs";

const frame = (eventType: string): ControlFrame => ({
  seq: 1,
  eventType,
  sessionId: "s1",
  payload: {},
  timestamp: 1,
});

beforeEach(() => {
  listMock.mockReset().mockResolvedValue({ runs: [] });
  unsubMock.mockReset();
  _streamCb = null;
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("useRunningRuns", () => {
  it("seeds from one REST snapshot and subscribes to the control stream", () => {
    renderHook(() => useRunningRuns());
    expect(listMock).toHaveBeenCalledTimes(1);
    expect(listMock).toHaveBeenCalledWith({ status: "running" });
    expect(_streamCb).toBeTypeOf("function");
  });

  it("refreshes (debounced) on a run lifecycle frame — no periodic polling", async () => {
    renderHook(() => useRunningRuns());
    // Let the cold-start snapshot poll settle so its in-flight guard clears
    // before we exercise the stream-driven refresh.
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    listMock.mockClear();

    // A burst of frames collapses into ONE refresh.
    act(() => {
      _streamCb?.(frame("run.started"));
      _streamCb?.(frame("run.status"));
      _streamCb?.(frame("run.finished"));
    });
    expect(listMock).not.toHaveBeenCalled(); // still within the debounce window
    await act(async () => {
      vi.advanceTimersByTime(300);
    });
    expect(listMock).toHaveBeenCalledTimes(1);

    // No timer-driven polls: idle time passes with zero further calls.
    listMock.mockClear();
    await act(async () => {
      vi.advanceTimersByTime(60_000);
    });
    expect(listMock).not.toHaveBeenCalled();
  });

  it("backstop-reconciles WHILE a run is running, then goes quiet when it drains", async () => {
    // A tick's ``_poll`` is async and guarded by ``_inFlight``; flush the
    // promise between advances so each cadence produces its own call.
    const settle = async (): Promise<void> => {
      await Promise.resolve();
      await Promise.resolve();
    };
    // A running run is present → the dot could be stuck if the stream misses
    // the clearing frame, so the backstop reconciles.
    listMock.mockResolvedValue({ runs: [{ session_id: "s1" }] });
    renderHook(() => useRunningRuns());
    await act(settle);
    listMock.mockClear();

    // One backstop cadence → one reconciliation poll (running + visible).
    await act(async () => {
      vi.advanceTimersByTime(15_000);
    });
    await act(settle);
    expect(listMock).toHaveBeenCalledTimes(1);
    expect(listMock).toHaveBeenCalledWith({ status: "running" });

    // The run drains — the next tick observes an empty set...
    listMock.mockResolvedValue({ runs: [] });
    await act(async () => {
      vi.advanceTimersByTime(15_000);
    });
    await act(settle);
    // ...and thereafter the backstop is quiet (idle client never polls).
    listMock.mockClear();
    await act(async () => {
      vi.advanceTimersByTime(60_000);
    });
    expect(listMock).not.toHaveBeenCalled();
  });

  it("pauses the backstop while the tab is hidden", async () => {
    listMock.mockResolvedValue({ runs: [{ session_id: "s1" }] });
    const hidden = vi
      .spyOn(document, "visibilityState", "get")
      .mockReturnValue("hidden");
    renderHook(() => useRunningRuns());
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    listMock.mockClear();

    await act(async () => {
      vi.advanceTimersByTime(60_000);
    });
    expect(listMock).not.toHaveBeenCalled(); // hidden → paused
    hidden.mockRestore();
  });

  it("closes the stream when the last consumer unmounts", () => {
    const a = renderHook(() => useRunningRuns());
    const b = renderHook(() => useRunningRuns());
    a.unmount();
    expect(unsubMock).not.toHaveBeenCalled();
    b.unmount();
    expect(unsubMock).toHaveBeenCalledTimes(1);
  });
});
