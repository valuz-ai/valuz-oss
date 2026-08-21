/** @vitest-environment jsdom */

import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { forkMock, navigateMock, toastSuccess, toastError } = vi.hoisted(() => ({
  forkMock: vi.fn(),
  navigateMock: vi.fn(),
  toastSuccess: vi.fn(),
  toastError: vi.fn(),
}));

vi.mock("@valuz/core", async (loadOriginal) => {
  const actual = await loadOriginal<typeof import("@valuz/core")>();
  return {
    ...actual,
    sessionsApi: { ...actual.sessionsApi, fork: forkMock },
    useTranslation: () => ({ t: (key: string) => key }),
  };
});

vi.mock("react-router-dom", async (loadOriginal) => {
  const actual = await loadOriginal<typeof import("react-router-dom")>();
  return { ...actual, useNavigate: () => navigateMock };
});

vi.mock("sonner", () => ({
  toast: { success: toastSuccess, error: toastError },
}));

import { useForkSession } from "./use-fork-session";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (cause: unknown) => void;
  const promise = new Promise<T>((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
}

beforeEach(() => {
  forkMock.mockReset();
  navigateMock.mockReset();
  toastSuccess.mockReset();
  toastError.mockReset();
});

describe("useForkSession", () => {
  it("exposes pending ids while in flight and clears them on success", async () => {
    const gate = deferred<{ id: string }>();
    forkMock.mockReturnValue(gate.promise);
    const { result } = renderHook(() => useForkSession());

    expect(result.current.forkInFlight).toBe(false);
    act(() => {
      void result.current.fork("sess-1", "msg-9");
    });
    expect(result.current.forkInFlight).toBe(true);
    expect(result.current.forkingSessionId).toBe("sess-1");
    expect(result.current.forkingMessageId).toBe("msg-9");

    gate.resolve({ id: "forked-1" });
    await waitFor(() => expect(result.current.forkInFlight).toBe(false));
    expect(result.current.forkingSessionId).toBeNull();
    expect(navigateMock).toHaveBeenCalledWith("/conversation/forked-1");
    expect(toastSuccess).toHaveBeenCalledTimes(1);
  });

  it("ignores re-entry before the first request settles (double-click)", async () => {
    const gate = deferred<{ id: string }>();
    forkMock.mockReturnValue(gate.promise);
    const { result } = renderHook(() => useForkSession());

    // Two clicks in the same tick: state hasn't re-rendered between them,
    // so only the ref guard can stop the second one.
    act(() => {
      void result.current.fork("sess-1");
      void result.current.fork("sess-1");
    });
    expect(forkMock).toHaveBeenCalledTimes(1);

    gate.resolve({ id: "forked-1" });
    await waitFor(() => expect(result.current.forkInFlight).toBe(false));
    expect(forkMock).toHaveBeenCalledTimes(1);
    expect(navigateMock).toHaveBeenCalledTimes(1);
  });

  it("re-enables forking after a failure", async () => {
    forkMock.mockRejectedValueOnce(new Error("boom"));
    const { result } = renderHook(() => useForkSession());

    await act(async () => {
      await result.current.fork("sess-1");
    });
    expect(toastError).toHaveBeenCalledTimes(1);
    expect(result.current.forkInFlight).toBe(false);
    expect(navigateMock).not.toHaveBeenCalled();

    forkMock.mockResolvedValueOnce({ id: "forked-2" });
    await act(async () => {
      await result.current.fork("sess-1");
    });
    expect(forkMock).toHaveBeenCalledTimes(2);
    expect(navigateMock).toHaveBeenCalledWith("/conversation/forked-2");
  });

  it("nudges the sidebar's finished-runs window on success", async () => {
    forkMock.mockResolvedValueOnce({ id: "forked-3" });
    const onRefresh = vi.fn();
    window.addEventListener("valuz-runs-refresh", onRefresh);
    try {
      const { result } = renderHook(() => useForkSession());
      await act(async () => {
        await result.current.fork("sess-1");
      });
      expect(onRefresh).toHaveBeenCalledTimes(1);
    } finally {
      window.removeEventListener("valuz-runs-refresh", onRefresh);
    }
  });
});
