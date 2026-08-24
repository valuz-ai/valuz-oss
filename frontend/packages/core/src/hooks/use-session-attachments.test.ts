/** @vitest-environment jsdom */
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { SessionAttachmentItem } from "../api/sessions-api";

const listAttachments = vi.fn();
const uploadAttachment = vi.fn();
const addKbAttachments = vi.fn();
const deleteAttachment = vi.fn();
const listStagedAttachments = vi.fn();

vi.mock("../api/sessions-api", () => ({
  sessionsApi: {
    listAttachments: (...a: unknown[]) => listAttachments(...a),
    uploadAttachment: (...a: unknown[]) => uploadAttachment(...a),
    addKbAttachments: (...a: unknown[]) => addKbAttachments(...a),
    deleteAttachment: (...a: unknown[]) => deleteAttachment(...a),
    listStagedAttachments: (...a: unknown[]) => listStagedAttachments(...a),
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
    listStagedAttachments.mockResolvedValue({ items: [] });
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

  it("a late/empty load does not clobber an optimistic upload (eager-create race)", async () => {
    // The new-conversation flow sets sessionId to the freshly-minted session,
    // firing the load BEFORE the upload commits → the server returns empty. If
    // that resolves after the upload appended its row, the row must survive.
    let resolveLoad: (v: { items: never[] }) => void = () => {};
    const loadPromise = new Promise<{ items: never[] }>((r) => {
      resolveLoad = r;
    });
    listAttachments.mockReturnValueOnce(loadPromise); // the racing, empty load
    uploadAttachment.mockResolvedValue(
      row({ id: "u1", session_id: "s1", parse_status: "parsing" }),
    );

    const { result } = renderHook(() => useSessionAttachments("s1"));
    // Upload appends u1 while the load is still pending.
    await act(async () => {
      await result.current.attachLocalFiles([
        new File(["x"], "f.pdf", { type: "application/pdf" }),
      ]);
    });
    expect(result.current.attachments.some((a) => a.id === "u1")).toBe(true);

    // The stale empty load resolves last — it must NOT wipe the appended row.
    await act(async () => {
      resolveLoad({ items: [] });
      await loadPromise;
    });
    expect(result.current.attachments.some((a) => a.id === "u1")).toBe(true);
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
    uploadAttachment.mockResolvedValue(
      row({ id: "u1", parse_status: "parsing" }),
    );
    const { result } = renderHook(() => useSessionAttachments("s1"));
    await waitFor(() => expect(listAttachments).toHaveBeenCalled());

    const file = new File(["hi"], "f.pdf", { type: "application/pdf" });
    await act(async () => {
      await result.current.attachLocalFiles([file]);
    });
    expect(uploadAttachment).toHaveBeenCalledWith(file, undefined);
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
      .mockResolvedValueOnce({
        items: [row({ id: "p1", parse_status: "parsing" })],
      })
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

  it("handoff race: pending rows landing AFTER the consume are stamped by the watermark", async () => {
    // Project-send handoff: the page mounts on /conversation/new with an
    // empty attachments state; performSend promotes sessionId (null → s1),
    // firing the first load, and then calls markPendingConsumed — which can
    // run before that load resolves. The rows it never saw must still land
    // consumed, or the composer chips resurrect with nothing to clear them.
    let resolveLoad: (v: { items: SessionAttachmentItem[] }) => void = () => {};
    const loadPromise = new Promise<{ items: SessionAttachmentItem[] }>((r) => {
      resolveLoad = r;
    });
    listAttachments.mockReturnValueOnce(loadPromise);
    const { result, rerender } = renderHook(
      ({ sid }: { sid: string | null }) => useSessionAttachments(sid),
      { initialProps: { sid: null as string | null } },
    );
    rerender({ sid: "s1" }); // promote → load fires (still pending)
    act(() => {
      result.current.markPendingConsumed(); // send resolved first
    });
    await act(async () => {
      resolveLoad({
        items: [row({ id: "p1", parse_status: "ready", consumed_at: null })],
      });
      await loadPromise;
    });
    expect(result.current.attachments[0].consumed_at).toBeTruthy();
  });

  it("session re-entry load re-asserts the consume watermark over server-pending rows", async () => {
    // Same session, fresh load (s1 → null → s1): local stamps were reset with
    // the state, and the server may still report the rows pending until the
    // turn runs — the watermark must re-stamp them.
    listAttachments.mockResolvedValue({
      items: [row({ id: "p1", parse_status: "ready", consumed_at: null })],
    });
    const { result, rerender } = renderHook(
      ({ sid }: { sid: string | null }) => useSessionAttachments(sid),
      { initialProps: { sid: "s1" as string | null } },
    );
    await waitFor(() => expect(result.current.attachments).toHaveLength(1));
    act(() => {
      result.current.markPendingConsumed();
    });
    rerender({ sid: null });
    await waitFor(() => expect(result.current.attachments).toHaveLength(0));
    rerender({ sid: "s1" });
    await waitFor(() => expect(result.current.attachments).toHaveLength(1));
    expect(result.current.attachments[0].consumed_at).toBeTruthy();
  });

  it("explicit session/ts override records the watermark before the hook's sessionId settles (handoff)", async () => {
    // ProjectDetailPage POSTs the message itself and navigates; the landing
    // page consumes the handoff (calling markPendingConsumed with the route's
    // session id + sentAt) while its own selectedSessionId may still be null.
    let resolveLoad: (v: { items: SessionAttachmentItem[] }) => void = () => {};
    const loadPromise = new Promise<{ items: SessionAttachmentItem[] }>((r) => {
      resolveLoad = r;
    });
    listAttachments.mockReturnValueOnce(loadPromise);
    const { result, rerender } = renderHook(
      ({ sid }: { sid: string | null }) => useSessionAttachments(sid),
      { initialProps: { sid: null as string | null } },
    );
    act(() => {
      result.current.markPendingConsumed("s1", Date.now()); // hook sessionId still null
    });
    rerender({ sid: "s1" }); // settles later → load fires
    await act(async () => {
      resolveLoad({
        items: [row({ id: "p1", parse_status: "ready", consumed_at: null })],
      });
      await loadPromise;
    });
    expect(result.current.attachments[0].consumed_at).toBeTruthy();
  });

  it("a row attached AFTER the send stays pending (watermark is not a blanket consume)", async () => {
    let resolveLoad: (v: { items: SessionAttachmentItem[] }) => void = () => {};
    const loadPromise = new Promise<{ items: SessionAttachmentItem[] }>((r) => {
      resolveLoad = r;
    });
    listAttachments.mockReturnValueOnce(loadPromise);
    const { result, rerender } = renderHook(
      ({ sid }: { sid: string | null }) => useSessionAttachments(sid),
      { initialProps: { sid: null as string | null } },
    );
    rerender({ sid: "s1" });
    act(() => {
      result.current.markPendingConsumed();
    });
    await act(async () => {
      resolveLoad({
        items: [
          // Uploaded after the consume moment → belongs to the NEXT turn.
          row({
            id: "late1",
            parse_status: "ready",
            consumed_at: null,
            created_at: Date.now() + 60_000,
          }),
        ],
      });
      await loadPromise;
    });
    expect(result.current.attachments[0].consumed_at).toBeNull();
  });

  // ── attach-time feedback ────────────────────────────────────────────────
  //
  // Reported on qa: attach an image in a cloud conversation and nothing
  // happens. The upload writes to the owner's store, and the chip only
  // appeared after it landed.

  const file = (name = "f.png") => new File(["x"], name, { type: "image/png" });

  it("shows the file the moment it is attached, before any request", async () => {
    const { result } = renderHook(() => useSessionAttachments(null));
    let releaseUpload: (v: SessionAttachmentItem) => void = () => {};
    uploadAttachment.mockReturnValue(
      new Promise<SessionAttachmentItem>((r) => {
        releaseUpload = r;
      }),
    );

    act(() => {
      void result.current.attachLocalFiles([file()]);
    });

    // The upload has not resolved — the chip is local.
    await waitFor(() => expect(result.current.attachments).toHaveLength(1));
    expect(result.current.attachments[0].filename).toBe("f.png");
    expect(result.current.hasParsing).toBe(true);

    await act(async () => {
      releaseUpload(row({ id: "srv1", filename: "f.png", session_id: null }));
    });
  });

  it("replaces the placeholder with the server row rather than showing both", async () => {
    uploadAttachment.mockResolvedValue(
      row({ id: "srv1", filename: "f.png", session_id: null }),
    );
    const { result } = renderHook(() => useSessionAttachments(null));

    await act(async () => {
      await result.current.attachLocalFiles([file()]);
    });

    expect(result.current.attachments).toHaveLength(1);
    expect(result.current.attachments[0].id).toBe("srv1");
  });

  it("takes the chip back down when the upload fails", async () => {
    uploadAttachment.mockRejectedValue(new Error("boom"));
    const { result } = renderHook(() => useSessionAttachments(null));

    await act(async () => {
      await result.current.attachLocalFiles([file()]);
    });

    expect(result.current.attachments).toHaveLength(0);
  });

  it("the parse poll does not delete a chip whose upload is still in flight", async () => {
    // ``mergeServer`` used to return the server list verbatim. The poll starts
    // as soon as a session exists and fires every second, so it would erase
    // the placeholder one tick after it appeared — the exact bug the chip was
    // added to fix, reintroduced by the thing that watches it.
    listAttachments.mockResolvedValue({
      items: [row({ id: "other", parse_status: "parsing" })],
    });
    vi.useFakeTimers();
    const { result } = renderHook(() => useSessionAttachments("s1"));

    let releaseUpload: (v: SessionAttachmentItem) => void = () => {};
    uploadAttachment.mockReturnValue(
      new Promise<SessionAttachmentItem>((r) => {
        releaseUpload = r;
      }),
    );
    await act(async () => {
      void result.current.attachLocalFiles([file()]);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });

    expect(result.current.attachments.some((a) => a.filename === "f.png")).toBe(
      true,
    );

    await act(async () => {
      releaseUpload(row({ id: "srv1", filename: "f.png", session_id: null }));
    });
  });

  it("un-attaches server-side when the file is removed mid-upload", async () => {
    // The upload cannot be cancelled. If nothing undid it, a file the person
    // watched themselves remove would still ride along with the turn.
    let releaseUpload: (v: SessionAttachmentItem) => void = () => {};
    uploadAttachment.mockReturnValue(
      new Promise<SessionAttachmentItem>((r) => {
        releaseUpload = r;
      }),
    );
    deleteAttachment.mockResolvedValue(undefined);
    const { result } = renderHook(() => useSessionAttachments("s1"));

    await act(async () => {
      void result.current.attachLocalFiles([file()]);
    });
    await waitFor(() => expect(result.current.attachments).toHaveLength(1));
    const placeholderId = result.current.attachments[0].id;

    await act(async () => {
      await result.current.remove(placeholderId);
    });
    expect(deleteAttachment).not.toHaveBeenCalled(); // no server row yet

    await act(async () => {
      releaseUpload(row({ id: "srv1", filename: "f.png", session_id: null }));
    });

    await waitFor(() =>
      expect(deleteAttachment).toHaveBeenCalledWith("srv1", undefined),
    );
    expect(result.current.attachments).toHaveLength(0);
  });

  it("uploads and reads staged files on the backend it was given", async () => {
    // A staged upload has no session to route on, so the caller names the
    // backend. Shipping the parameter without wiring it at the call sites sent
    // every cloud-project upload to the module default — the desktop's LOCAL
    // backend — so the rows landed in a database the turn that was supposed to
    // claim them could never see. Everything looked fine: the chip appeared,
    // the upload returned 201, and the file simply was not there.
    const base = "https://cloud.example/agent";
    uploadAttachment.mockResolvedValue(
      row({ id: "srv1", session_id: null, parse_status: "ready" }),
    );
    const { result } = renderHook(() => useSessionAttachments(null, base));

    await waitFor(() =>
      expect(listStagedAttachments).toHaveBeenCalledWith({ baseUrl: base }),
    );
    await act(async () => {
      await result.current.attachLocalFiles([file()]);
    });

    expect(uploadAttachment).toHaveBeenCalledWith(expect.any(File), {
      baseUrl: base,
    });
  });
});
