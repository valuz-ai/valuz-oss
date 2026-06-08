/** @vitest-environment jsdom */
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { SessionAttachmentItem } from "../api/sessions-api";

const listAttachments = vi.fn();
const uploadAttachment = vi.fn();
const addKbAttachments = vi.fn();
const deleteAttachment = vi.fn();

vi.mock("../api/sessions-api", () => ({
  sessionsApi: {
    listAttachments: (...a: unknown[]) => listAttachments(...a),
    uploadAttachment: (...a: unknown[]) => uploadAttachment(...a),
    addKbAttachments: (...a: unknown[]) => addKbAttachments(...a),
    deleteAttachment: (...a: unknown[]) => deleteAttachment(...a),
  },
}));

import { useSessionAttachments } from "./use-session-attachments";

const row = (over: Partial<SessionAttachmentItem>): SessionAttachmentItem => ({
  id: "a1",
  session_id: "s1",
  filename: "f.pdf",
  stored_path: "/raw.pdf",
  parsed_path: null,
  parse_status: "parsing",
  size_bytes: 1,
  mime_type: null,
  created_at: 0,
  source_kind: "local",
  consumed_at: null,
  ...over,
});

describe("useSessionAttachments", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listAttachments.mockResolvedValue({ items: [] });
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("loads the session's attachments on mount", async () => {
    listAttachments.mockResolvedValueOnce({
      items: [row({ parse_status: "ready", parsed_path: "/p.md" })],
    });
    const { result } = renderHook(() => useSessionAttachments("s1"));
    await waitFor(() => expect(result.current.attachments).toHaveLength(1));
    expect(listAttachments).toHaveBeenCalledWith("s1");
    expect(result.current.hasParsing).toBe(false);
  });

  it("clears attachments when sessionId is null", async () => {
    const { result } = renderHook(() => useSessionAttachments(null));
    await waitFor(() => expect(result.current.attachments).toHaveLength(0));
    expect(listAttachments).not.toHaveBeenCalled();
  });

  it("polls a parsing row until it settles to ready (S1-03)", async () => {
    vi.useFakeTimers();
    // First call = initial load (parsing); subsequent calls = poll (ready).
    listAttachments
      .mockResolvedValueOnce({ items: [row({ parse_status: "parsing" })] })
      .mockResolvedValue({
        items: [row({ parse_status: "ready", parsed_path: "/p.md" })],
      });
    const { result } = renderHook(() => useSessionAttachments("s1"));
    // Flush the initial load microtask.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.hasParsing).toBe(true);
    // Advance one poll interval (1000ms) → poller re-fetches → ready.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(result.current.attachments[0].parse_status).toBe("ready");
    expect(result.current.hasParsing).toBe(false);
  });

  it("uploads local files on attach and appends the parsing row", async () => {
    uploadAttachment.mockResolvedValue(row({ id: "u1", parse_status: "parsing" }));
    const ensureSession = vi.fn().mockResolvedValue({ id: "s1" });
    const { result } = renderHook(() => useSessionAttachments("s1"));
    await waitFor(() => expect(listAttachments).toHaveBeenCalled());

    const file = new File(["hi"], "f.pdf", { type: "application/pdf" });
    await act(async () => {
      await result.current.attachLocalFiles([file], ensureSession);
    });
    expect(ensureSession).toHaveBeenCalledTimes(1);
    expect(uploadAttachment).toHaveBeenCalledWith("s1", file);
    expect(result.current.attachments.some((a) => a.id === "u1")).toBe(true);
    expect(result.current.hasParsing).toBe(true);
  });

  it("markPendingConsumed stamps consumed_at on pending rows only (X-05)", async () => {
    const { result } = renderHook(() => useSessionAttachments("s1"));
    await waitFor(() => expect(listAttachments).toHaveBeenCalled());
    act(() => {
      result.current.setAttachments([
        row({ id: "p1", consumed_at: null }),
        row({ id: "c1", consumed_at: 123 }),
      ]);
    });
    act(() => {
      result.current.markPendingConsumed();
    });
    const byId = Object.fromEntries(
      result.current.attachments.map((a) => [a.id, a.consumed_at]),
    );
    expect(byId.p1).toBeTruthy(); // freshly stamped
    expect(byId.c1).toBe(123); // already-consumed untouched
  });

  it("poll merge preserves an optimistic consumed_at (no chip flash-back)", async () => {
    vi.useFakeTimers();
    listAttachments
      .mockResolvedValueOnce({ items: [row({ id: "p1", parse_status: "parsing" })] })
      // Server still reports the row as pending (turn hasn't consumed it yet).
      .mockResolvedValue({
        items: [row({ id: "p1", parse_status: "ready", consumed_at: null })],
      });
    const { result } = renderHook(() => useSessionAttachments("s1"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    // Optimistically consume (as a send would).
    act(() => {
      result.current.markPendingConsumed();
    });
    expect(result.current.attachments[0].consumed_at).toBeTruthy();
    // A poll fires while the row is still server-side pending …
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    // … but the optimistic consumed_at survives the merge.
    expect(result.current.attachments[0].consumed_at).toBeTruthy();
    expect(result.current.attachments[0].parse_status).toBe("ready");
  });
});
