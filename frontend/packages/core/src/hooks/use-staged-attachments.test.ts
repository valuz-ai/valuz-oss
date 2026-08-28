/** @vitest-environment jsdom */
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { SessionAttachmentItem } from "../api/sessions-api";

const listStagedAttachments = vi.fn();
const uploadAttachment = vi.fn();
const addKbAttachments = vi.fn();
const deleteAttachment = vi.fn();

vi.mock("../api/sessions-api", () => ({
  sessionsApi: {
    listStagedAttachments: (...a: unknown[]) => listStagedAttachments(...a),
    uploadAttachment: (...a: unknown[]) => uploadAttachment(...a),
    addKbAttachments: (...a: unknown[]) => addKbAttachments(...a),
    deleteAttachment: (...a: unknown[]) => deleteAttachment(...a),
  },
}));

import { useStagedAttachments } from "./use-staged-attachments";

const row = (
  over: Partial<SessionAttachmentItem> = {},
): SessionAttachmentItem => ({
  id: "srv1",
  session_id: null,
  filename: "f.png",
  stored_path: "attachments/srv1/f.png",
  parsed_path: null,
  parse_status: "ready",
  size_bytes: 1,
  mime_type: "image/png",
  created_at: 0,
  source_kind: "local",
  consumed_at: null,
  ...over,
});

const file = (name = "f.png") => new File(["x"], name, { type: "image/png" });

beforeEach(() => {
  vi.clearAllMocks();
  listStagedAttachments.mockResolvedValue({ items: [] });
});
afterEach(() => vi.useRealTimers());

