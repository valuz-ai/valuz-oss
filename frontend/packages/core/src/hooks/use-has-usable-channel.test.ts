import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  USABLE_CHANNEL_RETRY_MS,
  useHasUsableChannel,
} from "./use-has-usable-channel";
import { providersApi } from "../api/providers-api";

vi.mock("../api/providers-api", () => ({
  providersApi: { list: vi.fn() },
}));
vi.mock("./use-composer-providers", () => ({
  providerHasUsableCredentials: (p: { usable?: boolean }) => Boolean(p.usable),
}));

const listMock = vi.mocked(providersApi.list);

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("useHasUsableChannel", () => {
  it("reports a usable channel once the fetch resolves", async () => {
    listMock.mockResolvedValue({
      providers: [{ enabled: true, usable: true }],
    } as never);
    const { result } = renderHook(() => useHasUsableChannel());
    expect(result.current.loaded).toBe(false);
    await act(async () => {});
    expect(result.current).toMatchObject({ hasChannel: true, loaded: true });
  });

  it("loads with no channel when the list is genuinely empty", async () => {
    listMock.mockResolvedValue({ providers: [] } as never);
    const { result } = renderHook(() => useHasUsableChannel());
    await act(async () => {});
    expect(result.current).toMatchObject({ hasChannel: false, loaded: true });
  });

  it("keeps the banner gated on fetch failure and retries until an answer", async () => {
    // A failed fetch is not knowledge — 'loaded' must stay false so the
    // "no model configured" banner can't fire off an error (e.g. a briefly
    // degraded backend), and the hook must retry to converge on the truth.
    vi.useFakeTimers();
    listMock
      .mockRejectedValueOnce(new Error("backend degraded"))
      .mockResolvedValue({
        providers: [{ enabled: true, usable: true }],
      } as never);
    const { result } = renderHook(() => useHasUsableChannel());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.loaded).toBe(false); // failure ≠ "no channel"
    await act(async () => {
      await vi.advanceTimersByTimeAsync(USABLE_CHANNEL_RETRY_MS + 1);
    });
    expect(result.current).toMatchObject({ hasChannel: true, loaded: true });
  });

  it("re-asks when refresh is called, so a managed install can retry a catalog that has not arrived", async () => {
    listMock
      .mockResolvedValueOnce({ providers: [] } as never)
      .mockResolvedValue({
        providers: [{ enabled: true, usable: true }],
      } as never);

    const { result } = renderHook(() => useHasUsableChannel());
    await waitFor(() =>
      expect(result.current).toMatchObject({
        hasChannel: false,
        loaded: true,
      }),
    );

    // Settled with nothing: the retry loop only covers failures, so without
    // refresh a managed install would sit on "no channel" until reload.
    await act(async () => {
      result.current.refresh();
    });

    await waitFor(() => expect(result.current.hasChannel).toBe(true));
    expect(listMock).toHaveBeenCalledTimes(2);
  });
});
