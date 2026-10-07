import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  CRASH_COUNT_KEY,
  createCrashGuard,
  LOADING_MARKER_KEY,
} from "./crash-guard";

const memoryStorage = () => {
  const data = new Map<string, string>();
  return {
    data,
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => void data.set(key, value),
    removeItem: (key: string) => void data.delete(key),
  };
};

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("createCrashGuard", () => {
  it("preserves safe mode protection across the App Plugin naming migration", () => {
    const storage = memoryStorage();
    storage.setItem("valuz.thirdParty.loading", "1");
    storage.setItem("valuz.thirdParty.crashes", "1");
    expect(createCrashGuard(storage).evaluateStartup()).toEqual({ crashes: 2, safeMode: true });
    expect(storage.data.size).toBe(0);
  });
  it("a clean start is not a crash", () => {
    const storage = memoryStorage();
    const guard = createCrashGuard(storage);
    expect(guard.evaluateStartup()).toEqual({ crashes: 0, safeMode: false });
  });

  it("clears the marker ~10s after the plugins loaded", () => {
    const storage = memoryStorage();
    const guard = createCrashGuard(storage);
    guard.markLoading();
    guard.scheduleHealthy();
    expect(storage.data.has(LOADING_MARKER_KEY)).toBe(true);

    vi.advanceTimersByTime(9_000);
    expect(storage.data.has(LOADING_MARKER_KEY)).toBe(true);
    vi.advanceTimersByTime(1_500);
    expect(storage.data.has(LOADING_MARKER_KEY)).toBe(false);
    expect(createCrashGuard(storage).evaluateStartup().crashes).toBe(0);
  });

  it("goes to safe mode when the marker survived two starts in a row", () => {
    const storage = memoryStorage();

    // start 1: nothing left behind; load; the renderer dies before the timer
    const first = createCrashGuard(storage);
    expect(first.evaluateStartup().safeMode).toBe(false);
    first.markLoading();

    // start 2: the marker survived once
    const second = createCrashGuard(storage);
    expect(second.evaluateStartup()).toEqual({ crashes: 1, safeMode: false });
    expect(storage.data.get(CRASH_COUNT_KEY)).toBe("1");
    second.markLoading();

    // start 3: twice in a row
    const third = createCrashGuard(storage);
    expect(third.evaluateStartup()).toEqual({ crashes: 2, safeMode: true });
    expect(storage.data.size).toBe(0);
  });

  it("a healthy run in between resets the count", () => {
    const storage = memoryStorage();
    createCrashGuard(storage).markLoading();
    const second = createCrashGuard(storage);
    expect(second.evaluateStartup().crashes).toBe(1);
    second.markLoading();
    second.scheduleHealthy();
    vi.advanceTimersByTime(11_000);

    expect(createCrashGuard(storage).evaluateStartup()).toEqual({ crashes: 0, safeMode: false });
  });

  it("a normal window close (pagehide) is not a crash", () => {
    const storage = memoryStorage();
    const target = new EventTarget();
    const guard = createCrashGuard(storage, { target: target as unknown as Window });
    guard.markLoading();
    target.dispatchEvent(new Event("pagehide"));
    expect(storage.data.has(LOADING_MARKER_KEY)).toBe(false);
    guard.dispose();
  });

  it("works without storage", () => {
    const guard = createCrashGuard(null);
    expect(guard.evaluateStartup().safeMode).toBe(false);
    guard.markLoading();
    guard.markHealthy();
  });
});