describe("useStagedAttachments", () => {
  it("shows the file the moment it is attached, before the upload lands", async () => {
    let release: (v: SessionAttachmentItem) => void = () => {};
    uploadAttachment.mockReturnValue(
      new Promise<SessionAttachmentItem>((r) => {
        release = r;
      }),
    );
    const { result } = renderHook(() => useStagedAttachments());

    act(() => {
      void result.current.attachLocalFiles([file()]);
    });

    await waitFor(() => expect(result.current.attachments).toHaveLength(1));
    expect(result.current.hasPending).toBe(true);

    await act(async () => release(row()));
    expect(result.current.attachments).toHaveLength(1);
    expect(result.current.attachments[0].id).toBe("srv1");
  });

  it("takes the chip back down when the upload fails", async () => {
    uploadAttachment.mockRejectedValue(new Error("boom"));
    const { result } = renderHook(() => useStagedAttachments());

    await act(async () => {
      await result.current.attachLocalFiles([file()]);
    });

    expect(result.current.attachments).toHaveLength(0);
  });

  it("does not show files another composer staged", async () => {
    // Staging is owner-scoped on the server — an attachment has no session and
    // no composer, which is what lets it exist before either does. But a quick
    // chat and a project chat open at once would then each show the other's
    // files, which is what happened. A composer holds what IT attached, like
    // the text draft beside it.
    listStagedAttachments.mockResolvedValue({
      items: [row({ id: "someone-elses" })],
    });
    const { result } = renderHook(() => useStagedAttachments());

    // Give the mount a chance to do the wrong thing.
    await act(async () => {
      await Promise.resolve();
    });

    expect(result.current.attachments).toHaveLength(0);
  });

  it("a poll cannot pull in another composer's files either", async () => {
    uploadAttachment.mockResolvedValue(row({ parse_status: "parsing" }));
    listStagedAttachments.mockResolvedValue({
      items: [row({ parse_status: "ready" }), row({ id: "someone-elses" })],
    });
    vi.useFakeTimers();
    const { result } = renderHook(() => useStagedAttachments());
    await act(async () => {
      await result.current.attachLocalFiles([file()]);
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });

    expect(result.current.attachments.map((a) => a.id)).toEqual(["srv1"]);
  });

  it("uploads and reads on the backend it was given", async () => {
    // No session to route on, so the caller names the backend. Shipping the
    // parameter without passing it at the call sites sent every cloud-project
    // upload to the local default, where the turn could never find it.
    const base = "https://cloud.example/agent";
    uploadAttachment.mockResolvedValue(row({ parse_status: "parsing" }));
    listStagedAttachments.mockResolvedValue({ items: [row()] });
    vi.useFakeTimers();
    const { result } = renderHook(() => useStagedAttachments(base));

    await act(async () => {
      await result.current.attachLocalFiles([file()]);
    });
    expect(uploadAttachment).toHaveBeenCalledWith(expect.any(File), {
      baseUrl: base,
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1500);
    });
    expect(listStagedAttachments).toHaveBeenCalledWith({ baseUrl: base });
  });

  it("discards staged files on the backend they were uploaded to", async () => {
    // The switch case: a staged row only exists on the backend that took the
    // upload, so when the turn's backend changes the composer drops them —
    // deleting, not abandoning, and through the base it still holds. Carrying
    // them across instead is what lost a file silently: the turn named ids the
    // new backend had never seen, bound nothing, and said nothing.
    const base = "https://cloud.example/agent";
    uploadAttachment.mockResolvedValue(row());
    const { result } = renderHook(() => useStagedAttachments(base));
    await act(async () => {
      await result.current.attachLocalFiles([file()]);
    });
    expect(result.current.attachments).toHaveLength(1);

    await act(async () => {
      await result.current.discard();
    });

    expect(result.current.attachments).toHaveLength(0);
    expect(deleteAttachment).toHaveBeenCalledWith("srv1", { baseUrl: base });
  });

  it("an upload after a switch goes to the NEW backend", async () => {
    // The bug this pins, seen on qa: 本地服务 → attach → 云端服务 → attach.
    // The chip read 云端服务 and the registry held the right cloud URL, but
    // the POST still went to the local backend, because the base was captured
    // at render. The turn then named an id the cloud backend had never seen
    // and the file vanished from the message with nothing logged anywhere.
    let base: string | undefined = "http://local.example";
    uploadAttachment.mockResolvedValue(row());
    const { result, rerender } = renderHook(() =>
      useStagedAttachments(() => base),
    );

    await act(async () => {
      await result.current.attachLocalFiles([file()]);
    });
    expect(uploadAttachment).toHaveBeenLastCalledWith(expect.any(File), {
      baseUrl: "http://local.example",
    });

    base = "https://cloud.example/agent";
    rerender();
    await act(async () => {
      await result.current.attachLocalFiles([file("second.png")]);
    });
    expect(uploadAttachment).toHaveBeenLastCalledWith(expect.any(File), {
      baseUrl: "https://cloud.example/agent",
    });
  });

  it("resolves the base per call, not per render", async () => {
    // Same guarantee without a re-render: whatever the caller answers at the
    // moment of the upload is where the file goes.
    let base = "http://first.example";
    uploadAttachment.mockResolvedValue(row());
    const { result } = renderHook(() => useStagedAttachments(() => base));

    base = "http://second.example";
    await act(async () => {
      await result.current.attachLocalFiles([file()]);
    });

    expect(uploadAttachment).toHaveBeenLastCalledWith(expect.any(File), {
      baseUrl: "http://second.example",
    });
  });

  it("discarding nothing touches no backend", async () => {
    const { result } = renderHook(() => useStagedAttachments());
    await act(async () => {
      await result.current.discard();
    });
    expect(deleteAttachment).not.toHaveBeenCalled();
  });

  it("keeps polling until the parse settles", async () => {
    uploadAttachment.mockResolvedValue(row({ parse_status: "parsing" }));
    listStagedAttachments
      .mockResolvedValueOnce({ items: [] })
      .mockResolvedValue({ items: [row({ parse_status: "ready" })] });
    vi.useFakeTimers();
    const { result } = renderHook(() => useStagedAttachments());

    await act(async () => {
      await result.current.attachLocalFiles([file()]);
    });
    expect(result.current.hasPending).toBe(true);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });

    expect(result.current.hasPending).toBe(false);
  });

  it("un-attaches server-side when the file is removed mid-upload", async () => {
    let release: (v: SessionAttachmentItem) => void = () => {};
    uploadAttachment.mockReturnValue(
      new Promise<SessionAttachmentItem>((r) => {
        release = r;
      }),
    );
    deleteAttachment.mockResolvedValue(undefined);
    const { result } = renderHook(() => useStagedAttachments());

    await act(async () => {
      void result.current.attachLocalFiles([file()]);
    });
    await waitFor(() => expect(result.current.attachments).toHaveLength(1));
    const placeholderId = result.current.attachments[0].id;

    await act(async () => {
      await result.current.remove(placeholderId);
    });
    expect(deleteAttachment).not.toHaveBeenCalled(); // no server row yet

    await act(async () => release(row()));

    await waitFor(() =>
      expect(deleteAttachment).toHaveBeenCalledWith("srv1", undefined),
    );
    expect(result.current.attachments).toHaveLength(0);
  });

  // ── claim ───────────────────────────────────────────────────────────────

  it("hands the turn the files it is carrying", async () => {
    uploadAttachment.mockResolvedValue(row());
    const { result } = renderHook(() => useStagedAttachments());
    await act(async () => {
      await result.current.attachLocalFiles([file()]);
    });

    let claimed: SessionAttachmentItem[] = [];
    act(() => {
      claimed = result.current.claim();
    });

    expect(claimed.map((r) => r.id)).toEqual(["srv1"]);
    expect(result.current.attachments).toHaveLength(0);
  });

  it("does not hand the same file to two turns", async () => {
    uploadAttachment.mockResolvedValue(row());
    const { result } = renderHook(() => useStagedAttachments());
    await act(async () => {
      await result.current.attachLocalFiles([file()]);
    });

    act(() => void result.current.claim());
    let second: SessionAttachmentItem[] = [];
    act(() => {
      second = result.current.claim();
    });

    expect(second).toEqual([]);
  });

  it("leaves an in-flight upload staged for the next turn", async () => {
    // Claiming a placeholder would hand the turn an id the server has never
    // seen. Better staged than lost.
    uploadAttachment.mockReturnValue(
      new Promise<SessionAttachmentItem>(() => {}),
    );
    const { result } = renderHook(() => useStagedAttachments());
    act(() => {
      void result.current.attachLocalFiles([file()]);
    });
    await waitFor(() => expect(result.current.attachments).toHaveLength(1));

    let claimed: SessionAttachmentItem[] = [];
    act(() => {
      claimed = result.current.claim();
    });

    expect(claimed).toEqual([]);
    expect(result.current.attachments).toHaveLength(1);
  });

  it("gives the files back when the send they were claimed for fails", async () => {
    uploadAttachment.mockResolvedValue(row());
    const { result } = renderHook(() => useStagedAttachments());
    await act(async () => {
      await result.current.attachLocalFiles([file()]);
    });

    let claimed: SessionAttachmentItem[] = [];
    act(() => {
      claimed = result.current.claim();
    });
    act(() => result.current.restage(claimed));

    expect(result.current.attachments.map((a) => a.id)).toEqual(["srv1"]);
  });

  it("claims from the current set, not from the last render", async () => {
    // A send is several awaits long — create the session, navigate — and
    // ``claim`` runs in the middle of it. Deriving the answer from a render
    // value loses an upload that landed during those awaits.
    uploadAttachment.mockResolvedValue(row());
    const { result } = renderHook(() => useStagedAttachments());

    let claimed: SessionAttachmentItem[] = [];
    await act(async () => {
      await result.current.attachLocalFiles([file()]);
      // No re-render has been observed by this closure yet.
      claimed = result.current.claim();
    });

    expect(claimed.map((r) => r.id)).toEqual(["srv1"]);
  });

  it("is unaffected by a session being created mid-send", async () => {
    // THE bug this split exists for. The composer's send creates the session,
    // and while one hook served both the staging set and a session's history,
    // that promotion re-keyed this state and dropped every staged row — so the
    // turn went out claiming nothing and the file sat unbound on the server
    // with its parse finished.
    //
    // This hook takes no session id. There is no longer anything for a session
    // to change here, which is the whole point of the split; the test stands as
    // the statement of that invariant.
    uploadAttachment.mockResolvedValue(row());
    const { result, rerender } = renderHook(() => useStagedAttachments());
    await act(async () => {
      await result.current.attachLocalFiles([file()]);
    });

    rerender(); // whatever else the page re-rendered for

    let claimed: SessionAttachmentItem[] = [];
    act(() => {
      claimed = result.current.claim();
    });

    expect(claimed.map((r) => r.id)).toEqual(["srv1"]);
  });

  // ── in flight ───────────────────────────────────────────────────────────
  //
  // A file's life is staged → in-flight → bound. The middle state used to
  // exist only as a local variable inside the send, so the panel — which
  // renders staged plus bound — showed NEITHER for the whole length of the
  // send. On a cloud project that is a session-create plus a message POST, and
  // the file vanished from the panel for seconds while the message bubble
  // beside it already showed it.

  it("keeps a claimed file in flight until the bind is confirmed", async () => {
    uploadAttachment.mockResolvedValue(row());
    const { result } = renderHook(() => useStagedAttachments());
    await act(async () => {
      await result.current.attachLocalFiles([file()]);
    });

    act(() => void result.current.claim());

    // Out of the composer — it is on its way and must not be sent twice.
    expect(result.current.attachments).toHaveLength(0);
    // Still the conversation's, so the panel has something to render.
    expect(result.current.inFlight.map((a) => a.id)).toEqual(["srv1"]);
  });

  it("lets the row go once the conversation's own list has it", async () => {
    uploadAttachment.mockResolvedValue(row());
    const { result } = renderHook(() => useStagedAttachments());
    await act(async () => {
      await result.current.attachLocalFiles([file()]);
    });

    let claimed: SessionAttachmentItem[] = [];
    act(() => {
      claimed = result.current.claim();
    });
    act(() => result.current.settle(claimed));

    expect(result.current.inFlight).toHaveLength(0);
    expect(result.current.attachments).toHaveLength(0);
  });

  it("takes a row back out of flight when the send fails", async () => {
    // It never left, so it is a draft again — not something the panel should
    // still be describing as part of the conversation.
    uploadAttachment.mockResolvedValue(row());
    const { result } = renderHook(() => useStagedAttachments());
    await act(async () => {
      await result.current.attachLocalFiles([file()]);
    });

    let claimed: SessionAttachmentItem[] = [];
    act(() => {
      claimed = result.current.claim();
    });
    act(() => result.current.restage(claimed));

    expect(result.current.inFlight).toHaveLength(0);
    expect(result.current.attachments.map((a) => a.id)).toEqual(["srv1"]);
  });

  it("takes on files another page already sent", async () => {
    // The project composer posts and navigates without waiting, so the
    // arriving conversation holds files it did not stage and cannot yet read
    // back. They are spent — not restaged, which would offer them to a second
    // turn — so they land in flight and the panel can show them.
    const sent = [row({ id: "sent-elsewhere" })];
    const { result } = renderHook(() => useStagedAttachments());

    act(() => result.current.adopt(sent));

    expect(result.current.inFlight.map((a) => a.id)).toEqual([
      "sent-elsewhere",
    ]);
    expect(result.current.attachments).toHaveLength(0);
    expect(result.current.claim()).toEqual([]);
  });

  it("does not take the same file on twice", async () => {
    const sent = [row({ id: "sent-elsewhere" })];
    const { result } = renderHook(() => useStagedAttachments());

    act(() => result.current.adopt(sent));
    act(() => result.current.adopt(sent));

    expect(result.current.inFlight).toHaveLength(1);
  });

  it("adopts files handed over from another composer", async () => {
    // A composer holds only what it attached — which is what stops two of them
    // showing each other's files, and also what breaks the draft handoff
    // unless the transfer is explicit. The sending page claims and passes the
    // rows through the navigation; this page restages them and they become
    // ordinary staged files, claimable by its own send.
    //
    // Scoping without this made the receiving page claim nothing, so the turn
    // went out with no attachment at all.
    const handedOver = [row({ id: "from-elsewhere" })];
    const { result } = renderHook(() => useStagedAttachments());

    act(() => result.current.restage(handedOver));
    expect(result.current.attachments.map((a) => a.id)).toEqual([
      "from-elsewhere",
    ]);

    let claimed: SessionAttachmentItem[] = [];
    act(() => {
      claimed = result.current.claim();
    });
    expect(claimed.map((a) => a.id)).toEqual(["from-elsewhere"]);
  });

  it("a poll does not drop files that were handed over", async () => {
    // ``restage`` has to register them as this composer's, or the next poll
    // filters them straight back out.
    const handedOver = [row({ id: "from-elsewhere", parse_status: "parsing" })];
    listStagedAttachments.mockResolvedValue({
      items: [row({ id: "from-elsewhere", parse_status: "ready" })],
    });
    vi.useFakeTimers();
    const { result } = renderHook(() => useStagedAttachments());
    act(() => result.current.restage(handedOver));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });

    expect(result.current.attachments.map((a) => a.id)).toEqual([
      "from-elsewhere",
    ]);
  });
});
