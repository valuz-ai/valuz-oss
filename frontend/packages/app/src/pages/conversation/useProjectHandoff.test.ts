/** @vitest-environment jsdom */
import { renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const navigate = vi.fn();
vi.mock("react-router-dom", () => ({
  useNavigate: () => navigate,
}));

import { NEW_SESSION_ID } from "./session-events";
import { useProjectHandoff } from "./useProjectHandoff";

/** Everything the hook needs that this test does not care about. */
const inert = () => ({
  id: "s1",
  searchParams: new URLSearchParams(),
  selectedProjectId: "p1",
  draft: null,
  attachmentsParsing: false,
  markPendingConsumed: vi.fn(),
  historyCursorRef: { current: 0 },
  projectSendHandoffRef: { current: null },
  handoffSessionIdRef: { current: null as string | null },
  draftBootstrapSettled: true,
  setTurnStartAnchor: vi.fn(),
  setSending: vi.fn(),
  setParsingConfirmOpen: vi.fn(),
  getDisplayBusy: () => false,
  performEnqueue: vi.fn(),
  performSend: vi.fn(),
});

const at = (state: unknown) => ({
  pathname: "/conversation/s1",
  search: "",
  hash: "",
  state,
  key: "k",
});

const render = (state: unknown, over: Record<string, unknown> = {}) => {
  const setPendingUserMessage = vi.fn();
  const restageAttachments = vi.fn();
  // Left in flight on purpose: that is the state at the instant these
  // assertions run, and settling it would land ``setProjectSendInFlight`` in a
  // microtask after the test body, outside ``act``.
  const performSend = vi.fn(() => new Promise<void>(() => {}));
  renderHook(() =>
    useProjectHandoff({
      ...inert(),
      location: at(state) as never,
      setPendingUserMessage,
      restageAttachments,
      performSend,
      ...over,
    } as never),
  );
  return { setPendingUserMessage, restageAttachments, performSend };
};

/**
 * Render a ``projectSend`` handoff — the project composer's draft path, which
 * lands on ``/conversation/new`` and lets THIS page mint and send.
 */
const renderProjectSend = (
  attachments?: unknown[],
  over: Record<string, unknown> = {},
) =>
  render(
    {
      projectSend: {
        text: "read this",
        sentAt: Date.now(),
        ...(attachments ? { attachments } : {}),
      },
    },
    // The draft handoff only ever runs on the new-session route.
    { id: NEW_SESSION_ID, ...over },
  );

const row = (id: string) => ({
  id,
  session_id: null,
  filename: "shot.png",
  stored_path: `attachments/${id}/shot.png`,
  parse_status: "ready",
  size_bytes: 1,
  mime_type: "image/png",
  created_at: 0,
  source_kind: "local",
  consumed_at: null,
});

describe("useProjectHandoff", () => {
  it("shows the files that shipped with the handed-over turn", () => {
    // The receiving page cannot work these out for itself: both it and the
    // sending page stamp the rows consumed, so its own pending view is empty
    // by the time the attachments load lands. Seeding an empty list is what
    // made an attached image show up only when the server echoed the turn
    // back — the text sat on screen alone for seconds first.
    const attachments = [{ name: "shot.png", size: 73101 }];
    const { setPendingUserMessage } = render({
      handoff: { text: "look at this", sentAt: Date.now(), attachments },
    });

    expect(setPendingUserMessage).toHaveBeenCalledWith(
      expect.objectContaining({ text: "look at this", attachments }),
    );
  });

  it("still shows the message when the turn carried no files", () => {
    const { setPendingUserMessage } = render({
      handoff: { text: "hello", sentAt: Date.now() },
    });

    expect(setPendingUserMessage).toHaveBeenCalledWith(
      expect.objectContaining({ text: "hello", attachments: [] }),
    );
  });

  it("ignores a handoff old enough to be a reload replay", () => {
    const { setPendingUserMessage } = render({
      handoff: {
        text: "old",
        sentAt: Date.now() - 60_000,
        attachments: [{ name: "a.png", size: 1 }],
      },
    });

    expect(setPendingUserMessage).not.toHaveBeenCalled();
  });

  // ── custody ─────────────────────────────────────────────────────────────
  //
  // The project composer's draft path does not send: it navigates here and
  // THIS page sends. A composer holds only what it attached — which is what
  // stops two of them showing each other's files — so this page cannot find
  // the handed-over files by looking. They have to be given to it, and the
  // giving is these two lines. Delete either and the turn goes out with no
  // attachment while the file sits unclaimed on the server: exactly the bug
  // where the agent answered that the workspace held nothing.

  it("takes ownership of the files the sending composer handed over", () => {
    const rows = [row("a1"), row("a2")];
    const { restageAttachments } = renderProjectSend(rows);

    expect(restageAttachments).toHaveBeenCalledWith(rows);
  });

  it("owns them BEFORE it sends, so its own claim can find them", () => {
    // Ordering is the whole mechanism: ``performSend`` claims from this page's
    // staging set, so restaging after it would hand the turn nothing and leave
    // the files staged for a turn that never comes.
    const rows = [row("a1")];
    const { restageAttachments, performSend } = renderProjectSend(rows);

    expect(restageAttachments.mock.invocationCallOrder[0]).toBeLessThan(
      performSend.mock.invocationCallOrder[0],
    );
  });

  it("sends a handoff that carried no files without restaging anything", () => {
    const { restageAttachments, performSend } = renderProjectSend();

    expect(restageAttachments).not.toHaveBeenCalled();
    expect(performSend).toHaveBeenCalled();
  });

  it("does not restage when the handoff is held back at a gate", () => {
    // A handoff that has not passed ``canSendProjectHandoff`` is re-run once
    // bootstrap settles. Restaging on the early pass would put the files into
    // a composer that is about to be handed them again.
    const { restageAttachments, performSend } = renderProjectSend([row("a1")], {
      draftBootstrapSettled: false,
    });

    expect(restageAttachments).not.toHaveBeenCalled();
    expect(performSend).not.toHaveBeenCalled();
  });
});
