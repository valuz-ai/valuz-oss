import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ConversationTurn } from "@valuz/shared";

const api = vi.hoisted(() => ({
  listFeedback: vi.fn(),
  recordFeedback: vi.fn(),
  withdrawFeedback: vi.fn(),
}));
const toast = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }));

vi.mock("@valuz/core", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@valuz/core")>();
  return {
    ...actual,
    sessionsApi: { ...actual.sessionsApi, ...api },
  };
});
vi.mock("sonner", () => ({ toast }));

import { ApiError } from "@valuz/core";
import { useSessionFeedback } from "./useSessionFeedback";

const turn = (messageId: string | null): ConversationTurn => ({
  id: `turn-${messageId ?? "x"}`,
  messageId,
  userMessageSeq: 1,
  userText: "q",
  blocks: [{ kind: "assistant", text: "a" }],
  failedMessage: null,
});

describe("useSessionFeedback", () => {
  beforeEach(() => {
    api.listFeedback.mockReset();
    api.recordFeedback.mockReset();
    api.withdrawFeedback.mockReset();
    toast.error.mockReset();
    api.listFeedback.mockResolvedValue({ items: [] });
    api.recordFeedback.mockResolvedValue({});
    api.withdrawFeedback.mockResolvedValue(undefined);
  });

  it("rehydrates ratings from the session's rows and ignores non-rating rows", async () => {
    api.listFeedback.mockResolvedValue({
      items: [
        { message_id: "m1", action: "rating", value: "down", block_ref: "" },
        { message_id: "m2", action: "copy", value: null, block_ref: "" },
        { message_id: "m3", action: "rating", value: "up", block_ref: "code:0" },
      ],
    });
    const { result } = renderHook(() => useSessionFeedback("s1"));
    await waitFor(() => expect(result.current.ratings).toEqual({ m1: "down" }));
    expect(api.listFeedback).toHaveBeenCalledWith("s1");
  });

  it("does not fetch for a draft session", () => {
    renderHook(() => useSessionFeedback("new"));
    renderHook(() => useSessionFeedback(null));
    expect(api.listFeedback).not.toHaveBeenCalled();
  });

  it("rates optimistically and rolls back on failure", async () => {
    const { result } = renderHook(() => useSessionFeedback("s1"));
    await waitFor(() => expect(api.listFeedback).toHaveBeenCalled());

    await act(() => result.current.rateTurn(turn("m1"), "down", "too_slow"));
    expect(result.current.ratings).toEqual({ m1: "down" });
    expect(api.recordFeedback).toHaveBeenCalledWith("s1", {
      message_id: "m1",
      action: "rating",
      value: "down",
      reason_code: "too_slow",
      source: "ui",
      surface: "chat",
    });

    api.recordFeedback.mockRejectedValueOnce(new Error("offline"));
    await act(() => result.current.rateTurn(turn("m1"), "up"));
    expect(result.current.ratings).toEqual({ m1: "down" });
    expect(toast.error).toHaveBeenCalledTimes(1);
  });

  it("withdraws with DELETE and treats an already-missing row as success", async () => {
    api.listFeedback.mockResolvedValue({
      items: [{ message_id: "m1", action: "rating", value: "up", block_ref: "" }],
    });
    const { result } = renderHook(() => useSessionFeedback("s1"));
    await waitFor(() => expect(result.current.ratings).toEqual({ m1: "up" }));

    api.withdrawFeedback.mockRejectedValueOnce(new ApiError("gone", 404));
    await act(() => result.current.rateTurn(turn("m1"), null));
    expect(result.current.ratings).toEqual({});
    expect(api.withdrawFeedback).toHaveBeenCalledWith("s1", {
      message_id: "m1",
      action: "rating",
    });
    expect(toast.error).not.toHaveBeenCalled();
  });

  it("skips turns without a Message and reports copies fire-and-forget", async () => {
    const { result } = renderHook(() => useSessionFeedback("s1"));
    await act(() => result.current.rateTurn(turn(null), "up"));
    result.current.reportCopy(turn(null));
    expect(api.recordFeedback).not.toHaveBeenCalled();

    api.recordFeedback.mockRejectedValueOnce(new Error("offline"));
    result.current.reportCopy(turn("m9"));
    expect(api.recordFeedback).toHaveBeenCalledWith("s1", {
      message_id: "m9",
      action: "copy",
      source: "ui",
      surface: "chat",
    });
    await Promise.resolve();
    expect(toast.error).not.toHaveBeenCalled();
  });
});
