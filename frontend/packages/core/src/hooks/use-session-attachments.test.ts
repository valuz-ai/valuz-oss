/** @vitest-environment jsdom */
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { SessionAttachmentItem } from "../api/sessions-api";

const listAttachments = vi.fn();
const deleteAttachment = vi.fn();

vi.mock("../api/sessions-api", () => ({
  sessionsApi: {
    listAttachments: (...a: unknown[]) => listAttachments(...a),
    deleteAttachment: (...a: unknown[]) => deleteAttachment(...a),
  },
}));

import { useSessionAttachments } from "./use-session-attachments";

const row = (
  over: Partial<SessionAttachmentItem> = {},
): SessionAttachmentItem => ({
  id: "a1",
  session_id: "s1",
  filename: "f.pdf",
  stored_path: "attachments/a1/f.pdf",
  parsed_path: null,
  parse_status: "ready",
  size_bytes: 1,
  mime_type: null,
  created_at: 0,
  source_kind: "local",
  consumed_at: null,
  ...over,
});

beforeEach(() => {
  vi.clearAllMocks();
  listAttachments.mockResolvedValue({ items: [] });
});
afterEach(() => vi.useRealTimers());

describe("useSessionAttachments", () => {
  it("reads the conversation's own attachments", async () => {
    listAttachments.mockResolvedValue({ items: [row()] });
    const { result } = renderHook(() => useSessionAttachments("s1"));

    await waitFor(() => expect(result.current.attachments).toHaveLength(1));
    expect(listAttachments).toHaveBeenCalledWith("s1");
  });

  it("asks nothing when there is no session", async () => {
    const { result } = renderHook(() => useSessionAttachments(null));

    await waitFor(() => expect(result.current.attachments).toHaveLength(0));
    expect(listAttachments).not.toHaveBeenCalled();
  });

  it("replaces the list across a session switch", async () => {
    // The panel shows ONE conversation. Carrying a previous session's files
    // into the next one would misattribute them.
    listAttachments
      .mockResolvedValueOnce({ items: [row({ id: "old" })] })
      .mockResolvedValue({ items: [row({ id: "new", session_id: "s2" })] });
    const { result, rerender } = renderHook(
      ({ sid }: { sid: string | null }) => useSessionAttachments(sid),
      { initialProps: { sid: "s1" as string | null } },
    );
    await waitFor(() => expect(result.current.attachments[0].id).toBe("old"));

    rerender({ sid: "s2" });

    await waitFor(() => expect(result.current.attachments[0].id).toBe("new"));
    expect(result.current.attachments).toHaveLength(1);
  });

  it("polls only while a bound attachment is still parsing", async () => {
    // A turn can be sent mid-parse, so a bound row may still settle — but an
    // ordinary conversation must not poll forever.
    listAttachments
      .mockResolvedValueOnce({ items: [row({ parse_status: "parsing" })] })
      .mockResolvedValue({ items: [row({ parse_status: "ready" })] });
    vi.useFakeTimers();
    const { result } = renderHook(() => useSessionAttachments("s1"));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.hasParsing).toBe(true);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(result.current.hasParsing).toBe(false);

    const settled = listAttachments.mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(listAttachments).toHaveBeenCalledTimes(settled);
  });

  it("drops a removed attachment without waiting for the server", async () => {
    listAttachments.mockResolvedValue({ items: [row()] });
    deleteAttachment.mockResolvedValue(undefined);
    const { result } = renderHook(() => useSessionAttachments("s1"));
    await waitFor(() => expect(result.current.attachments).toHaveLength(1));

    await act(async () => {
      await result.current.remove("a1");
    });

    expect(result.current.attachments).toHaveLength(0);
    expect(deleteAttachment).toHaveBeenCalledWith("a1");
  });

  it("waits for a file another page said it sent", async () => {
    // The project composer posts and navigates in that order, so this read
    // races a bind that has not happened. One mount-time read and nothing
    // else refreshing left the file missing from the panel until the person
    // switched away and back.
    listAttachments
      .mockResolvedValueOnce({ items: [] })
      .mockResolvedValue({ items: [row({ id: "sent-elsewhere" })] });
    vi.useFakeTimers();
    const { result } = renderHook(() =>
      useSessionAttachments("s1", ["sent-elsewhere"]),
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });

    expect(result.current.attachments.map((a) => a.id)).toEqual([
      "sent-elsewhere",
    ]);
  });

  it("stops waiting for a file that never arrives", async () => {
    // The promise can be broken: the page that sent the turn owns the POST,
    // and a failure there means the bind never happens. Polling a conversation
    // forever over it is worse than giving up.
    listAttachments.mockResolvedValue({ items: [] });
    vi.useFakeTimers();
    renderHook(() => useSessionAttachments("s1", ["never-lands"]));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });

    // The mount read plus a bounded run of polls — not one per second forever.
    expect(listAttachments.mock.calls.length).toBeLessThanOrEqual(22);
  });

  it("does not poll when everything expected is already here", async () => {
    listAttachments.mockResolvedValue({ items: [row({ id: "a1" })] });
    vi.useFakeTimers();
    renderHook(() => useSessionAttachments("s1", ["a1"]));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    const settled = listAttachments.mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });

    expect(listAttachments).toHaveBeenCalledTimes(settled);
  });

  it("refreshes the session the caller names, not the one it was built with", async () => {
    // A send creates the session and then binds its files. The closure that
    // runs afterwards was built while the id was still null, so a refresh that
    // read the hook's own prop would read the wrong session — or none — and the
    // panel would stay empty through the whole turn. That is what happened.
    const { result } = renderHook(() => useSessionAttachments(null));
    await waitFor(() => expect(result.current.attachments).toHaveLength(0));
    listAttachments.mockResolvedValue({
      items: [row({ session_id: "s-new" })],
    });

    await act(async () => {
      await result.current.refresh("s-new");
    });

    expect(listAttachments).toHaveBeenCalledWith("s-new");
    expect(result.current.attachments).toHaveLength(1);
  });
});
