/** @vitest-environment jsdom */
import { renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useKbTreePolling } from "./use-kb-tree-polling";

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("useKbTreePolling", () => {
  it("re-reads the tree while documents are still parsing", async () => {
    const refresh = vi.fn().mockResolvedValue(undefined);
    renderHook(() =>
      useKbTreePolling({ active: true, refresh, intervalMs: 100 }),
    );

    await vi.advanceTimersByTimeAsync(350);

    expect(refresh).toHaveBeenCalledTimes(3);
  });

  it("costs nothing once everything has settled", async () => {
    const refresh = vi.fn().mockResolvedValue(undefined);
    renderHook(() =>
      useKbTreePolling({ active: false, refresh, intervalMs: 100 }),
    );

    await vi.advanceTimersByTimeAsync(350);

    expect(refresh).not.toHaveBeenCalled();
  });

  it("stops the moment the last document settles", async () => {
    const refresh = vi.fn().mockResolvedValue(undefined);
    const { rerender } = renderHook(
      ({ active }) => useKbTreePolling({ active, refresh, intervalMs: 100 }),
      { initialProps: { active: true } },
    );

    await vi.advanceTimersByTimeAsync(150);
    const whileActive = refresh.mock.calls.length;
    rerender({ active: false });
    await vi.advanceTimersByTimeAsync(500);

    expect(refresh).toHaveBeenCalledTimes(whileActive);
  });

  it("stops when the user leaves the knowledge base", async () => {
    const refresh = vi.fn().mockResolvedValue(undefined);
    const { unmount } = renderHook(() =>
      useKbTreePolling({ active: true, refresh, intervalMs: 100 }),
    );

    await vi.advanceTimersByTimeAsync(150);
    const beforeUnmount = refresh.mock.calls.length;
    unmount();
    await vi.advanceTimersByTimeAsync(500);

    expect(refresh).toHaveBeenCalledTimes(beforeUnmount);
  });

  it("keeps polling after a failed read", async () => {
    // A blip must not end the poll: the badge would then stay stale forever
    // with nothing on screen saying why.
    const refresh = vi
      .fn()
      .mockRejectedValueOnce(new Error("network"))
      .mockResolvedValue(undefined);
    renderHook(() =>
      useKbTreePolling({ active: true, refresh, intervalMs: 100 }),
    );

    await vi.advanceTimersByTimeAsync(250);

    expect(refresh).toHaveBeenCalledTimes(2);
  });

  it("does not restart the interval when the caller rebuilds refresh", async () => {
    // ``refreshTree`` is a ``useCallback`` over live state, so its identity
    // changes whenever the tree does — which is *every poll*. If the effect
    // depended on it, each re-render would clear and recreate the interval and
    // the timer would never elapse: the list would stop updating exactly when
    // it is updating, which is the hardest kind of bug to see.
    let calls = 0;
    const make = () => vi.fn().mockImplementation(async () => void calls++);
    const { rerender } = renderHook(
      ({ fn }) => useKbTreePolling({ active: true, refresh: fn, intervalMs: 100 }),
      { initialProps: { fn: make() } },
    );

    // Re-render more often than the interval fires — the pathological case.
    for (let i = 0; i < 6; i++) {
      await vi.advanceTimersByTimeAsync(60);
      rerender({ fn: make() });
    }

    expect(calls).toBeGreaterThanOrEqual(3);
  });
});
