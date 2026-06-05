/** @vitest-environment jsdom */
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { runtimesApi } from "../api/runtimes-api";
import { __clearRuntimesCacheForTests, useRuntimes } from "./use-runtimes";

const sample = [
  {
    id: "claude_agent" as const,
    display_name: "Claude Agent",
    supported_protocols: ["anthropic" as const],
    requires_binary: null,
    available: true,
    unavailable_reason: null,
  },
];

describe("useRuntimes", () => {
  beforeEach(() => {
    __clearRuntimesCacheForTests();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("fetches and exposes runtimes from the registry endpoint", async () => {
    const spy = vi
      .spyOn(runtimesApi, "list")
      .mockResolvedValue({ runtimes: sample });

    const { result } = renderHook(() => useRuntimes());
    expect(result.current.loading).toBe(true);

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.runtimes).toEqual(sample);
    expect(result.current.error).toBeNull();
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("surfaces a fetch failure as an error string", async () => {
    vi.spyOn(runtimesApi, "list").mockRejectedValue(new Error("API 502: down"));

    const { result } = renderHook(() => useRuntimes());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe("API 502: down");
    expect(result.current.runtimes).toEqual([]);
  });

  it("refresh() refetches", async () => {
    const spy = vi
      .spyOn(runtimesApi, "list")
      .mockResolvedValue({ runtimes: sample });

    const { result } = renderHook(() => useRuntimes());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(spy).toHaveBeenCalledTimes(1);

    act(() => result.current.refresh());

    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));
  });
});
